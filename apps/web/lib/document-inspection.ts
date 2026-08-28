import type { AnchorRegion, SourceAnchor, TableCellAddress } from "@/lib/evidence-presentation";
import { renderPolicy, sourceAnchors } from "@/lib/evidence-presentation";
import type { EvidenceDetail } from "@/lib/types";

/**
 * Reading a record as the document it came out of.
 *
 * `evidence-presentation` answers what may be shown and where a passage sits.
 * This module answers the next question a reader asks once both are settled: *what does
 * it actually say, in the shape the publisher wrote it*. That is a different job, and it
 * turns on one fact about this corpus that none of the location machinery encodes.
 *
 * The unit of evidence here is not a sentence. The XLSX extractor emits one record per
 * spreadsheet **row**, with `content_exact` serialised as `A146=…\nE146=…` and one anchor
 * per non-empty cell; the PDF extractor emits one record per **page**, with
 * `content_exact` the page's whole text and one anchor per text block. So a record's text
 * is a serialisation to be parsed back, not prose to be printed, and a record's anchors
 * are a set of places on one surface rather than a list of alternatives to choose between.
 * Rendering either one naively - the row as raw `A146=` text, the page as a single marked
 * block - shows the reader the pipeline instead of the document.
 */

/* ---------------------------------------------------------------- spreadsheet rows -- */

export interface SpreadsheetCell {
  /** Zero-based, as the corpus records it. */
  columnIndex: number;
  /** `E`, as the spreadsheet application labels it. */
  columnLetter: string;
  value: string;
  /** True when one of this record's own anchors addresses this cell. */
  anchored: boolean;
}

/**
 * One row of a source workbook, recovered from a record's serialised text.
 *
 * This is the whole evidence unit, not a fragment of one: the extractor writes a row at a
 * time, so every cell here was materialised, sealed and served together with the cited
 * one. Presenting the siblings is therefore not extra disclosure - it is the record.
 */
export interface SpreadsheetRow {
  tableId: string | null;
  /** Zero-based, as the corpus records it. */
  rowIndex: number;
  /** One-based, as the document numbers it. */
  rowNumber: number;
  cells: SpreadsheetCell[];
}

/** `A146=value`, which is exactly the shape `_xlsx` in the extractor writes. */
const CELL_LINE = /^([A-Z]+)(\d+)=([\s\S]*)$/;

function columnIndexOf(letters: string): number {
  let index = 0;
  for (const character of letters) {
    index = index * 26 + (character.charCodeAt(0) - 64);
  }
  return index - 1;
}

/**
 * Recover the source row a record serialises, or null when it does not serialise one.
 *
 * Null is returned generously and on purpose. Every caller falls back to printing the
 * text as it stands, so a wrong parse is far more costly than a missed one: it would
 * present invented cell addresses beside real values, in a table a clinician is reading
 * to check a dose. So the shape has to hold completely - table-cell anchors on the
 * record, every line accounted for, one row number throughout, and that row number
 * agreeing with the anchors - or nothing is claimed.
 *
 * A cell value may itself contain newlines, which is why continuation lines are appended
 * to the cell in hand rather than treated as a parse failure.
 */
