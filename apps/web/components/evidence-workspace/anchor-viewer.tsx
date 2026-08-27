"use client";

import { Crosshair, FileLock2, FileText, Grid3x3, ScanLine } from "lucide-react";
import { Fragment, useState } from "react";

import type {
  AnchorRegion,
  SourceAnchor,
  SourceVersionIdentity,
  TableCellAddress,
} from "@/lib/evidence-presentation";
import {
  anchorRegionStatus,
  columnLetter,
  humanizeCode,
  renderPolicy,
  sourceAnchors,
} from "@/lib/evidence-presentation";
import type { EvidenceDetail } from "@/lib/types";

import styles from "./workspace.module.css";

/**
 * An anchor drawn as a place in a document.
 *
 * Everything upstream of this component - the extractor's bounding boxes, the corpus
 * `SourceAnchor`, the serving locator - exists so a reader can be shown *where* a
 * passage came from. Until now that chain terminated in a sentence. This is where it
 * terminates in a picture.
 *
 * Two properties hold on every path through it.
 *
 * The source content is never required, and never reproduced here. Every WHO asset in
 * the active trust root carries `render_allowed: false` while the licence review is
 * open, so the view that ships is a page frame with a region marked on it and no
 * publisher text inside. That is not a placeholder for a better view: a marked region
 * over an empty frame is the honest rendering of "we know exactly where this is and may
 * not show it to you", and it is complete on its own terms. A licence that opens later
 * changes the region's tint and its tag, and moves nothing else - the passage itself is
 * rendered once, full size, by `PassageBody`, and drawing an illegible second copy
 * inside the rectangle would restate content where the point is location.
 *
 * The edition is always named. Two versions of one guideline can carry near-identical
 * text on similarly numbered pages, so a page and a rectangle do not identify a
 * passage - they identify a passage *within an edition*. The version block is part of
 * the figure rather than a caption beside it.
 */
