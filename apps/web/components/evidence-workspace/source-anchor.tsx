import { Crosshair, FileLock2, FileText, Grid3x3, MapPin, ScanLine } from "lucide-react";
import { Fragment } from "react";

import type {
  PassageBlock,
  PassageSegment,
  SpreadsheetRow,
} from "@/lib/document-inspection";
import {
  findMatchCount,
  parseSpreadsheetRow,
  passageBlocks,
  passageSegments,
} from "@/lib/document-inspection";
import type { AnchorPrecision, RenderPolicy, SourceAnchor } from "@/lib/evidence-presentation";
import {
  anchorRegionStatus,
  humanizeCode,
  renderPolicy,
  sourceAnchors,
} from "@/lib/evidence-presentation";
import type { EvidenceDetail } from "@/lib/types";

import styles from "./workspace.module.css";

const PRECISION_COPY: Record<AnchorPrecision, { icon: typeof MapPin; label: string }> = {
  EXACT_REGION: { icon: Crosshair, label: "Exact region" },
  CELL: { icon: Grid3x3, label: "Table cell" },
  PAGE: { icon: ScanLine, label: "Page" },
  DOCUMENT: { icon: FileText, label: "Document" },
};

/**
 * Where a passage sits in its source document, and how precisely that is known.
 *
 * The rows here are a summary, not the viewer: `AnchorViewer` groups these anchors by the
 * surface they sit on and draws them. This list stays one line per recorded locator
 * because it is the provenance record rather than the picture, and "page 11" and "this
 * rectangle on page 11" are different provenance claims that should never read alike.
 */
export function SourceAnchorList({ detail }: { detail: EvidenceDetail }) {
  const anchors = sourceAnchors(detail);
  if (!anchors.length) {
    return (
      <p className={styles["anchor-empty"]}>No source location was supplied for this passage.</p>
    );
  }

  return (
    <ul className={styles["anchor-list"]} aria-label="Source locations">
      {anchors.map((anchor) => (
        <li key={anchor.key}>
          <AnchorRow anchor={anchor} />
        </li>
      ))}
    </ul>
  );
}

function AnchorRow({ anchor }: { anchor: SourceAnchor }) {
  const { icon: Icon, label } = PRECISION_COPY[anchor.precision];
  return (
    <div className={styles["anchor-row"]}>
      <span className={`${styles["anchor-precision"]} ${styles[anchor.precision]}`}>
        <Icon size={14} aria-hidden="true" />
        {label}
      </span>
      <span className={styles["anchor-copy"]}>
        <strong>{anchor.label}</strong>
        {/* The kind, and nothing else. Every anchor in this list belongs to one evidence
            record and therefore to one edition, which the record block names above it;
            printing the edition on every row restated it a dozen times, and in this
            corpus the label and the identifier are the same string, so it restated it
            twice per row. */}
        <small>{humanizeCode(anchor.kind)}</small>
      </span>
      <span className={styles["anchor-status"]}>{anchorRegionStatus(anchor)}</span>
    </div>
  );
}

/**
 * What to point at inside a passage, and what the reader is looking for.
 *
 * Two different claims about a piece of text, kept apart: `terms` is the system saying
 * "this is where your question and this passage meet", `find` is the reader saying "show
 * me this". They are marked differently and the reader's search always wins an overlap.
 */
export interface PassageHighlight {
  terms: string[];
  find: string;
  /** Which find match is the current one, for the reader stepping through them. */
  currentMatch: number;
}

const NO_HIGHLIGHT: PassageHighlight = { terms: [], find: "", currentMatch: 0 };

/**
 * The passage itself, or the reason it is not shown.
 *
 * One rendering, because there is one passage. It used to have a second, compact form for
 * a panel in the other column that showed the same record at the same time; two renderings
 * of one thing on one screen is not a choice a reader should have to resolve.
 *
 * A restricted record is not a degraded view: the provenance it does carry is complete,
 * and saying so plainly is more useful than an apology for missing text.
 *
 * The two structured shapes this corpus actually has are recovered rather than printed
 * raw. A workbook record is a row - `A146=…` per populated cell - laid back out as
 * addresses and values. A PDF record is a page of text blocks joined by blank lines, laid
 * back out as paragraphs, each able to point at the rectangle it was read out of.
 */