export function parseSpreadsheetRow(detail: EvidenceDetail): SpreadsheetRow | null {
  if (detail.exact_text === null) return null;

  // The record must say it is a table row. Without this a PDF page whose text happens to
  // open with something like `A1=` would be reformatted into a table that does not exist.
  const cellLocators = detail.locators.filter(
    (locator) =>
      locator.kind === "TABLE_CELL" &&
      locator.row_index !== undefined &&
      locator.row_index !== null &&
      locator.column_index !== undefined &&
      locator.column_index !== null,
  );
  if (!cellLocators.length) return null;

  const anchoredRows = new Set(cellLocators.map((locator) => locator.row_index as number));
  if (anchoredRows.size !== 1) return null;
  const anchoredRowIndex = [...anchoredRows][0]!;
  const anchoredColumns = new Set(
    cellLocators.map((locator) => locator.column_index as number),
  );
  const tableIds = new Set(
    cellLocators.map((locator) => locator.table_id).filter((id): id is string => Boolean(id)),
  );

  const cells: Array<{ columnIndex: number; columnLetter: string; value: string }> = [];
  let rowNumber: number | null = null;

  for (const line of detail.exact_text.split("\n")) {
    const match = CELL_LINE.exec(line);
    if (!match) {
      const current = cells.at(-1);
      // A line before the first cell is not a continuation of anything, so the text is
      // not a serialised row at all.
      if (!current) return null;
      current.value = `${current.value}\n${line}`;
      continue;
    }
    const [, letters, digits, value] = match;
    const parsedRow = Number(digits);
    if (!Number.isSafeInteger(parsedRow) || parsedRow < 1) return null;
    if (rowNumber === null) rowNumber = parsedRow;
    // Cells from two different rows are not one row, whatever else they are.
    else if (rowNumber !== parsedRow) return null;
    cells.push({
      columnIndex: columnIndexOf(letters!),
      columnLetter: letters!,
      value: value!,
    });
  }

  if (rowNumber === null || !cells.length) return null;
  // The text and the anchors have to be describing the same row. When they disagree, one
  // of them is wrong and there is no way to tell which, so the values are shown as plain
  // text rather than laid out against addresses that may not be theirs.
  if (rowNumber !== anchoredRowIndex + 1) return null;

  return {
    tableId: tableIds.size === 1 ? [...tableIds][0]! : null,
    rowIndex: anchoredRowIndex,
    rowNumber,
    cells: cells.map((cell) => ({
      ...cell,
      value: cell.value.trim(),
      anchored: anchoredColumns.has(cell.columnIndex),
    })),
  };
}

/* ------------------------------------------------------------------ anchor groups -- */

/**
 * The anchors of one record, grouped by the surface they sit on.
 *
 * A page record carries one anchor per text block, so a PDF page routinely arrives with
 * thirty of them, every one labelled `PDF page 14` and every one part of the same
 * passage. Offering those as thirty interchangeable choices misdescribes the record
 * twice: it implies the reader must pick a location when the location is the page, and it
 * marks a paragraph when the evidence is everything on the sheet. Grouping restores what
 * the extractor meant - one surface, every recorded region on it.
 */
export interface AnchorGroup {
  key: string;
  form: "PAGE" | "TABLE_ROW" | "DOCUMENT";
  /** What the switcher shows: one place, not one coordinate. */
  label: string;
  /** The anchors on this surface, most precise first. */
  anchors: SourceAnchor[];
  /** The anchor whose coordinates stand for the group in captions and readouts. */
  primary: SourceAnchor;
  /**
   * Every region recorded on this surface, for drawing them all at once.
   *
   * Tagged with the position of the anchor it came from, not merely collected. That index
   * is what lets a paragraph of the passage and a rectangle on the page be the same
   * thing: the extractor writes one anchor per text block in reading order, so anchor 4
   * is paragraph 4, and a compacted list would have lost exactly that correspondence for
   * any record where one anchor happened to carry no box.
   */
  regions: Array<{ anchorIndex: number; region: AnchorRegion }>;
  /** Every cell addressed on this row, in column order. */
  cells: TableCellAddress[];
  pdfPage: number | null;
  printedPage: string | null;
}

function groupKey(anchor: SourceAnchor, index: number): string {
  const { placement } = anchor;
  if (placement.form === "PAGE") {
    const page = placement.pdfPage ?? placement.printedPage;
    // A page anchor with neither number names no page anyone can group it by, so it
    // stands alone rather than merging with every other unnumbered one.
    return page === null ? `page:unnumbered:${index}` : `page:${page}`;
  }
  if (placement.form === "TABLE_CELL") {
    return placement.cell === null
      ? `table:unaddressed:${index}`
      : `table:${placement.cell.tableId}:${placement.cell.rowIndex}`;
  }
  // Same kind and same URI is the same place; a section anchor and a whole-document
  // anchor are not, and stay apart.
  return `document:${anchor.kind}:${anchor.sourceUri}`;
}