export function AnchorViewer({ detail }: { detail: EvidenceDetail }) {
  const anchors = sourceAnchors(detail);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const anchor = anchors.find((item) => item.key === selectedKey) ?? anchors[0] ?? null;
  const policy = renderPolicy(detail);

  if (!anchor) {
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
        <span className={styles["anchor-viewer-status"]}>{anchorRegionStatus(anchor)}</span>
      </div>

      {anchors.length > 1 ? (
        <div className={styles["anchor-switcher"]} role="group" aria-label="Source locations">
          {anchors.map((item) => (
            <button
              className={item.key === anchor.key ? styles.selected : ""}
              type="button"
              key={item.key}
              onClick={() => setSelectedKey(item.key)}
              aria-pressed={item.key === anchor.key}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}

      <SourceVersionBlock version={anchor.version} />

      <AnchorFigure anchor={anchor} canShowContent={policy.canQuote} />

      <AnchorCoordinates anchor={anchor} />

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
  anchor,
  canShowContent,
}: {
  anchor: SourceAnchor;
  canShowContent: boolean;
}) {
  if (anchor.placement.form === "TABLE_CELL") {
    return <TableCellFigure anchor={anchor} cell={anchor.placement.cell} />;
  }
  if (anchor.placement.form === "PAGE") {
    return (
      <PageFigure
        anchor={anchor}
        canShowContent={canShowContent}
        pdfPage={anchor.placement.pdfPage}
        printedPage={anchor.placement.printedPage}
        region={anchor.placement.region}
      />
    );
  }
  return <DocumentFigure anchor={anchor} />;
}

/**
 * A page with the recorded region marked on it.
 *
 * The frame is drawn at a fixed page aspect and the region is positioned as a fraction
 * of it, which is exactly what a proportional bounding box means. A region recorded in
 * the source document's own units is not drawn at all - see `AnchorRegion` - and the
 * frame then shows the page alone, with the numbers reported underneath.
 */
function PageFigure({
  anchor,
  canShowContent,
  pdfPage,
  printedPage,
  region,
}: {
  anchor: SourceAnchor;
  canShowContent: boolean;
  pdfPage: number | null;
  printedPage: string | null;
  region: AnchorRegion | null;
}) {
  const drawable = region !== null && region.placement === "PROPORTIONAL";
  const pageCaption =
    printedPage !== null
      ? `Printed page ${printedPage}`
      : pdfPage !== null
        ? `PDF page ${pdfPage}`
        : "Page not numbered";

  return (
    <figure className={styles["anchor-figure"]}>
      <div
        className={styles["page-frame"]}
        role="img"
        aria-label={figureLabel(anchor, drawable)}
      >
        <span className={styles["page-frame-number"]} aria-hidden="true">
          {pageCaption}
        </span>
        {drawable ? (
          <span
            className={`${styles["page-region"]} ${canShowContent ? styles.readable : styles.withheld}`}
            style={{
              left: pageFraction(region.left),
              top: pageFraction(region.top),
              width: pageFraction(region.width),
              height: pageFraction(region.height),
            }}
            aria-hidden="true"
          >
            <span className={styles["page-region-tag"]}>
              <Crosshair size={13} aria-hidden="true" />
              {canShowContent ? "Passage region" : "Anchored region"}
            </span>
          </span>
        ) : null}
        {drawable ? null : (
          <span className={styles["page-frame-note"]} aria-hidden="true">
            <ScanLine size={20} />
            {region === null
              ? "No region recorded on this page"
              : "Region recorded in source units; no page size to place it against"}
          </span>
        )}
      </div>
      <figcaption>
        {pdfPage !== null && printedPage !== null
          ? `Printed page ${printedPage}, PDF page ${pdfPage}`
          : pageCaption}
        {" of "}
        {anchor.version.versionLabel}
      </figcaption>
    </figure>
  );
}

/**
 * A recorded fraction as a CSS length.
 *
 * Rounded to four decimal places, which is finer than any display can resolve and
 * keeps floating-point subtraction out of the rendered attribute.
 */
function pageFraction(fraction: number): string {
  return `${Number((fraction * 100).toFixed(4))}%`;
}

/**
 * A table with the addressed cell marked.
 *
 * The grid is a frame, not the publisher's table: no cell holds source content unless
 * the licence permits the passage, and the row and column headings are the document's
 * own one-based numbering so a reader can find the cell in the original. The window is
 * clamped around the address rather than starting at A1, because an anchor deep in a
 * long table would otherwise place off the drawn grid entirely.
 */
function TableCellFigure({
  anchor,
  cell,
}: {
  anchor: SourceAnchor;
  cell: TableCellAddress | null;
}) {
  if (cell === null) {
    return (
      <figure className={styles["anchor-figure"]}>
        <div className={styles["cell-frame-empty"]} role="img" aria-label={figureLabel(anchor, false)}>
          <Grid3x3 size={22} aria-hidden="true" />
          <strong>Cell address not carried</strong>
          <span>
            This anchor names a table cell, but the release carried no table, row, or
            column for it. The passage is located to the document and no further.
          </span>
        </div>
        <figcaption>
          Table cell in {anchor.version.versionLabel}, address not carried
        </figcaption>
      </figure>
    );
  }

  const firstColumn = Math.max(0, cell.columnIndex - 1);
  const firstRow = Math.max(0, cell.rowIndex - 1);
  const columns = [0, 1, 2].map((offset) => firstColumn + offset);
  const rows = [0, 1, 2].map((offset) => firstRow + offset);

  return (
    <figure className={styles["anchor-figure"]}>
      <div className={styles["cell-frame"]} role="img" aria-label={figureLabel(anchor, true)}>
        <span className={styles["cell-frame-title"]} aria-hidden="true">
          {cell.tableId}
        </span>
        <div className={styles["cell-grid"]} aria-hidden="true">
          <span className={styles["cell-corner"]} />
          {columns.map((columnIndex) => (
            <span
              className={`${styles["cell-heading"]} ${columnIndex === cell.columnIndex ? styles.addressed : ""}`}
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
                  className={`${styles.cell} ${rowIndex === cell.rowIndex && columnIndex === cell.columnIndex ? styles.addressed : ""}`}
                  key={`cell-${rowIndex}-${columnIndex}`}
                >
                  {rowIndex === cell.rowIndex && columnIndex === cell.columnIndex ? (
                    <Crosshair size={14} />
                  ) : null}
                </span>
              ))}
            </Fragment>
          ))}
        </div>
      </div>
      <figcaption>
        Cell {cell.columnLetter}
        {cell.rowNumber} of table {cell.tableId} in {anchor.version.versionLabel}
      </figcaption>
    </figure>
  );
}

function DocumentFigure({ anchor }: { anchor: SourceAnchor }) {
  return (
    <figure className={styles["anchor-figure"]}>
      <div className={styles["document-frame"]} role="img" aria-label={figureLabel(anchor, false)}>
        <FileText size={26} aria-hidden="true" />
        <strong>Located to the document</strong>
        <span>
          This anchor names {humanizeCode(anchor.kind).toLowerCase()} scope. It carries no
          page and no region, so there is no place on a page to mark.
        </span>
      </div>
      <figcaption>
        {anchor.label} in {anchor.version.versionLabel}
      </figcaption>
    </figure>
  );
}

/** The coordinates as recorded, under the figure that was drawn from them. */
function AnchorCoordinates({ anchor }: { anchor: SourceAnchor }) {
  const rows: Array<[string, string]> = [["Locator kind", anchor.kind]];

  if (anchor.placement.form === "PAGE") {
    const { pdfPage, printedPage, region } = anchor.placement;
    rows.push(["PDF page", pdfPage === null ? "Not supplied" : String(pdfPage)]);
    rows.push(["Printed page", printedPage ?? "Not supplied"]);
    rows.push(["Region", region === null ? "Not recorded" : region.readout]);
  } else if (anchor.placement.form === "TABLE_CELL") {
    const { cell } = anchor.placement;
    rows.push(["Table", cell?.tableId ?? "Not carried"]);
    rows.push(["Cell", cell === null ? "Not carried" : cell.reference]);
    rows.push([
      "Row, column",
      cell === null ? "Not carried" : `${cell.rowIndex}, ${cell.columnIndex} (zero-based)`,
    ]);
  }

  rows.push(["Source URI", anchor.sourceUri]);

  return (
    <dl className={styles["anchor-coordinates"]}>
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * What the figure says, for a reader who cannot see it.
 *
 * The drawn figure is one `role="img"`, so this label carries the whole address in
 * words - including the edition, on the same grounds the visual figure does.
 */
function figureLabel(anchor: SourceAnchor, drawn: boolean): string {
  const placed = drawn
    ? "The marked region shows where the passage sits."
    : "No region is drawn.";
  return `${anchor.qualifiedLabel}. ${placed}`;
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
