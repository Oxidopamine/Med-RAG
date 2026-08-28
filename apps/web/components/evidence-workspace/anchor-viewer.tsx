"use client";

import {
  ChevronLeft,
  ChevronRight,
  Crosshair,
  Expand,
  FileLock2,
  FileText,
  Grid3x3,
} from "lucide-react";
import { Fragment, useState } from "react";

import type { AnchorGroup, SpreadsheetRow } from "@/lib/document-inspection";
import { anchorGroups, parseSpreadsheetRow } from "@/lib/document-inspection";
import type {
  SourceVersionIdentity,
  TableCellAddress,
} from "@/lib/evidence-presentation";
import {
  anchorRegionStatus,
  columnLetter,
  humanizeCode,
  renderPolicy,
} from "@/lib/evidence-presentation";
import type { EvidenceDetail } from "@/lib/types";

import { PageViewer } from "./page-viewer";
import { PageSurface, useSourcePage } from "./source-page";
import styles from "./workspace.module.css";

/**
 * An anchor drawn as a place in a document.
 *
 * Everything upstream of this component - the extractor's bounding boxes, the corpus
 * `SourceAnchor`, the serving locator - exists so a reader can be shown *where* a passage
 * came from. This is where that chain terminates in a picture.
 *
 * Three properties hold on every path through it.
 *
 * The source content is never required. Every WHO asset in the active trust root
 * withholds page reproduction while the licence review is open, so the view that ships
 * for those is a page frame with the regions marked on it and no publisher text inside.
 * That is not a placeholder for a better view: a marked region over an empty frame is the
 * honest rendering of "we know exactly where this is and may not show it to you".
 *
 * The whole record is located, not a sample of it. A PDF evidence unit is an entire page
 * and carries one anchor per text block; a workbook unit is an entire row and carries one
 * anchor per populated cell. So anchors are grouped by the surface they sit on and every
 * region on that surface is drawn at once. Offering thirty identically-labelled anchors
 * and marking whichever one was clicked described the pipeline rather than the document,
 * and understated the passage to whatever fraction of the page one block happens to be.
 *
 * The edition is always named. Two versions of one guideline can carry near-identical
 * text on similarly numbered pages, so a page and a rectangle do not identify a passage -
 * they identify a passage *within an edition*. The version block is part of the figure
 * rather than a caption beside it.
 */
