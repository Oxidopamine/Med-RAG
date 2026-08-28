"use client";

import { Crosshair, RotateCw, ScanLine, Unplug } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  DEFAULT_PAGE_DPI,
  getSourcePageImage,
  retainSourcePage,
  type SourcePageImage,
} from "@/lib/api";
import type { AnchorRegion } from "@/lib/evidence-presentation";

import styles from "./workspace.module.css";

/**
 * The rendered source page, and the state of asking for one.
 *
 * `unavailable` and `error` are kept apart all the way to the screen. A source whose
 * licence withholds page reproduction is the ordinary case - it is every WHO source in
 * the release today - and the view for it is complete: a located region with no page
 * behind it. A request that failed is not that. It is a fault, the reader should be told
 * it is a fault rather than shown a licence explanation for a dropped connection, and it
 * is the only one of the two worth offering a retry for.
 */
export type PageLoad =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; image: SourcePageImage }
  | { status: "unavailable" }
  | { status: "error"; message: string };

/**
 * Ask for one page, and report honestly what came back.
 *
 * The client caches and shares page requests, so this hook does not abort: a page a
 * reader moved away from is a page the cache now holds for their next visit. Staleness is
 * handled the only way it safely can be - by comparing the key the result came back for
 * against the key currently wanted, and discarding anything else.
 *
 * "Loading" is never written, only derived: a state that does not answer to the key
 * currently wanted *is* a page still in flight. That is why a stale image can never be
 * drawn under a new page's rectangle, and why the retry below - which changes the attempt
 * and nothing else - shows the frame loading again without a write of its own.
 */