function groupLabel(anchors: SourceAnchor[]): string {
  const primary = anchors[0]!;
  const { placement } = primary;

  if (placement.form === "TABLE_CELL") {
    if (placement.cell === null) return "Table cell, address not carried";
    const { tableId, rowNumber } = placement.cell;
    if (anchors.length === 1) {
      return `Table ${tableId}, cell ${placement.cell.columnLetter}${rowNumber}`;
    }
    return `Table ${tableId}, row ${rowNumber}`;
  }

  if (placement.form === "PAGE") {
    const pages: string[] = [];
    if (placement.printedPage !== null) pages.push(`Printed page ${placement.printedPage}`);
    if (placement.pdfPage !== null) pages.push(`PDF page ${placement.pdfPage}`);
    if (pages.length) {
      return pages.length === 2 ? `${pages[0]} (${pages[1]})` : pages[0]!;
    }
  }

  // Falls back to the anchor's own label, which already humanises the locator kind.
  return primary.label;
}

export function anchorGroups(detail: EvidenceDetail): AnchorGroup[] {
  const grouped = new Map<string, SourceAnchor[]>();
  // `sourceAnchors` has already ordered the anchors most-precise-first, so the first
  // member of every group is the best one to speak for it.
  sourceAnchors(detail).forEach((anchor, index) => {
    const key = groupKey(anchor, index);
    const existing = grouped.get(key);
    if (existing) existing.push(anchor);
    else grouped.set(key, [anchor]);
  });

  return [...grouped.entries()].map(([key, anchors]) => {
    const primary = anchors[0]!;
    const regions = anchors.flatMap((anchor, anchorIndex) =>
      anchor.placement.form === "PAGE" && anchor.placement.region !== null
        ? [{ anchorIndex, region: anchor.placement.region }]
        : [],
    );
    const cells = anchors
      .flatMap((anchor) =>
        anchor.placement.form === "TABLE_CELL" && anchor.placement.cell !== null
          ? [anchor.placement.cell]
          : [],
      )
      .sort((left, right) => left.columnIndex - right.columnIndex);

    return {
      key,
      form:
        primary.placement.form === "PAGE"
          ? "PAGE"
          : primary.placement.form === "TABLE_CELL"
            ? "TABLE_ROW"
            : "DOCUMENT",
      label: groupLabel(anchors),
      anchors,
      primary,
      regions,
      cells,
      pdfPage: primary.placement.form === "PAGE" ? primary.placement.pdfPage : null,
      printedPage: primary.placement.form === "PAGE" ? primary.placement.printedPage : null,
    } satisfies AnchorGroup;
  });
}

/**
 * One paragraph of a passage, and the region on the page it was read out of.
 *
 * A PDF evidence unit is a whole page: the extractor takes the page's text blocks in
 * reading order, joins them with a blank line, and records one anchor per block. Both
 * halves of that are recoverable, which is what lets the text and the picture point at
 * each other - click a paragraph and its rectangle lights up, click a rectangle and the
 * paragraph scrolls into view. Rendering the join verbatim instead threw the structure
 * away twice over: the blank lines collapse in HTML, so a page arrived as one unbroken
 * slab, and the correspondence to the boxes was never drawn at all.
 */
export interface PassageBlock {
  /** Position in reading order. */
  index: number;
  text: string;
  /**
   * The anchor this block was extracted from, or null when the two cannot be paired.
   *
   * Null is the whole safety story here. Pairing is by position and is claimed only when
   * the counts match exactly, because a mispaired block would draw a rectangle around
   * text that is not the text in it - a provenance view pointing confidently at the wrong
   * place, which is worse than one that points nowhere.
   */
  anchorIndex: number | null;
}

/** Exactly what `_pdf` in the extractor joins blocks with. */
const BLOCK_SEPARATOR = "\n\n";

/**
 * Split a passage back into the blocks it was assembled from.
 *
 * Returns null when there is nothing to split: no quotable text, or a workbook row, which
 * has its own structure and its own renderer. A record whose blocks cannot be paired with
 * anchors still comes back split - the paragraphs are real either way, and only the
 * linking is withheld.
 */
export function passageBlocks(detail: EvidenceDetail): PassageBlock[] | null {
  if (detail.exact_text === null) return null;
  if (parseSpreadsheetRow(detail) !== null) return null;

  const texts = detail.exact_text.split(BLOCK_SEPARATOR);
  const pageAnchors = anchorGroups(detail).find((group) => group.form === "PAGE")?.anchors ?? [];
  const paired = texts.length > 1 && texts.length === pageAnchors.length;

  return texts.map((text, index) => ({
    index,
    text,
    // A block whose anchor recorded no box still pairs: it is that block's anchor, and
    // the figure simply has nothing to draw for it.
    anchorIndex: paired ? index : null,
  }));
}