export function AnchorViewer({
  activeAnchor = null,
  detail,
  onSelectAnchor,
}: {
  /** The anchor whose paragraph the reader is on, drawn as the live region. */
  activeAnchor?: number | null;
  detail: EvidenceDetail;
  /** Supplied when the passage above is split into paragraphs this figure can reach. */
  onSelectAnchor?: (anchorIndex: number) => void;
}) {
  const groups = anchorGroups(detail);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const group = groups.find((item) => item.key === selectedKey) ?? groups[0] ?? null;
  const policy = renderPolicy(detail);

  if (!group) {
    return (
      <section className={styles["anchor-viewer"]} aria-labelledby="anchor-viewer-heading">
        <h3 id="anchor-viewer-heading">Location in source</h3>
        <p className={styles["anchor-empty"]}>
          No source location was supplied for this passage, so there is nothing to place.
        </p>
      </section>
    );
  }

  return (
    <section className={styles["anchor-viewer"]} aria-labelledby="anchor-viewer-heading">
      <div className={styles["anchor-viewer-heading"]}>
        <h3 id="anchor-viewer-heading">Location in source</h3>
        <span className={styles["anchor-viewer-status"]}>
          {anchorRegionStatus(group.primary)}
        </span>
      </div>

      {groups.length > 1 ? (
        <div className={styles["anchor-switcher"]} role="group" aria-label="Source locations">
          {groups.map((item) => (
            <button
              className={item.key === group.key ? styles.selected : ""}
              type="button"
              key={item.key}
              onClick={() => setSelectedKey(item.key)}
              aria-pressed={item.key === group.key}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}

      <SourceVersionBlock version={group.primary.version} />

      <AnchorFigure
        key={group.key}
        activeAnchor={activeAnchor}
        canShowContent={policy.canQuote}
        detail={detail}
        group={group}
        onSelectAnchor={onSelectAnchor}
      />

      <AnchorCoordinates group={group} />

      {policy.canQuote ? null : (
        <p className={styles["anchor-degraded-note"]} role="note">
          <FileLock2 size={15} aria-hidden="true" />
          <span>
            {policy.licenceRestricted
              ? "The location is placed from the recorded anchor. The publisher content at that location is withheld under licence and is not reproduced here."
              : "The location is placed from the recorded anchor. This release carried no text for that location."}
          </span>
        </p>
      )}
    </section>
  );
}

/**
 * The edition the coordinates belong to.
 *
 * `source_version_id` is shown rather than only the version label, because the label is
 * what collides: two editions can both be captioned "2021" while being different
 * documents with different page numbering. The opaque identifier is the field that
 * settles which one a rectangle was measured against.
 */
function SourceVersionBlock({ version }: { version: SourceVersionIdentity }) {
  return (
    <dl className={styles["anchor-version"]}>
      <div className={styles["anchor-version-title"]}>
        <dt>Edition</dt>
        <dd>
          {version.title}
          <small>
            {version.publisherName} &middot; {version.versionLabel}
          </small>
        </dd>
      </div>
      <div>
        <dt>Version ID</dt>
        <dd>
          <code>{version.sourceVersionId}</code>
        </dd>
      </div>
      <div>
        <dt>Lifecycle</dt>
        <dd className={version.superseded ? styles["anchor-version-superseded"] : ""}>
          {humanizeCode(version.lifecycleStatus)}
        </dd>
      </div>
      <div>
        <dt>In force</dt>
        <dd>{effectiveRange(version)}</dd>
      </div>
    </dl>
  );
}

function AnchorFigure({
  activeAnchor,
  canShowContent,
  detail,
  group,
  onSelectAnchor,
}: {
  activeAnchor: number | null;
  canShowContent: boolean;
  detail: EvidenceDetail;
  group: AnchorGroup;
  onSelectAnchor?: (anchorIndex: number) => void;
}) {
  if (group.form === "TABLE_ROW") {
    return (
      <TableRowFigure
        group={group}
        row={parseSpreadsheetRow(detail)}
        version={group.primary.version}
      />
    );
  }
  if (group.form === "PAGE") {
    return (
      <PageFigure
        activeAnchor={activeAnchor}
        canShowContent={canShowContent}
        detail={detail}
        group={group}
        onSelectAnchor={onSelectAnchor}
        // Ask whenever there is a page to ask about, and let the API answer. Whether a
        // source page may be reproduced is a fact about the *source's* licence, which the
        // endpoint reads at the point it opens the bytes; deciding it a second time out
        // here would be this component holding an opinion about a licence it cannot see,
        // and the two copies would drift. The record's own `exact_highlight_available` is
        // deliberately not used: that is a per-record flag about showing one passage's
        // exact text, which is a different act from showing the page it sits on, and
        // conflating them made the document view unreachable for records the licence in
        // fact permits.
        canRenderPage
      />
    );
  }
  return <DocumentFigure group={group} />;
}

/**
 * A page with every recorded region marked on it.
 *
 * The frame is drawn at the page's own aspect and the regions are positioned as fractions
 * of it. A reader who wants to read the words rather than locate them opens the expanded
 * viewer; a reader who wants the paragraph that continues overleaf turns the page here.
 * Turning the page removes the marks, because they belong to the page they were measured
 * on and nowhere else.
 */
function PageFigure({
  activeAnchor,
  canShowContent,
  canRenderPage,
  detail,
  group,
  onSelectAnchor,
}: {
  activeAnchor: number | null;
  canShowContent: boolean;
  canRenderPage: boolean;
  detail: EvidenceDetail;
  group: AnchorGroup;
  onSelectAnchor?: (anchorIndex: number) => void;
}) {
  const [pageNumber, setPageNumber] = useState<number | null>(group.pdfPage);
  const [expanded, setExpanded] = useState(false);
  // The page count comes from the hook, which keeps it across a page turn: it is a fact
  // about the document, and reading it off the image in hand made it vanish while the next
  // page was in flight, disabling the control mid-turn.
  const { load, pageCount, reload } = useSourcePage(
    detail.source_id,
    pageNumber,
    canRenderPage,
  );
  const page = load.status === "loaded" ? load.image : null;
  const onAnchoredPage = pageNumber !== null && pageNumber === group.pdfPage;
  const printedPage = page?.pageLabel ?? (onAnchoredPage ? group.printedPage : null);

  const caption =
    pageNumber === null
      ? "Page not numbered"
      : printedPage !== null
        ? `Printed page ${printedPage} · PDF page ${pageNumber}`
        : `PDF page ${pageNumber}`;

  return (
    <figure className={styles["anchor-figure"]}>
      <PageSurface
        activeAnchor={activeAnchor}
        ariaLabel={figureLabel(group, {
          caption,
          marks: pageMarks(group, onAnchoredPage),
          rendered: page !== null,
        })}
        canShowContent={canShowContent}
        caption={caption}
        load={load}
        onRetry={reload}
        onSelectRegion={onAnchoredPage ? onSelectAnchor : undefined}
        regions={group.regions}
        showRegions={onAnchoredPage}
      />

      {/* Present whenever there is a page to navigate from, not only when one rendered.
          Gating the whole group on a loaded image meant a page turn that failed - or was
          merely still in flight - took away the controls that turn was made with,
          including the only way back to the cited page. A reader who stepped one page past
          a network hiccup was stranded on a frame they could not leave, and the group
          flickered out and back on every ordinary turn besides. What a missing image
          disables is the two controls that need to know the document's length, and the
          reading-size view, which has nothing to show. */}
      {pageNumber !== null ? (
        <div className={styles["page-controls"]} role="group" aria-label="Page navigation">
          <button
            type="button"
            onClick={() => setPageNumber((current) => (current ?? 1) - 1)}
            disabled={pageNumber <= 1}
            aria-label="Previous page of the source"
          >
            <ChevronLeft size={16} aria-hidden="true" />
          </button>
          <span>
            {pageCount > 0 ? `${pageNumber} of ${pageCount}` : `Page ${pageNumber}`}
          </span>
          <button
            type="button"
            onClick={() => setPageNumber((current) => (current ?? 0) + 1)}
            disabled={pageCount === 0 || pageNumber >= pageCount}
            aria-label="Next page of the source"
          >
            <ChevronRight size={16} aria-hidden="true" />
          </button>
          {onAnchoredPage ? null : (
            <button type="button" onClick={() => setPageNumber(group.pdfPage)}>
              Back to the cited page
            </button>
          )}
          <button
            type="button"
            className={styles["page-expand"]}
            disabled={page === null}
            onClick={() => setExpanded(true)}
          >
            <Expand size={15} aria-hidden="true" />
            Open at reading size
          </button>
        </div>
      ) : null}

      <figcaption>
        {caption}
        {" of "}
        {group.primary.version.versionLabel}
        {onAnchoredPage && group.regions.length > 1
          ? ` · ${group.regions.length} regions recorded`
          : ""}
      </figcaption>

      {expanded ? (
        <PageViewer
          activeAnchor={activeAnchor}
          canShowContent={canShowContent}
          detail={detail}
          group={group}
          onOpenChange={setExpanded}
          open={expanded}
        />
      ) : null}
    </figure>
  );
}

/**
 * The row as the sheet has it.
 *
 * This is the workbook counterpart of the page image, and it earns its place the same way:
 * for a PDF record the passage is printed as text *and* shown on the page it came off, and
 * nobody calls the second one a duplicate - the words are the same, but only one of them
 * tells you where on the sheet they sit. So the cells carry their values here, laid out
 * left to right under their column letters, with the ones this citation anchors marked.
 *
 * An empty grid was the honest drawing when the licence withheld the text, and it still is
 * - that branch is below. It was never the right drawing for a record whose values are in
 * hand, which is most of this corpus.
 */
function TableRowFigure({
  group,
  row,
  version,
}: {
  group: AnchorGroup;
  row: SpreadsheetRow | null;
  version: SourceVersionIdentity;
}) {
  const cell = group.cells[0] ?? null;

  if (cell === null) {
    return (
      <figure className={styles["anchor-figure"]}>
        <div
          className={styles["cell-frame-empty"]}
          role="img"
          aria-label={figureLabel(group, {
            caption: "Table cell",
            marks: "The release carried no address for it, so no cell is marked.",
            rendered: false,
          })}
        >
          <Grid3x3 size={22} aria-hidden="true" />
          <strong>Cell address not carried</strong>
          <span>
            This anchor names a table cell, but the release carried no table, row, or
            column for it. The passage is located to the document and no further.
          </span>
        </div>
        <figcaption>
          Table cell in {version.versionLabel}, address not carried
        </figcaption>
      </figure>
    );
  }

  // The row itself is drawn above, by `PassageBody`, in the worksheet's own layout - with
  // the values in the cells and the anchored ones marked. A grid of empty boxes under it
  // adds no location the coordinates below do not already state, and reading it as a
  // picture of the sheet is exactly the mistake it invites: it is three columns wide and
  // the row is not.
  if (row !== null) {
    return (
      <p className={styles["anchor-drawn-above"]}>
        <Grid3x3 size={15} aria-hidden="true" />
        <span>
          Row {row.rowNumber} of {cell.tableId} is shown above in worksheet order, with the
          cells this citation anchors marked.
        </span>
      </p>
    );
  }

  return <TableCellGrid cells={group.cells} cell={cell} group={group} version={version} />;
}


/**
 * The addressed cells drawn on a grid, for a record whose text is withheld.
 *
 * With no values to show, the honest figure is the address itself. The row and column
 * headings are the document's own one-based numbering so a reader can find the cell in the
 * original, and the window is clamped around the address rather than starting at A1,
 * because an anchor deep in a long table would otherwise place off the drawn grid.
 */
function TableCellGrid({
  cell,
  cells,
  group,
  version,
}: {
  cell: TableCellAddress;
  cells: TableCellAddress[];
  group: AnchorGroup;
  version: SourceVersionIdentity;
}) {
  const addressed = new Set(cells.map((item) => item.columnIndex));
  const firstColumn = Math.max(0, cell.columnIndex - 1);
  const firstRow = Math.max(0, cell.rowIndex - 1);
  const columns = [0, 1, 2].map((offset) => firstColumn + offset);
  const rows = [0, 1, 2].map((offset) => firstRow + offset);

  return (
    <figure className={styles["anchor-figure"]}>
      <div
        className={styles["cell-frame"]}
        role="img"
        aria-label={figureLabel(group, {
          caption: cell.reference,
          marks:
            cells.length > 1
              ? `${cells.length} addressed cells are marked on the grid.`
              : "The addressed cell is marked on the grid.",
          rendered: false,
        })}
      >
        <span className={styles["cell-frame-title"]} aria-hidden="true">
          {cell.tableId}
        </span>
        <div className={styles["cell-grid"]} aria-hidden="true">
          <span className={styles["cell-corner"]} />
          {columns.map((columnIndex) => (
            <span
              className={`${styles["cell-heading"]} ${addressed.has(columnIndex) ? styles.addressed : ""}`}
              key={`column-${columnIndex}`}
            >
              {columnLetter(columnIndex)}
            </span>
          ))}
          {rows.map((rowIndex) => (
            <Fragment key={`row-${rowIndex}`}>
              <span
                className={`${styles["cell-heading"]} ${rowIndex === cell.rowIndex ? styles.addressed : ""}`}
              >
                {rowIndex + 1}
              </span>
              {columns.map((columnIndex) => (
                <span
                  className={`${styles.cell} ${rowIndex === cell.rowIndex && addressed.has(columnIndex) ? styles.addressed : ""}`}
                  key={`cell-${rowIndex}-${columnIndex}`}
                >
                  {rowIndex === cell.rowIndex && addressed.has(columnIndex) ? (
                    <Crosshair size={14} />
                  ) : null}
                </span>
              ))}
            </Fragment>
          ))}
        </div>
      </div>
      <figcaption>
        {cells.length > 1
          ? `Cells ${cells.map((item) => item.columnLetter + item.rowNumber).join(", ")} of table ${cell.tableId} in ${version.versionLabel}`
          : `Cell ${cell.columnLetter}${cell.rowNumber} of table ${cell.tableId} in ${version.versionLabel}`}
      </figcaption>
    </figure>
  );
}

function DocumentFigure({ group }: { group: AnchorGroup }) {
  return (
    <figure className={styles["anchor-figure"]}>
      <div
        className={styles["document-frame"]}
        role="img"
        aria-label={figureLabel(group, {
          caption: "Document scope",
          marks: "There is no page and no region, so nothing is marked.",
          rendered: false,
        })}
      >
        <FileText size={26} aria-hidden="true" />
        <strong>Located to the document</strong>
        <span>
          This anchor names {humanizeCode(group.primary.kind).toLowerCase()} scope. It
          carries no page and no region, so there is no place on a page to mark.
        </span>
      </div>
      <figcaption>
        {group.label} in {group.primary.version.versionLabel}
      </figcaption>
    </figure>
  );
}

/** The coordinates as recorded, under the figure that was drawn from them. */
function AnchorCoordinates({ group }: { group: AnchorGroup }) {
  const rows: Array<[string, string]> = [["Locator kind", group.primary.kind]];

  if (group.form === "PAGE") {
    rows.push(["PDF page", group.pdfPage === null ? "Not supplied" : String(group.pdfPage)]);
    rows.push(["Printed page", group.printedPage ?? "Not supplied"]);
    rows.push([
      "Regions",
      group.regions.length === 0
        ? "Not recorded"
        : group.regions.length === 1
          ? group.regions[0]!.region.readout
          : `${group.regions.length} recorded on this page`,
    ]);
  } else if (group.form === "TABLE_ROW") {
    const cell = group.cells[0] ?? null;
    rows.push(["Table", cell?.tableId ?? "Not carried"]);
    // Singular keeps the qualified `Sheet!C4` reference, which is the unambiguous address
    // a reader can carry to the workbook. A row with several anchored cells has no single
    // such reference, so it lists them against the table named on the line above.
    rows.push(
      group.cells.length > 1
        ? [
            "Cells",
            group.cells.map((item) => `${item.columnLetter}${item.rowNumber}`).join(", "),
          ]
        : ["Cell", cell === null ? "Not carried" : cell.reference],
    );
    rows.push([
      "Row, column",
      cell === null
        ? "Not carried"
        : `${cell.rowIndex}, ${group.cells.map((item) => item.columnIndex).join(" / ")} (zero-based)`,
    ]);
  }

  rows.push(["Source URI", group.primary.sourceUri]);

  return (
    <>
      <dl className={styles["anchor-coordinates"]}>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {group.regions.length > 1 ? (
        // Every recorded box, kept reachable but folded away. One page carries as many as
        // the extractor found blocks on it, and printing thirty readouts under a figure
        // buries the page and the edition above them.
        <details className={styles["anchor-region-list"]}>
          <summary>All {group.regions.length} recorded regions</summary>
          <ol>
            {group.regions.map(({ anchorIndex, region }) => (
              <li key={anchorIndex}>{region.readout}</li>
            ))}
          </ol>
        </details>
      ) : null}
    </>
  );
}

/**
 * What a figure says, for a reader who cannot see it.
 *
 * Each drawn figure is one `role="img"`, so this label carries the whole address in words
 * - including the edition, on the same grounds the visual figure does. What is marked is
 * passed in rather than derived: a page, a grid of cells and a document-scope anchor are
 * marked in three different senses, and a single derivation would end up telling the
 * reader of a section anchor that they are not on the cited page.
 */
function figureLabel(
  group: AnchorGroup,
  { caption, marks, rendered }: { caption: string; marks: string; rendered: boolean },
): string {
  const surface = rendered
    ? "The source page is shown."
    : "The surface is drawn as an empty frame; no source content is reproduced.";
  return `${caption}. ${group.primary.qualifiedLabel}. ${surface} ${marks}`;
}

/** What a page figure has marked on it, in words. */
function pageMarks(group: AnchorGroup, onAnchoredPage: boolean): string {
  if (!onAnchoredPage) return "This is not the cited page, so no region is marked.";
  if (group.regions.length === 0) return "No region is drawn.";
  if (group.regions.length === 1) return "The marked region shows where the passage sits.";
  return `${group.regions.length} marked regions show where the passage sits.`;
}

function effectiveRange(version: SourceVersionIdentity): string {
  const { effectiveFrom: start, effectiveTo: end } = version;
  if (!start && !end) return "Dates not supplied";
  if (start && end) return `${formatDate(start)}–${formatDate(end)}`;
  if (start) return `From ${formatDate(start)}`;
  return `Until ${formatDate(end!)}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}
