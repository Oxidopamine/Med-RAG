"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  ChevronLeft,
  ChevronRight,
  Crosshair,
  Minus,
  Plus,
  Undo2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EXPANDED_PAGE_DPI } from "@/lib/api";
import type { AnchorGroup } from "@/lib/document-inspection";
import type { EvidenceDetail } from "@/lib/types";

import {
  PageSurface,
  placeRegion,
  regionEnvelope,
  useSourcePage,
  type PlacedRegion,
} from "./source-page";
import styles from "./workspace.module.css";

/**
 * Zoom, as a multiple of fitting the page across the stage.
 *
 * 1 is the whole page in view, which is the right opening position - the reader has just
 * come from a thumbnail and needs to see where on the sheet they have landed. Everything
 * above it is for reading, and the ceiling is set by the render: at 288 DPI a page is
 * about four device pixels per point, so past roughly 4x the reader is magnifying the
 * rasteriser's output rather than the document.
 */
const MINIMUM_ZOOM = 1;
const MAXIMUM_ZOOM = 4;
const ZOOM_STEP = 0.5;

/** How much of the stage the anchored region should fill when zoomed to. */
const REGION_FILL = 0.7;

interface PageViewerProps {
  /** The anchor whose paragraph the reader is on, kept accented at reading size. */
  activeAnchor?: number | null;
  canShowContent: boolean;
  detail: EvidenceDetail;
  group: AnchorGroup;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

/**
 * The source page at reading size, with the anchored region marked on it.
 *
 * The inline figure in the inspector is a thumbnail: it answers *where on the page*, at a
 * width of a few hundred pixels, which is about half the size a 144 DPI page needs to be
 * legible. That is the correct answer to the question it is asked and the wrong one for a
 * reader who now wants to read the words the rectangle is around. This is where that
 * happens - a bigger render, a zoom, and the page turns either side of it.
 *
 * The regions travel with their page and only their page. Turning to page 15 draws no
 * rectangle and says so, because a mark carried onto a neighbouring page would assert a
 * location the record never recorded.
 */
export function PageViewer({
  activeAnchor = null,
  canShowContent,
  detail,
  group,
  onOpenChange,
  open,
}: PageViewerProps) {
  const anchoredPage = group.pdfPage;
  const [pageNumber, setPageNumber] = useState<number | null>(anchoredPage);
  const [zoom, setZoom] = useState(MINIMUM_ZOOM);
  const [focusToken, setFocusToken] = useState(0);
  const stageRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Reopening lands back on the anchored page at full-page zoom, and does so by
  // construction: the viewer is mounted only while it is open, so the initial state above
  // *is* the reset. Restoring wherever the reader last wandered to would open on a page
  // that is not the one the citation names, which is the one thing it must never look
  // like - and an effect that re-applied the initial state would only be a second, less
  // reliable way of saying what mounting already says.

  const { load, reload } = useSourcePage(
    detail.source_id,
    pageNumber,
    open && pageNumber !== null,
    EXPANDED_PAGE_DPI,
  );
  const page = load.status === "loaded" ? load.image : null;
  const onAnchoredPage = pageNumber !== null && pageNumber === anchoredPage;

  const placed = useMemo<PlacedRegion[]>(
    () =>
      onAnchoredPage
        ? group.regions.flatMap(({ region }) => {
            const box = placeRegion(region, page);
            return box === null ? [] : [box];
          })
        : [],
    [group.regions, onAnchoredPage, page],
  );
  // Zoom-to-region follows the paragraph the reader is on when there is one, and the whole
  // marked extent otherwise. Reading one paragraph at reading size is the point of opening
  // this; framing the entire page's worth of boxes around it would defeat that.
  const live = useMemo<PlacedRegion[]>(() => {
    if (!onAnchoredPage || activeAnchor === null) return placed;
    const match = group.regions.find((entry) => entry.anchorIndex === activeAnchor);
    const box = match ? placeRegion(match.region, page) : null;
    return box === null ? placed : [box];
  }, [activeAnchor, group.regions, onAnchoredPage, page, placed]);
  const envelope = useMemo(() => regionEnvelope(live), [live]);

  const zoomToRegion = useCallback(() => {
    if (!envelope || envelope.width <= 0) return;
    const next = Math.min(
      MAXIMUM_ZOOM,
      Math.max(MINIMUM_ZOOM, REGION_FILL / envelope.width),
    );
    setZoom(next);
    setFocusToken((token) => token + 1);
  }, [envelope]);

  // Scrolling happens after the zoom has been laid out, which is why it hangs off a token
  // rather than off the zoom value: the reader can ask to be taken back to the region at
  // a zoom level they are already at, and that has to move the view.
  useEffect(() => {
    const stage = stageRef.current;
    const sheet = sheetRef.current;
    if (focusToken === 0 || !stage || !sheet || !envelope) return;
    if (typeof stage.scrollTo !== "function") return;
    const centreX = sheet.offsetLeft + (envelope.left + envelope.width / 2) * sheet.offsetWidth;
    const centreY = sheet.offsetTop + (envelope.top + envelope.height / 2) * sheet.offsetHeight;
    stage.scrollTo({
      left: centreX - stage.clientWidth / 2,
      top: centreY - stage.clientHeight / 2,
      behavior: "smooth",
    });
  }, [focusToken, envelope, zoom]);

  const pageCount = page?.pageCount ?? 0;
  const canGoBack = pageNumber !== null && pageNumber > 1;
  const canGoForward = pageNumber !== null && pageCount > 0 && pageNumber < pageCount;
  const printedLabel = page?.pageLabel ?? (onAnchoredPage ? group.printedPage : null);
  const caption = pageCaption(pageNumber, printedLabel, pageCount);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles["viewer-backdrop"]} />
        <Dialog.Content className={styles["viewer-dialog"]} aria-describedby={undefined}>
          <header className={styles["viewer-heading"]}>
            <div>
              <span className={styles["section-kicker"]}>
                {detail.publisher_name} &middot; {detail.source_version_label}
              </span>
              <Dialog.Title asChild>
                <h2>{detail.source_title}</h2>
              </Dialog.Title>
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label="Close the page viewer">
                <X size={18} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </header>