/* -------------------------------------------------------------- reading a passage -- */

/**
 * Words too common to be worth pointing at.
 *
 * Deliberately short and general. This list exists to stop `the` and `what` lighting up
 * every line of a page; it is not a clinical stoplist, and nothing clinical belongs in it
 * - a term this list drops is a term the reader is not shown, and the whole point of
 * marking terms is to show where the question and the passage actually meet.
 */
const STOPWORDS = new Set([
  "about", "after", "again", "all", "and", "any", "are", "been", "before", "being", "between",
  "both", "but", "can", "does", "doing", "done", "for", "from", "had", "has", "have", "her",
  "here", "him", "his", "how", "into", "its", "may", "might", "more", "most", "much", "must",
  "not", "now", "off", "once", "only", "other", "our", "out", "over", "own", "recommend",
  "recommended", "same", "she", "should", "some", "such", "than", "that", "the", "their",
  "them", "then", "there", "these", "they", "this", "those", "through", "under", "until",
  "use", "used", "very", "was", "what", "when", "where", "which", "while", "who", "why",
  "will", "with", "would", "you", "your",
]);

/** Beyond this the marking stops being a signal and becomes a highlighter over the page. */
const MAXIMUM_TERMS = 40;
const MINIMUM_TERM_LENGTH = 3;

/**
 * The inflections a term is allowed to differ by, at both ends.
 *
 * A question asks about `monitored` and the guideline says `monitoring`; a claim says
 * `guidelines` and the page says `guideline`. Refusing those is not conservatism, it is a
 * feature that silently fails on the ordinary case. This is the smallest rule that covers
 * it - one regular English suffix, stripped from the term and re-admitted in the pattern -
 * and not a stemmer: nothing here rewrites a word's root, so `dose` never reaches `dosage`
 * and no term is ever marked on a word that does not begin with it.
 */
const INFLECTION = /(?:ing|ed|es|s)$/;
/** Below this a stripped stem is short enough to match words it has nothing to do with. */
const MINIMUM_STEM_LENGTH = 4;

function stem(token: string): string {
  const stripped = token.replace(INFLECTION, "");
  return stripped.length >= MINIMUM_STEM_LENGTH ? stripped : token;
}

/**
 * The words worth pointing at in a passage, taken from the question and the claim.
 *
 * A page record is a page of text, and the reader's actual task is finding the two lines
 * in it that bear on what was asked. The terms come from the question and from the claim
 * the passage was cited for, because those are the two things the reader is checking the
 * passage against - and neither is a search the reader had to type.
 *
 * Terms come back as stems, so `monitored` and `monitoring` are one term rather than two
 * that each miss the other.
 */
export function questionTerms(...sources: Array<string | null | undefined>): string[] {
  const terms = new Set<string>();
  for (const source of sources) {
    if (!source) continue;
    for (const raw of source.toLowerCase().split(/[^\p{L}\p{N}-]+/u)) {
      const token = raw.replace(/^-+|-+$/g, "");
      if (token.length < MINIMUM_TERM_LENGTH || STOPWORDS.has(token)) continue;
      terms.add(stem(token));
      if (terms.size >= MAXIMUM_TERMS) return [...terms];
    }
  }
  return [...terms];
}

export interface PassageSegment {
  text: string;
  /**
   * `term` is a word from the question or claim; `find` is a hit for what the reader
   * typed into the passage search. They are different claims about a piece of text - one
   * is the system pointing, the other is the reader looking - and are never merged.
   */
  kind: "plain" | "term" | "find";
  /** Position among the find matches, so one of them can be the current one. */
  matchIndex: number | null;
}