export function PassageBody({
  activeAnchor = null,
  detail,
  highlight = NO_HIGHLIGHT,
  onSelectBlock,
  policy = renderPolicy(detail),
}: {
  /** The paragraph the reader is on, when the figure below is pointing at one. */
  activeAnchor?: number | null;
  detail: EvidenceDetail;
  highlight?: PassageHighlight;
  /** Supplied when a paragraph can be located on a page figure. */
  onSelectBlock?: (anchorIndex: number) => void;
  policy?: RenderPolicy;
}) {
  if (!policy.canQuote || detail.exact_text === null) {
    return (
      <div className={styles["restricted-passage"]}>
        <FileLock2 size={28} aria-hidden="true" />
        <strong>{policy.headline}</strong>
        <span>{policy.explanation}</span>
      </div>
    );
  }

  const row = parseSpreadsheetRow(detail);
  if (row !== null) {
    return (
      <div className={styles["source-passage"]}>
        <span>{policy.headline} &middot; source row</span>
        <SpreadsheetRowTable highlight={highlight} row={row} />
      </div>
    );
  }

  const blocks = passageBlocks(detail);
  if (blocks !== null && blocks.length > 1) {
    return (
      <div className={styles["source-passage"]}>
        <span>
          {policy.headline} &middot; {blocks.length} blocks on this page
        </span>
        <PassageBlocks
          activeAnchor={activeAnchor}
          blocks={blocks}
          highlight={highlight}
          onSelectBlock={onSelectBlock}
        />
      </div>
    );
  }

  return (
    <div className={styles["source-passage"]}>
      <span>{policy.headline}</span>
      <mark>
        <MarkedText highlight={highlight} text={detail.exact_text} />
      </mark>
    </div>
  );
}

/**
 * A page's text as the blocks it was extracted from.
 *
 * The blank lines that separated them collapse in HTML, so a page-sized passage used to
 * arrive as one unbroken slab - the structure was in the payload the whole time and was
 * being thrown away on the way to the screen. Restoring it does two things at once: the
 * page becomes readable, and each paragraph gains an address, which is what the locate
 * control in the gutter spends.
 *
 * The paragraph text itself is deliberately not a control. A reader selects and copies
 * from these, and a button wrapped around a paragraph makes dragging across it a click.
 */