export function useSourcePage(
  sourceId: string,
  pageNumber: number | null,
  enabled: boolean,
  dpi: number = DEFAULT_PAGE_DPI,
): { load: PageLoad; pageCount: number; reload: () => void } {
  const [state, setState] = useState<{
    key: string;
    load: PageLoad;
    source: string;
    pageCount: number;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const requestKey =
    enabled && pageNumber !== null ? `${sourceId}|${pageNumber}|${dpi}|${attempt}` : null;

  useEffect(() => {
    if (requestKey === null) return;
    let live = true;

    const pending = getSourcePageImage(sourceId, pageNumber!, { dpi });
    // Held for exactly as long as this hook can put the result on screen, which is not
    // the same span as the cache keeping it: `load` reports the image only while `state`
    // answers to the current key, and that is precisely this effect's lifetime. Without
    // the hold, paging far enough through a document evicts the page still being drawn
    // and revokes its blob URL underneath the `<img>`.
    const release = retainSourcePage(sourceId, pageNumber!, { dpi });

    void pending.then((result) => {
      if (!live) return;
      setState((previous) => ({
        key: requestKey,
        source: sourceId,
        load:
          result.status === "loaded"
            ? { status: "loaded", image: result.image }
            : result.status === "unavailable"
              ? { status: "unavailable" }
              : { status: "error", message: result.message },
        // Carried forward across pages of one document, and dropped the moment the source
        // changes. See the note on `pageCount` below.
        pageCount:
          result.status === "loaded"
            ? result.image.pageCount
            : previous?.source === sourceId
              ? previous.pageCount
              : 0,
      }));
    });

    return () => {
      live = false;
      release();
    };
  }, [requestKey, sourceId, pageNumber, dpi]);

  const reload = useCallback(() => setAttempt((count) => count + 1), []);
  const load: PageLoad =
    requestKey === null
      ? { status: "idle" }
      : state !== null && state.key === requestKey
        ? state.load
        : { status: "loading" };
  /*
   * How long the document is, which unlike the load survives a page turn.
   *
   * The count arrives with each page image, so reading it off the page in hand makes it
   * vanish for as long as the next page is in flight - and with it the bound that says
   * whether there is a next page, so the control that turns the page disables itself
   * halfway through turning it. It is a fact about the document, so it is kept against the
   * source and dropped as soon as that changes.
   */
  const pageCount = state !== null && state.source === sourceId ? state.pageCount : 0;

  return { load, pageCount, reload };
}

/** A rectangle as fractions of the page, or null when it cannot be placed on one. */
export interface PlacedRegion {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Reduce a recorded region to fractions of its page.
 *
 * A `PROPORTIONAL` bbox is already fractions and needs no page box, so it places with or
 * without an image. A bbox in the document's own points needs one, and gets it from the
 * rendered page - the same box the coordinates were measured against, which is why this
 * is a division and not a guess. Anything else stays unplaced.
 */
export function placeRegion(
  region: AnchorRegion | null,
  page: SourcePageImage | null,
): PlacedRegion | null {
  if (region === null) return null;
  if (region.placement === "PROPORTIONAL") {
    return {
      left: region.left,
      top: region.top,
      width: region.width,
      height: region.height,
    };
  }
  if (page === null) return null;
  const { pageWidth, pageHeight } = page;
  if (pageWidth <= 0 || pageHeight <= 0) return null;
  // Clamped, because a region that reaches past the page box would otherwise paint
  // outside the frame. A bbox that needs clamping is a bbox worth not trusting to the
  // pixel, but its page and its neighbourhood are still correct.
  const left = clampFraction(region.left / pageWidth);
  const top = clampFraction(region.top / pageHeight);
  return {
    left,
    top,
    width: clampFraction(region.width / pageWidth, 1 - left),
    height: clampFraction(region.height / pageHeight, 1 - top),
  };
}

function clampFraction(value: number, upper = 1): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(Math.max(value, 0), upper);
}

/**
 * A recorded fraction as a CSS length.
 *
 * Rounded to four decimal places, which is finer than any display can resolve and keeps
 * floating-point subtraction out of the rendered attribute.
 */
export function pageFraction(fraction: number): string {
  return `${Number((fraction * 100).toFixed(4))}%`;
}

/** The union of several placed regions, for scrolling one view onto all of them. */
export function regionEnvelope(regions: PlacedRegion[]): PlacedRegion | null {
  if (!regions.length) return null;
  const left = Math.min(...regions.map((region) => region.left));
  const top = Math.min(...regions.map((region) => region.top));
  const right = Math.max(...regions.map((region) => region.left + region.width));
  const bottom = Math.max(...regions.map((region) => region.top + region.height));
  return { left, top, width: right - left, height: bottom - top };
}

/** A recorded region, tagged with the anchor - and so the paragraph - it belongs to. */
export interface SurfaceRegion {
  anchorIndex: number;
  region: AnchorRegion;
}

interface PageSurfaceProps {
  /** The anchor whose paragraph the reader is currently on, drawn as the live region. */
  activeAnchor?: number | null;
  ariaLabel: string;
  /** Whether the licence permits the passage text, which decides how a region reads. */
  canShowContent: boolean;
  /** The page number caption drawn inside the frame. */
  caption: string;
  load: PageLoad;
  /**
   * Supplied only where the passage above is split into paragraphs this figure can point
   * back at. Its presence is what makes the regions controls rather than decoration, so
   * the frame stops being one `role="img"` and becomes a labelled group of them.
   */
  onSelectRegion?: (anchorIndex: number) => void;
  onRetry?: () => void;
  regions: SurfaceRegion[];
  /**
   * False once the reader has paged away from the anchored page. The regions belong to
   * one page, and drawing them over a different one would mark a place the record never
   * named - the single worst thing a provenance view can do.
   */
  showRegions: boolean;
}

/**
 * The page, with every region this record recorded on it.
 *
 * All of them, not one. A PDF evidence record is an entire page and carries one anchor
 * per text block, so marking a single block would understate the passage to whatever
 * fraction of the page that block happens to be. The first region carries the label and
 * the rest are outlined, because a tag on each of thirty is noise, not information.
 */
export function PageSurface({
  activeAnchor = null,
  ariaLabel,
  canShowContent,
  caption,
  load,
  onRetry,
  onSelectRegion,
  regions,
  showRegions,
}: PageSurfaceProps) {
  const page = load.status === "loaded" ? load.image : null;
  const placed = showRegions
    ? regions.flatMap(({ anchorIndex, region }) => {
        const box = placeRegion(region, page);
        return box === null ? [] : [{ anchorIndex, box }];
      })
    : [];
  const unplaceable = showRegions && regions.length > 0 && placed.length === 0;
  // Interactive only where the caller has paragraphs to send the reader to. A `role="img"`
  // hides everything inside it, so a frame holding controls must not claim to be one.
  const interactive = typeof onSelectRegion === "function" && placed.length > 0;

  return (
    <div
      className={`${styles["page-frame"]} ${page ? styles["page-frame-rendered"] : ""}`}
      role={interactive ? "group" : "img"}
      aria-label={ariaLabel}
      style={page ? { aspectRatio: `${page.pageWidth} / ${page.pageHeight}` } : undefined}
    >
      {page ? (
        /* A blob URL for one already-rasterised page. `next/image` optimises by
           refetching the source through its own pipeline, which cannot resolve a URL that
           exists only inside this document, and there is nothing left to optimise on a
           PNG the API just sized for us. Empty alt: the enclosing frame is the
           `role="img"`, and its label already describes the page and the region. */
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles["page-frame-image"]} src={page.objectUrl} alt="" />
      ) : null}

      <span className={styles["page-frame-number"]} aria-hidden="true">
        {caption}
      </span>

      {placed.map(({ anchorIndex, box }, index) => {
        const active = activeAnchor !== null && anchorIndex === activeAnchor;
        // The tag rides the live region when there is one, and the first otherwise, so a
        // page of thirty boxes carries one label rather than thirty.
        const tagged = activeAnchor === null ? index === 0 : active;
        const className = `${styles["page-region"]} ${canShowContent ? styles.readable : styles.withheld} ${active ? styles["page-region-active"] : ""}`;
        const style = {
          left: pageFraction(box.left),
          top: pageFraction(box.top),
          width: pageFraction(box.width),
          height: pageFraction(box.height),
        };
        const tag = tagged ? (
          <span className={styles["page-region-tag"]}>
            <Crosshair size={13} aria-hidden="true" />
            {canShowContent ? "Passage region" : "Anchored region"}
            {placed.length > 1 ? ` · ${index + 1} of ${placed.length}` : ""}
          </span>
        ) : null;

        return interactive ? (
          <button
            aria-label={`Paragraph ${index + 1} of ${placed.length} on this page`}
            aria-pressed={active}
            className={className}
            key={anchorIndex}
            onClick={() => onSelectRegion!(anchorIndex)}
            style={style}
            type="button"
          >
            {tag}
          </button>
        ) : (
          <span className={className} key={anchorIndex} style={style} aria-hidden="true">
            {tag}
          </span>
        );
      })}

      {load.status === "error" ? (
        <span className={styles["page-frame-error"]}>
          <Unplug size={20} aria-hidden="true" />
          {load.message}
          {onRetry ? (
            <button type="button" onClick={onRetry}>
              <RotateCw size={14} aria-hidden="true" />
              Try again
            </button>
          ) : null}
        </span>
      ) : unplaceable ? (
        <span className={styles["page-frame-note"]} aria-hidden="true">
          <ScanLine size={20} />
          Region recorded in source units; no page size to place it against
        </span>
      ) : showRegions && regions.length === 0 ? (
        <span className={styles["page-frame-note"]} aria-hidden="true">
          <ScanLine size={20} />
          No region recorded on this page
        </span>
      ) : null}
    </div>
  );
}