function escapeForRegExp(value: string): string {
  return value.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The reader's search has to be long enough to mean something. */
const MINIMUM_FIND_LENGTH = 2;

/**
 * Split a passage into what to mark and what to leave alone.
 *
 * Find matches take precedence over question terms wherever the two overlap: the reader
 * looking for something specific has said what they care about, and that outranks what
 * the system inferred they might. Segments are produced in reading order and concatenate
 * back to the original text exactly, so nothing can be dropped or duplicated on screen.
 */
export function passageSegments(
  text: string,
  { terms = [], find = "" }: { terms?: string[]; find?: string } = {},
): PassageSegment[] {
  const trimmedFind = find.trim();
  const findPattern =
    trimmedFind.length >= MINIMUM_FIND_LENGTH
      ? new RegExp(escapeForRegExp(trimmedFind), "giu")
      : null;
  const usableTerms = terms.filter((term) => term.length >= MINIMUM_TERM_LENGTH);
  const termPattern = usableTerms.length
    ? new RegExp(
        // Longest first: `viral` and `viral-load` both reaching one position must resolve
        // to the longer, or the mark stops halfway through the word it is pointing at.
        `(?<![\\p{L}\\p{N}])(?:${[...usableTerms]
          .sort((left, right) => right.length - left.length)
          .map((term) => `${escapeForRegExp(term)}(?:ing|ed|es|s)?`)
          .join("|")})(?![\\p{L}\\p{N}])`,
        "giu",
      )
    : null;

  if (!findPattern && !termPattern) {
    return text ? [{ text, kind: "plain", matchIndex: null }] : [];
  }

  const segments: PassageSegment[] = [];
  const push = (value: string, kind: PassageSegment["kind"], matchIndex: number | null) => {
    if (!value) return;
    const previous = segments.at(-1);
    // Adjacent runs of the same kind are merged so the DOM does not gain an element per
    // matched word where one span would do.
    if (previous && previous.kind === kind && kind === "plain") {
      previous.text += value;
      return;
    }
    segments.push({ text: value, kind, matchIndex });
  };

  const markTerms = (value: string) => {
    if (!termPattern) return push(value, "plain", null);
    termPattern.lastIndex = 0;
    let cursor = 0;
    for (let hit = termPattern.exec(value); hit !== null; hit = termPattern.exec(value)) {
      push(value.slice(cursor, hit.index), "plain", null);
      push(hit[0], "term", null);
      cursor = hit.index + hit[0].length;
      // A zero-length match cannot happen with the patterns built above, but an empty
      // alternative would otherwise spin here forever.
      if (hit[0].length === 0) termPattern.lastIndex += 1;
    }
    push(value.slice(cursor), "plain", null);
  };

  if (!findPattern) {
    markTerms(text);
    return segments;
  }

  findPattern.lastIndex = 0;
  let cursor = 0;
  let matchIndex = 0;
  for (let hit = findPattern.exec(text); hit !== null; hit = findPattern.exec(text)) {
    markTerms(text.slice(cursor, hit.index));
    push(hit[0], "find", matchIndex);
    matchIndex += 1;
    cursor = hit.index + hit[0].length;
  }
  markTerms(text.slice(cursor));
  return segments;
}

/** How many find matches a set of segments holds. */
export function findMatchCount(segments: PassageSegment[]): number {
  return segments.filter((segment) => segment.kind === "find").length;
}

/**
 * The text a passage search actually runs over.
 *
 * For a workbook record this is the cell values without their `A146=` addresses, because
 * that is what is on the screen: counting matches over the serialisation would report
 * hits in text the reader is not being shown, and a `146` typed into the search would
 * light up every cell of the row. The newline join is safe on both sides - a reader
 * cannot type one into a single-line search, and term matching treats it as a boundary.
 */
export function passageSearchText(detail: EvidenceDetail): string {
  const row = parseSpreadsheetRow(detail);
  if (row !== null) return row.cells.map((cell) => cell.value).join("\n");
  return detail.exact_text ?? "";
}

/* ------------------------------------------------------------- taking a quote away -- */

/**
 * The passage with everything needed to cite it, or null when it may not be quoted.
 *
 * Null rather than a partial block: a reader who copies this is going to paste it
 * somewhere this interface cannot annotate, so text that leaves here without its edition,
 * its location and its research-use notice is text that will be read without them. The
 * version identifier is included alongside the label because two editions can both be
 * captioned `2021`.
 */
export function citationText(detail: EvidenceDetail, locationLabel: string): string | null {
  const policy = renderPolicy(detail);
  if (!policy.canQuote || detail.exact_text === null) return null;

  return [
    `"${detail.exact_text}"`,
    "",
    `${detail.publisher_name}. ${detail.source_title}.`,
    `${detail.source_version_label} (${detail.source_version_id}). ${locationLabel}.`,
    detail.source_url,
    `Evidence ID: ${detail.evidence_id}. Research use only.`,
  ].join("\n");
}
