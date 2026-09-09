"use client";

import { ExternalLink, FileSearch, Search, X } from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type { CitationIndex } from "@/lib/evidence-presentation";
import {
  anchorGroups,
  citationText,
  findMatchCount,
  passageSearchText,
  passageSegments,
  questionTerms,
} from "@/lib/document-inspection";
import {
  humanizeCode,
  locationSummary,
  primaryAnchor,
  renderPolicy,
  sourceAnchors,
} from "@/lib/evidence-presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type { EvidenceDetail } from "@/lib/types";

import { AnchorViewer } from "./anchor-viewer";
import { PassageBody, SourceAnchorList } from "./source-anchor";
import { TableNeighbourhood } from "./table-neighbourhood";
import styles from "./workspace.module.css";

interface SourceViewerProps {
  candidates: RankedEvidence[];
  /**
   * The claim this passage was cited for, and the question that was asked.
   *
   * Neither is displayed here. Both are read for the words worth pointing at inside the
   * passage, which is what makes a page-sized excerpt readable: the reader's actual task
   * is finding the two lines in it that bear on what they asked, and until something
   * marks them that is a manual scan of a wall of text.
   */
  claimText?: string | null;
  /** One numbering for the whole answer, so the rail can print the same reference. */
  citations: CitationIndex;
  evidence: EvidenceDetail[];
  /** Whether the inspector currently has the workspace to itself. */
  focusMode?: boolean;
  onPinEvidence?: (evidenceId: string | null) => void;
  onSelectEvidence: (evidenceId: string) => void;
  onToggleFocus?: () => void;
  /** The record held beside the selected one for comparison, already resolved. */
  pinnedEvidence?: EvidenceDetail | null;
  selectedEvidenceId: string | null;
}