          <div className={styles["viewer-toolbar"]} role="group" aria-label="Page viewer controls">
            <div className={styles["toolbar-group"]}>
              <button
                type="button"
                onClick={() => setPageNumber((current) => (current ?? 1) - 1)}
                disabled={!canGoBack}
                aria-label="Previous page"
              >
                <ChevronLeft size={18} aria-hidden="true" />
              </button>
              <span className={styles["viewer-page-label"]}>{caption}</span>
              <button
                type="button"
                onClick={() => setPageNumber((current) => (current ?? 0) + 1)}
                disabled={!canGoForward}
                aria-label="Next page"
              >
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </div>

            <div className={styles["toolbar-group"]}>
              {onAnchoredPage ? null : (
                <button
                  type="button"
                  className={styles["viewer-return"]}
                  onClick={() => setPageNumber(anchoredPage)}
                >
                  <Undo2 size={15} aria-hidden="true" />
                  Back to the cited page
                </button>
              )}
              <button
                type="button"
                onClick={zoomToRegion}
                disabled={envelope === null}
                aria-label="Zoom to the anchored region"
              >
                <Crosshair size={17} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => setZoom((current) => Math.max(MINIMUM_ZOOM, current - ZOOM_STEP))}
                disabled={zoom <= MINIMUM_ZOOM}
                aria-label="Zoom out"
              >
                <Minus size={17} aria-hidden="true" />
              </button>
              <span className={styles["viewer-zoom-label"]}>{Math.round(zoom * 100)}%</span>
              <button
                type="button"
                onClick={() => setZoom((current) => Math.min(MAXIMUM_ZOOM, current + ZOOM_STEP))}
                disabled={zoom >= MAXIMUM_ZOOM}
                aria-label="Zoom in"
              >
                <Plus size={17} aria-hidden="true" />
              </button>
            </div>
          </div>

          {/* The stage scrolls, so a keyboard has to be able to enter it: at any zoom
              above the opening one, most of the page is outside the viewport, and a scroll
              region a keyboard cannot reach is a page a keyboard cannot read. */}
          <div
            className={styles["viewer-stage"]}
            ref={stageRef}
            tabIndex={0}
            role="group"
            aria-label="Source page"
          >
            <div
              className={styles["viewer-sheet"]}
              ref={sheetRef}
              style={{ width: `${zoom * 100}%` }}
            >
              <PageSurface
                activeAnchor={activeAnchor}
                ariaLabel={surfaceLabel(group, onAnchoredPage, page !== null, caption)}
                canShowContent={canShowContent}
                caption={caption}
                load={load}
                onRetry={reload}
                regions={group.regions}
                showRegions={onAnchoredPage}
              />
            </div>
          </div>

          <footer className={styles["viewer-footer"]}>
            <span>
              {group.label} &middot; {detail.source_version_label} (
              {detail.source_version_id})
            </span>
            <span>
              {onAnchoredPage
                ? `${group.regions.length} region${group.regions.length === 1 ? "" : "s"} recorded on this page`
                : "No region is recorded on this page; it is shown for context only."}
            </span>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function pageCaption(
  pageNumber: number | null,
  printedLabel: string | null,
  pageCount: number,
): string {
  if (pageNumber === null) return "Page not numbered";
  const ordinal = pageCount > 0 ? `PDF page ${pageNumber} of ${pageCount}` : `PDF page ${pageNumber}`;
  // The printed label is what the reader will find on the paper, and it is routinely not
  // the ordinal, so both are said rather than one standing in for the other.
  return printedLabel === null ? ordinal : `Printed page ${printedLabel} · ${ordinal}`;
}

function surfaceLabel(
  group: AnchorGroup,
  onAnchoredPage: boolean,
  rendered: boolean,
  caption: string,
): string {
  const surface = rendered
    ? "The source page is shown."
    : "The page is drawn as an empty frame; no source content is reproduced.";
  const marks = !onAnchoredPage
    ? "This is not the cited page, so no region is marked."
    : group.regions.length === 0
      ? "No region is drawn."
      : `${group.regions.length} marked region${group.regions.length === 1 ? "" : "s"} show where the passage sits.`;
  return `${caption}. ${group.primary.qualifiedLabel}. ${surface} ${marks}`;
}