function PassageBlocks({
  activeAnchor,
  blocks,
  highlight,
  onSelectBlock,
}: {
  activeAnchor: number | null;
  blocks: PassageBlock[];
  highlight: PassageHighlight;
  onSelectBlock?: (anchorIndex: number) => void;
}) {
  // Find matches are numbered across the whole passage rather than restarted per block,
  // so "3 of 12" in the toolbar counts the things the reader is stepping through.
  const withOffsets = blocks.reduce<{
    rows: Array<{ block: PassageBlock; matchOffset: number }>;
    total: number;
  }>(
    (state, block) => ({
      rows: [...state.rows, { block, matchOffset: state.total }],
      total:
        state.total +
        findMatchCount(
          passageSegments(block.text, { terms: highlight.terms, find: highlight.find }),
        ),
    }),
    { rows: [], total: 0 },
  ).rows;

  return (
    <ol className={styles["passage-blocks"]}>
      {withOffsets.map(({ block, matchOffset }) => {
        const locatable = onSelectBlock !== undefined && block.anchorIndex !== null;
        const active = block.anchorIndex !== null && block.anchorIndex === activeAnchor;
        return (
          <li
            className={active ? styles["passage-block-active"] : undefined}
            data-anchor={block.anchorIndex ?? undefined}
            key={block.index}
          >
            {locatable ? (
              <button
                aria-label={`Locate block ${block.index + 1} on the page`}
                aria-pressed={active}
                className={styles["passage-locate"]}
                onClick={() => onSelectBlock!(block.anchorIndex!)}
                type="button"
              >
                <Crosshair size={13} aria-hidden="true" />
              </button>
            ) : (
              <span className={styles["passage-locate-empty"]} aria-hidden="true" />
            )}
            <mark>
              <MarkedText
                highlight={highlight}
                matchOffset={matchOffset}
                text={block.text.trim()}
              />
            </mark>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One row of a source workbook, laid out as the workbook lays it out.
 *
 * Column letters across the top, the row number at the left, values in the cells, and the
 * cells this citation anchors marked. This is the only rendering of the row: a vertical
 * cell-per-line list beside a grid of the same four values, a few hundred pixels apart,
 * is the same thing twice - and of the two, only this one tells a reader which column a
 * value sits under, which is what they will be looking at when they open the workbook.
 *
 * It scrolls sideways rather than wrapping. A DAK row runs to twenty cells of
 * sentence-length text, and a row that wraps is no longer a row.
 */
function SpreadsheetRowTable({
  highlight,
  row,
}: {
  highlight: PassageHighlight;
  row: SpreadsheetRow;
}) {
  // Find matches are numbered across the whole passage, not restarted per cell, so that
  // "3 of 12" in the toolbar counts the same things the reader is stepping through. Each
  // cell therefore has to know how many matches precede it, which is a running total
  // carried through the fold rather than a counter mutated as the row is mapped.
  const rowsWithOffsets = row.cells.reduce<{
    rows: Array<{ cell: (typeof row.cells)[number]; matchOffset: number }>;
    total: number;
  }>(
    (state, cell) => ({
      rows: [...state.rows, { cell, matchOffset: state.total }],
      total:
        state.total +
        findMatchCount(
          passageSegments(cell.value, { terms: highlight.terms, find: highlight.find }),
        ),
    }),
    { rows: [], total: 0 },
  ).rows;

  return (
    <div className={styles["sheet-scroll"]}>
      <table className={styles["sheet-row"]}>
        <caption className={styles.srOnly}>
          Row {row.rowNumber}
          {row.tableId === null ? "" : ` of worksheet ${row.tableId}`}, as the release
          carried it. Cells this citation anchors are marked.
        </caption>
        <thead>
          <tr>
            <th className={styles["sheet-corner"]} scope="col">
              <span className={styles.srOnly}>Row</span>
            </th>
            {row.cells.map((cell) => (
              <th
                className={cell.anchored ? styles.addressed : undefined}
                key={cell.columnIndex}
                scope="col"
              >
                {cell.columnLetter}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th className={styles["sheet-corner"]} scope="row">
              {row.rowNumber}
            </th>
            {rowsWithOffsets.map(({ cell, matchOffset }) => (
              <td
                className={cell.anchored ? styles.addressed : undefined}
                key={cell.columnIndex}
              >
                {cell.anchored ? (
                  <Crosshair
                    className={styles["sheet-mark"]}
                    size={12}
                    aria-hidden="true"
                  />
                ) : null}
                <MarkedText
                  highlight={highlight}
                  matchOffset={matchOffset}
                  text={cell.value}
                />
                {cell.anchored ? (
                  <span className={styles.srOnly}>, anchored by this citation</span>
                ) : null}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/**
 * Text with the question's terms and the reader's search marked in it.
 *
 * A PDF evidence unit is a whole page of text, and the reader's real task is finding the
 * two lines in it that bear on what was asked. Marking is presentational only - the
 * segments concatenate back to the exact bytes the release carried, so nothing shown here
 * is other than what was verified.
 */
export function MarkedText({
  highlight,
  matchOffset = 0,
  text,
}: {
  highlight: PassageHighlight;
  /** How many find matches precede this fragment in the passage it belongs to. */
  matchOffset?: number;
  text: string;
}) {
  const segments = passageSegments(text, { terms: highlight.terms, find: highlight.find });
  if (segments.length === 1 && segments[0]!.kind === "plain") return <>{text}</>;

  return (
    <>
      {segments.map((segment, index) => (
        <Fragment key={index}>
          {renderSegment(segment, highlight.currentMatch, matchOffset)}
        </Fragment>
      ))}
    </>
  );
}

function renderSegment(segment: PassageSegment, currentMatch: number, matchOffset: number) {
  if (segment.kind === "plain") return segment.text;
  if (segment.kind === "term") {
    return <b className={styles["term-hit"]}>{segment.text}</b>;
  }
  const position = segment.matchIndex === null ? null : segment.matchIndex + matchOffset;
  const current = position === currentMatch;
  return (
    <b
      className={`${styles["find-hit"]} ${current ? styles["find-hit-current"] : ""}`}
      data-find-match={position ?? undefined}
      data-find-current={current ? "true" : undefined}
    >
      {segment.text}
    </b>
  );
}