export function SourceViewer({
  candidates,
  citations,
  claimText = null,
  evidence,
  focusMode = false,
  onPinEvidence,
  onSelectEvidence,
  onToggleFocus,
  pinnedEvidence = null,
  selectedEvidenceId,
}: SourceViewerProps) {
  /**
   * The passage search, and the passage it belongs to.
   *
   * The evidence ID is part of the state rather than something an effect watches. A
   * search is about the text in front of the reader, so when the selection moves the
   * search is *not* the current search any more - which is a fact the render can read off
   * the two IDs, with no write, no extra render, and no window in which a stale count is
   * on screen beside a new passage.
   */
  const [search, setSearch] = useState<{
    evidenceId: string | null;
    find: string;
    currentMatch: number;
  }>({ evidenceId: null, find: "", currentMatch: 0 });
  const [copied, setCopied] = useState(false);
  /** Whether the rail is narrowed to the records the search actually hit. */
  const [matchesOnly, setMatchesOnly] = useState(false);
  /**
   * The paragraph the reader is on, and the record it belongs to.
   *
   * Keyed by record for the same reason the search is: paragraph 4 of one page is not
   * paragraph 4 of the next, so the selection is read off the two IDs rather than cleared
   * by an effect that would briefly point at the wrong block.
   */
  const [anchorFocus, setAnchorFocus] = useState<{
    evidenceId: string | null;
    anchorIndex: number | null;
    /** Which side asked, so only a click on the page scrolls the passage. */
    origin: "passage" | "page";
  }>({ evidenceId: null, anchorIndex: null, origin: "passage" });
  const railRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const copyTimerRef = useRef<number | null>(null);

  // Cited evidence first, then the ranked passages nothing cited. Reading order is the
  // retrieval ranking, so stepping past the last citation is what the next ranked result
  // means here - but the two halves stay labelled apart everywhere they are shown.
  const items: Array<{ detail: EvidenceDetail; retrievalRank: number | null }> = useMemo(
    () => [
      ...evidence.map((detail) => ({ detail, retrievalRank: null })),
      ...candidates.map(({ detail, retrievalRank }) => ({ detail, retrievalRank })),
    ],
    [candidates, evidence],
  );
  const selectedIndex = Math.max(
    0,
    items.findIndex((item) => item.detail.evidence_id === selectedEvidenceId),
  );
  const selectedItem = items[selectedIndex] ?? null;
  const selected = selectedItem?.detail ?? null;
  const candidateRank = selectedItem?.retrievalRank ?? null;

  /**
   * Walk the ranking from the keyboard.
   *
   * Bound to the rail rather than the document: a global handler would swallow `j` and
   * `k` while someone is typing a question. Focus follows the selection so the reader can
   * keep pressing without reaching for the mouse between passages.
   */
  const walk = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const step = event.key === "j" ? 1 : event.key === "k" ? -1 : 0;
      if (step === 0 || event.metaKey || event.ctrlKey || event.altKey) return;
      // Walk what the rail is showing, not the full ranking behind it. With the matches
      // filter on those are different lists, and stepping into a record the rail is
      // hiding moves the selection somewhere the reader cannot see it.
      const visible = railRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? [];
      const here = [...visible].findIndex(
        (button) => button.getAttribute("aria-current") === "true",
      );
      const next = visible[here + step];
      if (here === -1 || !next) return;
      event.preventDefault();
      // Addressed by the record it carries rather than by position: the rail renders a
      // filtered list and the walk used to index it with a position from the unfiltered
      // one, which dropped focus off the end of the list and killed the keystroke.
      const evidenceId = next.dataset.evidenceId;
      if (evidenceId === undefined) return;
      onSelectEvidence(evidenceId);
      next.focus();
    },
    [onSelectEvidence],
  );

  const terms = useMemo(() => questionTerms(claimText), [claimText]);
  const selectedEvidenceKey = selected?.evidence_id ?? null;
  const find = search.evidenceId === selectedEvidenceKey ? search.find : "";
  // Counted over the text as it is rendered, not as it is stored: a workbook record is
  // shown as cell values without their `A146=` addresses, so counting over the
  // serialisation would report hits in text the reader is not being shown.
  const searchText = selected === null ? "" : passageSearchText(selected);
  const matchCount = useMemo(
    () => findMatchCount(passageSegments(searchText, { find })),
    [find, searchText],
  );
  // Wrapped at read time rather than clamped by an effect. Editing a search shortens the
  // match list under whatever position the reader had reached, and correcting that
  // afterwards puts an out-of-range index on screen for one frame.
  const currentMatch =
    matchCount === 0 || search.evidenceId !== selectedEvidenceKey
      ? 0
      : ((search.currentMatch % matchCount) + matchCount) % matchCount;

  /**
   * How many times the search hits each retrieved record, not merely the open one.
   *
   * The reader's question is nearly always "which of these mentions X", and answering it
   * by opening twelve passages in turn is the manual version of a search this already has
   * the text for - every passage is on the client the moment the result arrives.
   */
  const railMatches = useMemo(() => {
    const counts = new Map<string, number>();
    if (!find.trim()) return counts;
    for (const { detail } of items) {
      counts.set(
        detail.evidence_id,
        findMatchCount(passageSegments(passageSearchText(detail), { find })),
      );
    }
    return counts;
  }, [find, items]);
  const matchingRecords = [...railMatches.values()].filter((count) => count > 0).length;
  const activeAnchor =
    anchorFocus.evidenceId === selectedEvidenceKey ? anchorFocus.anchorIndex : null;

  // Only a click on the page moves the passage. Scrolling the paragraph a reader has just
  // clicked would yank the thing under their cursor away from it.
  useEffect(() => {
    if (anchorFocus.origin !== "page" || activeAnchor === null) return;
    const node = stageRef.current?.querySelector(`[data-anchor="${activeAnchor}"]`);
    if (node instanceof HTMLElement && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "center" });
    }
  }, [activeAnchor, anchorFocus.origin]);

  useEffect(() => {
    const node = stageRef.current?.querySelector('[data-find-current="true"]');
    // A passage with no match has nothing to scroll to, and jsdom implements neither the
    // method nor the layout it needs. Both are ordinary states rather than failures.
    if (node instanceof HTMLElement && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "center" });
    }
  }, [currentMatch, find, selectedEvidenceKey]);

  useEffect(
    () => () => {
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
    },
    [],
  );

  if (!selected) {
    return (
      <section className={`${styles.panel} ${styles["source-empty-panel"]}`} aria-labelledby="source-heading">
        <FileSearch size={27} aria-hidden="true" />
        <div>
          <h2 id="source-heading">Source inspector</h2>
          <p>A verified source passage will appear after a supported claim is selected.</p>
        </div>
      </section>
    );
  }

  const canGoBack = selectedIndex > 0;
  const canGoForward = selectedIndex < items.length - 1;
  /*
   * One number for one record.
   *
   * The rail numbers every retrieved record in ranking order, so a position that counted
   * within the cited half printed a different number from the row the reader had just
   * clicked - rail 6 against "ranked passage 2 of 5". What kind of record it is is not
   * lost by counting straight through: the badge above states it, and the rail marks the
   * boundary where citation stops.
   */
  const positionLabel = `Record ${selectedIndex + 1} of ${items.length}`;
  const quotable = citationText(selected, locationSummary(selected));
  const tableGroup = anchorGroups(selected).find((group) => group.form === "TABLE_ROW") ?? null;
  const comparing = pinnedEvidence !== null && pinnedEvidence.evidence_id !== selected.evidence_id;
  const pinned = pinnedEvidence !== null && pinnedEvidence.evidence_id === selected.evidence_id;
  // The open record is never filtered out from under the reader, whatever it matches.
  const railItems =
    matchesOnly && find.trim()
      ? items.filter(
          ({ detail }) =>
            detail.evidence_id === selected.evidence_id ||
            (railMatches.get(detail.evidence_id) ?? 0) > 0,
        )
      : items;

  function selectAnchor(anchorIndex: number, origin: "passage" | "page") {
    setAnchorFocus({ evidenceId: selectedEvidenceKey, anchorIndex, origin });
  }

  function stepMatch(step: number) {
    if (matchCount === 0) return;
    setSearch((current) => ({
      ...current,
      evidenceId: selectedEvidenceKey,
      currentMatch: currentMatch + step,
    }));
  }

  async function copyQuote() {
    if (quotable === null) return;
    try {
      await navigator.clipboard.writeText(quotable);
      setCopied(true);
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => setCopied(false), 2500);
    } catch {
      // A browser that refuses clipboard access leaves the passage on screen to select by
      // hand, which is what the reader would have done without the button.
      setCopied(false);
    }
  }

  return (
    <section className={`${styles.panel} ${styles["source-viewer"]}`} aria-labelledby="source-heading">
      <div className={styles["panel-titlebar"]}>
        <div>
          {/* Focusable only programmatically: "Inspect evidence" moves the reader here, and
              a keyboard user needs to arrive with their focus rather than watch the page
              scroll away from it. Not in the tab order - it is a heading. */}
          <h2 id="source-heading" tabIndex={-1}>
            Sources
          </h2>
        </div>
        {candidateRank === null ? (
          <span className={styles["verified-source-label"]}>Cited in this answer</span>
        ) : (
          <span className={styles["uncited-source-label"]}>Not cited</span>
        )}
      </div>

      {/* `group`, not `toolbar`: a toolbar promises roving arrow-key navigation this
          does not implement, and a role that overstates the keyboard contract is worse
          than the plain grouping the controls actually form. */}
      <div className={styles["source-toolbar"]} role="group" aria-label="Source inspector controls">
        <div className={styles["toolbar-group"]}>
          <button
            type="button"
            onClick={() => onSelectEvidence(items[selectedIndex - 1]!.detail.evidence_id)}
            disabled={!canGoBack}
            aria-label="Previous ranked result"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => onSelectEvidence(items[selectedIndex + 1]!.detail.evidence_id)}
            disabled={!canGoForward}
            aria-label="Next ranked result"
          >
            Next
          </button>
          <span className={styles["evidence-position"]}>{positionLabel}</span>
        </div>
        {/* A passage here is routinely a whole page of text or a twenty-cell row, so
            finding a word in it is a navigation problem rather than a convenience. */}
        <div className={styles["toolbar-find"]}>
          <span className={styles["find-field"]}>
            <Search size={15} aria-hidden="true" />
            <input
              type="search"
              value={find}
              onChange={(event) =>
                setSearch({
                  evidenceId: selectedEvidenceKey,
                  find: event.target.value,
                  currentMatch: 0,
                })
              }
              placeholder="Find in passage"
              aria-label="Find in passage"
            />
          </span>
          {find.trim() ? (
            <>
              <span className={styles["find-count"]} role="status">
                {matchCount === 0 ? "No matches" : `${currentMatch + 1} of ${matchCount}`}
              </span>
              <button
                type="button"
                onClick={() => stepMatch(-1)}
                disabled={matchCount === 0}
                aria-label="Previous match"
              >
                Previous match
              </button>
              <button
                type="button"
                onClick={() => stepMatch(1)}
                disabled={matchCount === 0}
                aria-label="Next match"
              >
                Next match
              </button>
              <button
                type="button"
                onClick={() =>
                  setSearch({ evidenceId: selectedEvidenceKey, find: "", currentMatch: 0 })
                }
                aria-label="Clear the search"
              >
                Clear
              </button>
              {/* The search runs over every retrieved record, not just the open one, so
                  it can answer "which of these mentions X" - which is the question a
                  reader with twelve passages actually has. */}
              <button
                aria-pressed={matchesOnly}
                className={matchesOnly ? styles.selected : ""}
                onClick={() => setMatchesOnly((current) => !current)}
                type="button"
              >
                {matchingRecords} of {items.length} records
              </button>
            </>
          ) : null}
        </div>

        <div className={styles["toolbar-end"]}>
          <div className={styles["toolbar-group"]}>
            {/* Offered only where the licence permits the passage. What leaves this
                interface leaves it with its edition, its location and its research-use
                notice attached, because it is going somewhere nothing can annotate it. */}
            {quotable === null ? null : (
              <button type="button" onClick={() => void copyQuote()}>
                {copied ? "Copied" : "Copy quote"}
              </button>
            )}
            {/* Two passages that disagree are the one thing this workspace could not
                show at once, in the panel whose whole purpose is a comparison. */}
            {onPinEvidence ? (
              <button
                aria-pressed={pinned || comparing}
                onClick={() => onPinEvidence(comparing || pinned ? null : selected.evidence_id)}
                type="button"
              >
                {comparing ? "Stop comparing" : pinned ? "Unpin" : "Compare"}
              </button>
            ) : null}
            <a href={selected.source_url} target="_blank" rel="noreferrer">
              Open source
              <span className={styles.srOnly}> (opens in a new tab)</span>
            </a>
            {onToggleFocus ? (
              <button
                aria-pressed={focusMode}
                onClick={onToggleFocus}
                type="button"
                aria-label={focusMode ? "Leave focus mode" : "Give the inspector the full width"}
              >
                {focusMode ? "Exit focus" : "Focus"}
              </button>
            ) : null}
          </div>
        </div>
      </div>

      <div className={styles["viewer-layout"]}>
        <div
          className={styles["evidence-rail"]}
          ref={railRef}
          role="group"
          aria-label="Retrieved records"
          aria-keyshortcuts="j k"
          onKeyDown={walk}
        >
          {railItems.map(({ detail, retrievalRank }, index) => {
            const rank = items.findIndex((item) => item.detail.evidence_id === detail.evidence_id);
            const reference = citations.byEvidenceId.get(detail.evidence_id)?.number ?? null;
            const matches = railMatches.get(detail.evidence_id) ?? 0;
            const superseded = detail.lifecycle_status !== "CURRENT";
            const previous = railItems[index - 1];
            return (
              <Fragment key={detail.evidence_id}>
                {/* Where citation stops, stated once, instead of the word "uncited"
                    printed down every row below it. Read off the transition rather than a
                    fixed index, so it stays correct when the search narrows the rail. */}
                {retrievalRank !== null && (index === 0 || previous!.retrievalRank === null) &&
                index > 0 ? (
                  <span className={styles["rail-divider"]} aria-hidden="true">
                    Not cited
                  </span>
                ) : null}
                <button
                  className={`${styles["rail-item"]} ${retrievalRank === null ? "" : styles.candidate} ${detail.evidence_id === selected.evidence_id ? styles.selected : ""}`}
                  type="button"
                  data-evidence-id={detail.evidence_id}
                  onClick={() => onSelectEvidence(detail.evidence_id)}
                  aria-label={railLabel(detail, rank, reference, retrievalRank, superseded, matches, find)}
                  aria-current={detail.evidence_id === selected.evidence_id ? "true" : undefined}
                >
                  {/* Rank identifies the row; the title says which document and the
                      locator, on its own line under it, says where in that document. The
                      reference number is what the claim above prints, so carrying it here
                      is what lets a reader move between the two without re-reading titles -
                      which is the whole reason the second copy of this list, in the other
                      column, could go. */}
                  <span className={styles["rail-rank"]}>{rank + 1}</span>
                  <span className={styles["rail-copy"]}>
                    <span className={styles["rail-source"]}>{detail.source_title}</span>
                    <span className={styles["rail-locator"]}>{railLocator(detail)}</span>
                    {reference !== null || superseded ? (
                      <span className={styles["rail-flags"]}>
                        {reference !== null ? <span>[{reference}]</span> : null}
                        {/* An edition no longer in force is the one fact worth knowing
                            before opening a passage, and it used to take three clicks. */}
                        {superseded ? (
                          <span className={styles["rail-superseded"]}>
                            {humanize(detail.lifecycle_status)}
                          </span>
                        ) : null}
                      </span>
                    ) : null}
                  </span>
                  {find.trim() ? (
                    <span
                      className={`${styles["rail-match"]} ${matches === 0 ? styles.empty : ""}`}
                      aria-hidden="true"
                    >
                      {matches}
                    </span>
                  ) : null}
                </button>
              </Fragment>
            );
          })}
        </div>

        {/* No longer its own scroll region: the source column scrolls, and a document
            inside a scrolling panel inside a scrolling page was one context too many for
            a reader to keep track of with a wheel. */}
        <div
          className={`${styles["document-stage"]} ${comparing ? styles["stage-comparing"] : ""}`}
          ref={stageRef}
        >
          {comparing ? (
            <ComparePair
              highlight={{ terms, find, currentMatch }}
              left={selected}
              onSelectEvidence={onSelectEvidence}
              onStop={() => onPinEvidence?.(null)}
              right={pinnedEvidence!}
            />
          ) : (
          <article className={styles.reader}>
            <h3>{selected.source_title}</h3>
            <p className={styles["reader-meta"]}>
              {selected.publisher_name} &middot; {selected.source_version_label} &middot;{" "}
              {sourceLocation(selected)}
              {selected.effective_from || selected.effective_to
                ? ` · ${dateRange(selected.effective_from, selected.effective_to)}`
                : ""}
            </p>

            {candidateRank === null ? null : (
              <p className={styles["uncited-notice"]} role="note">
                <strong>No claim in this answer cites this passage.</strong> Retrieved at
                rank {candidateRank}.
              </p>
            )}

            {selected.section_path.length ? (
              <p className={styles["paper-section"]}>{selected.section_path.join(" / ")}</p>
            ) : null}

            <div className={`${styles.paper} reading`}>
              <PassageBody
                activeAnchor={activeAnchor}
                detail={selected}
                highlight={{ terms, find, currentMatch }}
                onSelectBlock={(anchorIndex) => selectAnchor(anchorIndex, "passage")}
              />
            </div>

            {/* Directly under the passage, rather than below the location figure. A
                reader checking a decision table wants the rows either side of the one
                they are reading, at the moment they are reading it. Keyed apart from the
                figure: two static siblings carrying one key are duplicates as far as React
                is concerned. */}
            {tableGroup ? (
              <TableNeighbourhood
                detail={selected}
                group={tableGroup}
                key={`neighbourhood-${selected.evidence_id}`}
              />
            ) : null}

            <AnchorViewer
              activeAnchor={activeAnchor}
              detail={selected}
              key={`anchor-${selected.evidence_id}`}
              onSelectAnchor={(anchorIndex) => selectAnchor(anchorIndex, "page")}
            />

            <details className={styles["reader-details"]}>
              <summary>Details</summary>
              <SourceMetadata evidence={selected} />
              <p>Verified excerpt &middot; {selected.evidence_id}</p>
            </details>
          </article>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * Two passages, side by side, and nothing else.
 *
 * The conflict panel names two records that disagree and the workspace could only ever
 * show one of them - which is the wrong affordance for the one section whose entire
 * purpose is a comparison. Both sides are cut to what a comparison needs: the text, the
 * edition, the dates and the location. The page figure, the neighbourhood and the
 * colophon are not on either side, because a reader comparing two clauses is not
 * simultaneously auditing either one's provenance chain, and two of everything would bury
 * the thing being compared.
 */
function ComparePair({
  highlight,
  left,
  onSelectEvidence,
  onStop,
  right,
}: {
  highlight: { terms: string[]; find: string; currentMatch: number };
  left: EvidenceDetail;
  onSelectEvidence: (evidenceId: string) => void;
  onStop: () => void;
  right: EvidenceDetail;
}) {
  return (
    <>
      {[left, right].map((detail, index) => (
        <article
          className={`${styles["excerpt-sheet"]} ${styles["compare-sheet"]}`}
          key={detail.evidence_id}
        >
          <header className={styles["paper-heading"]}>
            <span>{index === 0 ? "Open" : "Pinned"}</span>
            {/* "Unpin", not a second "Stop comparing": the toolbar already carries that
                wording, and two controls with one name are two controls a reader has to
                tell apart before using either. */}
            {index === 0 ? null : (
              <button className={styles["compare-close"]} onClick={onStop} type="button">
                <X size={14} aria-hidden="true" />
                Unpin
              </button>
            )}
          </header>
          <h3>{detail.source_title}</h3>
          <p className={styles["paper-section"]}>
            {detail.publisher_name} &middot; {detail.source_version_label} &middot;{" "}
            {humanize(detail.lifecycle_status)} &middot; {dateRange(detail.effective_from, detail.effective_to)}
          </p>

          <PassageBody detail={detail} highlight={highlight} />

          <footer className={styles["paper-footer"]}>
            <span>{locationSummary(detail)}</span>
            {index === 0 ? (
              <span>{detail.evidence_id}</span>
            ) : (
              <button
                className={styles["compare-close"]}
                onClick={() => onSelectEvidence(detail.evidence_id)}
                type="button"
              >
                Open this one
              </button>
            )}
          </footer>
        </article>
      ))}
    </>
  );
}

/**
 * What a rail row says, for a reader who cannot see it.
 *
 * Everything the row shows visually plus what it shows by colour and position - whether
 * the record is cited, where citation stopped, and how many times the current search hit
 * it. A rail that conveys those three by tint alone conveys them to nobody using it by
 * ear.
 */
function railLabel(
  detail: EvidenceDetail,
  rank: number,
  reference: number | null,
  retrievalRank: number | null,
  superseded: boolean,
  matches: number,
  find: string,
): string {
  const kind =
    retrievalRank === null
      ? `Record ${rank + 1}${reference === null ? "" : `, reference ${reference}`}`
      : `Record ${rank + 1}, retrieved at rank ${retrievalRank}, cited by no claim`;
  const edition = superseded ? `, ${humanize(detail.lifecycle_status).toLowerCase()} edition` : "";
  const hits = find.trim()
    ? `, ${matches} search match${matches === 1 ? "" : "es"}`
    : "";
  return `${kind}: ${detail.source_title}, ${railLocator(detail)}${edition}${hits}`;
}

/**
 * Everything the release records about this passage's source, at the foot of the sheet.
 *
 * It used to be a 232px column beside the document, which is where it read as junk: a
 * fixed third of the panel spent wrapping `WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_B` across
 * four lines and repeating the publisher and title printed six centimetres above it. In
 * the sheet it is a colophon - the same facts, one column narrower than the passage is
 * wide, laid out where a document's imprint belongs, and no longer competing with the
 * thing the panel exists to show.
 */
function SourceMetadata({ evidence }: { evidence: EvidenceDetail }) {
  const policy = renderPolicy(evidence);
  return (
    <aside className={styles["document-metadata"]} aria-label="Record provenance">
      <h3>About this record</h3>
      {/* The single home for this record's provenance. The claim-linked panel carries the
          passage and the licence decision; everything that identifies and dates the
          source is stated here once. */}
      <dl>
        <div>
          <dt>Publisher</dt>
          <dd>{evidence.publisher_name}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd className={styles["value-identifier"]}>{evidence.source_version_label}</dd>
        </div>
        <div>
          {/* The identifier, not only the label: two editions can share a label and a
              page, and this is what tells a reader which one the passage came from. */}
          <dt>Version ID</dt>
          <dd className={styles["value-identifier"]}>{evidence.source_version_id}</dd>
        </div>
        <div>
          <dt>Jurisdiction</dt>
          <dd>{evidence.jurisdiction}</dd>
        </div>
        <div>
          <dt>Section</dt>
          <Value>{evidence.section_path.join(" › ") || "Not supplied"}</Value>
        </div>
        <div>
          <dt>Page</dt>
          <Value>{locatorPage(evidence) ?? "Not supplied"}</Value>
        </div>
        <div>
          <dt>Highlight</dt>
          <Value>{highlightSummary(evidence, policy.canHighlightExactly)}</Value>
        </div>
        <div>
          <dt>Effective</dt>
          <Value>{dateRange(evidence.effective_from, evidence.effective_to)}</Value>
        </div>
        <div>
          <dt>Evidence roles</dt>
          <dd>{evidence.evidence_roles.map(humanizeCode).join(", ")}</dd>
        </div>
        <div>
          <dt>Evidence type</dt>
          <Value>
            {evidence.evidence_type ? humanizeCode(evidence.evidence_type) : "Not supplied"}
          </Value>
        </div>
        <div>
          <dt>Lifecycle</dt>
          <dd>{humanize(evidence.lifecycle_status)}</dd>
        </div>
      </dl>
      <AnchorBlock evidence={evidence} />
      <a href={evidence.source_url} target="_blank" rel="noreferrer">
        Open publisher source
        <ExternalLink size={15} aria-hidden="true" />
      </a>
    </aside>
  );
}

/**
 * Every locator the release carried for this passage.
 *
 * A DAK record anchors a whole spreadsheet row, which is thirteen cell locators that
 * differ from one another in one character. Listed flat they are a wall of near-identical
 * lines at the foot of the sheet, and the one a reader actually wants - the most precise
 * one - is already drawn above under "Location in source". Past a handful the list folds
 * away behind its own count: still one row per locator, still nothing dropped, just not
 * unrolled by default.
 */
function AnchorBlock({ evidence }: { evidence: EvidenceDetail }) {
  const total = sourceAnchors(evidence).length;
  if (total <= ANCHOR_LIST_LIMIT) {
    return (
      <div className={styles["anchor-block"]}>
        <h4>Source anchors</h4>
        <SourceAnchorList detail={evidence} />
      </div>
    );
  }
  return (
    <details className={`${styles["anchor-block"]} ${styles["anchor-block-folded"]}`}>
      <summary>
        Source anchors
        <span>{total} locators</span>
      </summary>
      <SourceAnchorList detail={evidence} />
    </details>
  );
}

/** Above this many locators the list is folded; see `AnchorBlock`. */
const ANCHOR_LIST_LIMIT = 4;

/**
 * A provenance value, dimmed when the release carried none.
 *
 * "Not supplied" is a finding, not a blank: the corpus recorded no section, no page, no
 * date. It stays on the page for that reason and is set quieter than the facts beside it
 * so a reader scanning the block lands on what *was* recorded first.
 */
function Value({ children }: { children: string }) {
  const missing = /^(Not supplied|Not available|Dates not supplied)$/.test(children);
  return <dd className={missing ? styles["value-missing"] : ""}>{children}</dd>;
}

/**
 * What the interface can do with this record's exact location.
 *
 * A region the release recorded but the licence withholds is reported as withheld, not
 * as absent: the provenance exists, and saying "not available" would understate what
 * was verified while overstating what changes if the licence review lands.
 */
function highlightSummary(evidence: EvidenceDetail, canHighlight: boolean): string {
  if (canHighlight) return "Exact location verified";
  const anchor = primaryAnchor(evidence);
  if (anchor?.highlightSuppressedByLicence) return "Recorded, withheld by licence";
  return "Not available";
}

/**
 * The one line that distinguishes a rail row from the row above it.
 *
 * A page where there is one, a cell address where the anchor is a table cell, and the
 * locator kind otherwise. The old rail fell back to the words "cited" and "uncited",
 * which every row in a group shares and which the group's own colour already carries.
 */
function railLocator(evidence: EvidenceDetail): string {
  const anchor = primaryAnchor(evidence);
  if (!anchor) return "No location";
  if (anchor.placement.form === "TABLE_CELL") {
    return anchor.placement.cell === null
      ? "Table cell"
      : `Cell ${anchor.placement.cell.columnLetter}${anchor.placement.cell.rowNumber}`;
  }
  return locatorPage(evidence) ?? humanize(anchor.kind);
}

/** The compact page label used wherever a record is reduced to its page, if it has one. */
function locatorPage(evidence: EvidenceDetail): string | null {
  const anchor = primaryAnchor(evidence);
  if (!anchor) return null;
  if (anchor.printedPage !== null) return `p. ${anchor.printedPage}`;
  if (anchor.pdfPage !== null) return `PDF p. ${anchor.pdfPage}`;
  return null;
}

function sourceLocation(evidence: EvidenceDetail): string {
  return locatorPage(evidence) ?? humanize(primaryAnchor(evidence)?.kind ?? "source location");
}

function humanize(value: string): string {
  const words = value.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function dateRange(start: string | null, end: string | null): string {
  if (!start && !end) return "Dates not supplied";
  if (start && end) return `${formatDay(start)}–${formatDay(end)}`;
  if (start) return `From ${formatDay(start)}`;
  return `Until ${formatDay(end!)}`;
}

function formatDay(value: string): string {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}
