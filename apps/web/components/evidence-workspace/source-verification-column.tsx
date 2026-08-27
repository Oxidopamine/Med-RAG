"use client";

import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  CircleDashed,
  ExternalLink,
  FileSearch,
  Minus,
  Plus,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type {
  EvidenceProgressEvent,
  EvidenceRunLifecycle,
} from "@/components/evidence-workspace/use-evidence-run";
import { humanizeCode, primaryAnchor, renderPolicy } from "@/lib/evidence-presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type { EvidenceDetail, QuestionResult, QuestionStatus } from "@/lib/types";

import { AnchorViewer } from "./anchor-viewer";
import { PassageBody, SourceAnchorList } from "./source-anchor";
import styles from "./workspace.module.css";

const VERIFICATION_STEPS: Array<{
  activeLabel: string;
  completeLabel: string;
  label: string;
  statuses: QuestionStatus[];
}> = [
  {
    label: "Clinical context",
    activeLabel: "Extracting context",
    completeLabel: "Context extracted",
    statuses: ["CONTEXT_EXTRACTED"],
  },
  {
    label: "Evidence retrieval",
    activeLabel: "Retrieving and ranking",
    completeLabel: "Retrieval complete",
    statuses: ["RETRIEVING", "RERANKING"],
  },
  {
    label: "Counter-evidence",
    activeLabel: "Searching exceptions",
    completeLabel: "Counter-evidence checked",
    statuses: ["SEARCHING_COUNTER_EVIDENCE"],
  },
  {
    label: "Evidence completeness",
    activeLabel: "Checking completeness",
    completeLabel: "Completeness checked",
    statuses: ["CHECKING_EVIDENCE_COMPLETENESS"],
  },
  {
    label: "Final answer gate",
    activeLabel: "Running final gates",
    completeLabel: "Answer gate passed",
    statuses: ["VERIFYING"],
  },
];

interface SourceViewerProps {
  candidates: RankedEvidence[];
  evidence: EvidenceDetail[];
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}

/**
 * The reader's text size, kept across runs.
 *
 * It is an accessibility preference, not a per-run whim, and it was resetting on every
 * question. Reads are wrapped because a browser set to block site data throws here rather
 * than returning null.
 */
const TEXT_SCALE_KEY = "evidence-workspace.text-scale";

function storedTextScale(): number {
  if (typeof window === "undefined") return 100;
  try {
    const raw = Number(window.localStorage.getItem(TEXT_SCALE_KEY));
    return Number.isFinite(raw) && raw >= 90 && raw <= 140 ? raw : 100;
  } catch {
    return 100;
  }
}

export function SourceViewer({
  candidates,
  evidence,
  onSelectEvidence,
  selectedEvidenceId,
}: SourceViewerProps) {
  // Read in the initializer rather than an effect: this panel renders only once a result
  // exists, which never happens during the server render, so there is no markup for a
  // stored value to disagree with.
  const [textScale, setTextScale] = useState(storedTextScale);
  const railRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(TEXT_SCALE_KEY, String(textScale));
    } catch {
      // A browser that refuses storage still gets the control; it just does not persist.
    }
  }, [textScale]);
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
      const next = items[selectedIndex + step];
      if (!next) return;
      event.preventDefault();
      onSelectEvidence(next.detail.evidence_id);
      railRef.current?.querySelectorAll("button")[selectedIndex + step]?.focus();
    },
    [items, onSelectEvidence, selectedIndex],
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
  const positionLabel =
    candidateRank === null
      ? `Evidence ${selectedIndex + 1} of ${evidence.length}`
      : `Ranked passage ${selectedIndex - evidence.length + 1} of ${candidates.length}, uncited`;

  return (
    <section className={`${styles.panel} ${styles["source-viewer"]}`} aria-labelledby="source-heading">
      <div className={styles["panel-titlebar"]}>
        <div>
          <span className={styles["section-kicker"]}>Claim-linked provenance</span>
          <h2 id="source-heading">Source inspector</h2>
        </div>
        {candidateRank === null ? (
          <span className={styles["verified-source-label"]}>
            <Check size={14} strokeWidth={3} aria-hidden="true" />
            Canonical evidence
          </span>
        ) : (
          <span className={styles["uncited-source-label"]}>
            <AlertTriangle size={14} aria-hidden="true" />
            Retrieved, not cited
          </span>
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
            <ArrowUp size={19} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => onSelectEvidence(items[selectedIndex + 1]!.detail.evidence_id)}
            disabled={!canGoForward}
            aria-label="Next ranked result"
          >
            <ArrowDown size={19} aria-hidden="true" />
          </button>
          <span className={styles["evidence-position"]}>{positionLabel}</span>
          <span className={styles["rail-shortcut"]} aria-hidden="true">
            <kbd>j</kbd>
            <kbd>k</kbd>
          </span>
        </div>
        {/* A text-size control, not a page zoom: there is no page here to magnify. */}
        <div className={styles["toolbar-center"]}>
          <button
            type="button"
            onClick={() => setTextScale((current) => Math.max(90, current - 10))}
            disabled={textScale <= 90}
            aria-label="Decrease source text size"
          >
            <Minus size={18} aria-hidden="true" />
          </button>
          <span className={styles["text-size-label"]}>Text size</span>
          <button
            type="button"
            onClick={() => setTextScale((current) => Math.min(140, current + 10))}
            disabled={textScale >= 140}
            aria-label="Increase source text size"
          >
            <Plus size={18} aria-hidden="true" />
          </button>
        </div>
        <div className={styles["toolbar-group"]}>
          <a href={selected.source_url} target="_blank" rel="noreferrer">
            <ExternalLink size={17} aria-hidden="true" />
            Open source
          </a>
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
          {items.map(({ detail, retrievalRank }, index) => (
            <button
              className={`${styles["rail-item"]} ${retrievalRank === null ? "" : styles.candidate} ${detail.evidence_id === selected.evidence_id ? styles.selected : ""}`}
              type="button"
              key={detail.evidence_id}
              onClick={() => onSelectEvidence(detail.evidence_id)}
              aria-label={
                retrievalRank === null
                  ? `View evidence ${index + 1}: ${detail.source_title}`
                  : `View uncited ranked passage ${retrievalRank}: ${detail.source_title}`
              }
              aria-current={detail.evidence_id === selected.evidence_id ? "true" : undefined}
            >
              {/* Rank is the identity, in every row. The page or section is secondary
                  text beneath it, and uncited entries carry their own colour rather than
                  a third label vocabulary - three of those in a 66px column was a puzzle
                  rather than a wayfinder. */}
              <span className={styles["rail-rank"]}>{index + 1}</span>
              <span className={styles["rail-locator"]}>
                {locatorPage(detail) ?? (retrievalRank === null ? "cited" : "uncited")}
              </span>
            </button>
          ))}
        </div>

        {/* The stage scrolls, so it has to be reachable by keyboard on its own: the
            document it holds need not contain anything focusable, and a scroll region
            a keyboard cannot enter is content a keyboard cannot read. */}
        <div
          className={styles["document-stage"]}
          role="group"
          aria-label="Source excerpt"
          tabIndex={0}
        >
          <article className={styles["excerpt-sheet"]} style={{ fontSize: `${textScale}%` }}>
            <header className={styles["paper-heading"]}>
              <span>{selected.publisher_name}</span>
              <span>{selected.source_version_label}</span>
            </header>
            <h3>{selected.source_title}</h3>
            {selected.section_path.length ? (
              <p className={styles["paper-section"]}>{selected.section_path.join(" / ")}</p>
            ) : null}

            {candidateRank === null ? null : (
              <div className={styles["uncited-notice"]} role="note">
                <AlertTriangle size={19} aria-hidden="true" />
                <div>
                  <strong>Retrieved at rank {candidateRank}. No claim cites it.</strong>
                  <span>
                    This passage passed no claim gate and supports nothing rendered above.
                    Read it as background on what retrieval returned.
                  </span>
                </div>
              </div>
            )}

            <PassageBody detail={selected} variant="document" />

            <AnchorViewer detail={selected} key={selected.evidence_id} />

            <footer className={styles["paper-footer"]}>
              {/* Said plainly, because the surrounding chrome once implied otherwise:
                  this is text extracted from the source at the location named, not an
                  image of the page. */}
              <span>Verified excerpt &middot; {selected.evidence_id}</span>
              <span>{sourceLocation(selected)}</span>
            </footer>
          </article>
        </div>

        <SourceMetadata evidence={selected} />
      </div>

      <details className={styles["mobile-source-metadata"]}>
        <summary>Source details</summary>
        <SourceMetadata evidence={selected} mobile />
      </details>
    </section>
  );
}

function SourceMetadata({
  evidence,
  mobile = false,
}: {
  evidence: EvidenceDetail;
  mobile?: boolean;
}) {
  const policy = renderPolicy(evidence);
  return (
    <aside className={`${styles["document-metadata"]} ${mobile ? styles["metadata-mobile"] : ""}`}>
      <h3>Document</h3>
      <p>{evidence.source_title}</p>
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
          <dd>{evidence.source_version_label}</dd>
        </div>
        <div>
          <dt>Jurisdiction</dt>
          <dd>{evidence.jurisdiction}</dd>
        </div>
        <div>
          <dt>Section</dt>
          <dd>{evidence.section_path.join(" › ") || "Not supplied"}</dd>
        </div>
        <div>
          <dt>Page</dt>
          <dd>{locatorPage(evidence) ?? "Not supplied"}</dd>
        </div>
        <div>
          <dt>Highlight</dt>
          <dd>{highlightSummary(evidence, policy.canHighlightExactly)}</dd>
        </div>
        <div>
          <dt>Effective</dt>
          <dd>{dateRange(evidence.effective_from, evidence.effective_to)}</dd>
        </div>
        <div>
          <dt>Evidence roles</dt>
          <dd>{evidence.evidence_roles.map(humanizeCode).join(", ")}</dd>
        </div>
        <div>
          <dt>Evidence type</dt>
          <dd>
            {evidence.evidence_type ? humanizeCode(evidence.evidence_type) : "Not supplied"}
          </dd>
        </div>
        <div>
          <dt>Lifecycle</dt>
          <dd>{humanize(evidence.lifecycle_status)}</dd>
        </div>
      </dl>
      <div className={styles["anchor-block"]}>
        <h4>Source anchors</h4>
        <SourceAnchorList detail={evidence} />
      </div>
      <a href={evidence.source_url} target="_blank" rel="noreferrer">
        Open publisher source
        <ExternalLink size={15} aria-hidden="true" />
      </a>
    </aside>
  );
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

interface VerificationPanelProps {
  isRunning: boolean;
  lifecycle: EvidenceRunLifecycle;
  progressEvents: EvidenceProgressEvent[];
  result: QuestionResult | null;
  status: QuestionStatus | null;
}

export function VerificationPanel({
  isRunning,
  lifecycle,
  progressEvents,
  result,
  status,
}: VerificationPanelProps) {
  const badge = verificationBadge(result, lifecycle);

  return (
    <section className={`${styles.panel} ${styles["verification-panel"]}`} aria-labelledby="verification-heading">
      <div className={styles["verification-heading"]}>
        <div>
          <span className={styles["section-kicker"]}>Automated audit</span>
          <h2 id="verification-heading">Verification</h2>
        </div>
        <span
          className={`${styles["gate-badge"]} ${styles[badge.tone]}`}
          role="status"
          aria-live="polite"
        >
          {badge.label}
        </span>
      </div>

      <p className={styles["verification-summary"]}>{badge.message}</p>

      {/* Open on every state, including answer-ready. Five named gates and what each one
          concluded is the product's differentiator; collapsing it by default on the best
          case was the interface being modest about the thing that distinguishes it. */}
      <details className={styles["verification-details"]} open>
        <summary>Audit steps</summary>
        <ol className={styles["verification-timeline"]}>
          {VERIFICATION_STEPS.map((step, index) => {
            const state = verificationStepState({
              index,
              isRunning,
              lifecycle,
              progressEvents,
              result,
              status,
            });
            const event = progressEvents.findLast((progressEvent) =>
              step.statuses.includes(progressEvent.status),
            );
            const duration = stepDuration(progressEvents, index);
            return (
              <li className={styles[state]} key={step.label}>
                <div className={styles["step-track"]}>
                  <span className={styles["step-node"]} aria-hidden="true">
                    {state === "complete" ? (
                      <Check size={15} strokeWidth={3} />
                    ) : state === "currentStep" ? (
                      <CircleDashed className={styles.spin} size={15} />
                    ) : state === "blockedStep" ? (
                      <AlertTriangle size={15} />
                    ) : state === "skippedStep" ? (
                      "-"
                    ) : (
                      index + 1
                    )}
                  </span>
                </div>
                <strong>{index + 1}.</strong>
                <span>{stepLabel(step, state)}</span>
                <small title={event ? formatTime(event.occurredAt) : undefined}>
                  {duration ?? (event ? formatTime(event.occurredAt) : stepStateLabel(state))}
                </small>
              </li>
            );
          })}
        </ol>
      </details>

      {result ? (
        <div className={styles["run-metadata"]}>
          <span>Run ID</span>
          <code>{result.question_id}</code>
          <span>Updated</span>
          <time dateTime={result.updated_at}>{formatDateTime(result.updated_at)}</time>
        </div>
      ) : null}
    </section>
  );
}

function verificationStepState({
  index,
  isRunning,
  lifecycle,
  progressEvents,
  result,
  status,
}: {
  index: number;
  isRunning: boolean;
  lifecycle: EvidenceRunLifecycle;
  progressEvents: EvidenceProgressEvent[];
  result: QuestionResult | null;
  status: QuestionStatus | null;
}): "blockedStep" | "complete" | "currentStep" | "pendingStep" | "skippedStep" {
  const step = VERIFICATION_STEPS[index]!;
  const observed = progressEvents.some((event) => step.statuses.includes(event.status));
  const isFinal = index === VERIFICATION_STEPS.length - 1;

  if (isFinal && result?.status === "ANSWER_READY") return "complete";
  if (isFinal && result && ["ABSTAINED", "FAILED"].includes(result.status)) {
    return "blockedStep";
  }
  if (isRunning && status && step.statuses.includes(status)) return "currentStep";
  if (observed) return "complete";
  if (["abstained", "failed", "cancelled", "completed"].includes(lifecycle)) {
    return "skippedStep";
  }
  return "pendingStep";
}

function stepLabel(
  step: (typeof VERIFICATION_STEPS)[number],
  state: ReturnType<typeof verificationStepState>,
): string {
  if (state === "currentStep") return step.activeLabel;
  if (state === "complete") return step.completeLabel;
  return step.label;
}

/**
 * How long a stage took, from the events already on the wire.
 *
 * The gap between the previous stage's last event and this one's. The first stage has no
 * predecessor to measure from, so it reports nothing rather than timing from the run's
 * acceptance - the queue is not the stage.
 */
function stepDuration(events: EvidenceProgressEvent[], index: number): string | null {
  if (index === 0) return null;
  const step = VERIFICATION_STEPS[index]!;
  const end = events.findLast((event) => step.statuses.includes(event.status));
  if (!end) return null;
  const previous = VERIFICATION_STEPS[index - 1]!;
  const start = events.findLast((event) => previous.statuses.includes(event.status));
  if (!start) return null;
  const ms = Date.parse(end.occurredAt) - Date.parse(start.occurredAt);
  if (!Number.isFinite(ms) || ms < 0) return null;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function stepStateLabel(state: ReturnType<typeof verificationStepState>): string {
  if (state === "complete") return "Complete";
  if (state === "currentStep") return "In progress";
  if (state === "blockedStep") return "Blocked";
  if (state === "skippedStep") return "Skipped";
  return "Not started";
}

function verificationBadge(
  result: QuestionResult | null,
  lifecycle: EvidenceRunLifecycle,
): { label: string; message: string; tone: "neutral" | "passed" | "processing" | "withheld" } {
  if (lifecycle === "running") {
    return {
      label: "Checks in progress",
      message: "The system is evaluating context, source coverage, exceptions, and claim support.",
      tone: "processing",
    };
  }
  if (result?.status === "ANSWER_READY") {
    return {
      label: "Automated checks passed",
      message: `${result.verification_summary.supported_claims} rendered claim${result.verification_summary.supported_claims === 1 ? "" : "s"} passed the configured source and evidence gates.`,
      tone: "passed",
    };
  }
  if (result?.status === "ABSTAINED") {
    return {
      label: "Answer gate blocked",
      message: result.abstention?.message ?? "The evidence gate withheld the answer.",
      tone: "withheld",
    };
  }
  if (result?.status === "FAILED" || lifecycle === "failed") {
    return {
      label: "Review failed safely",
      message: "No clinical claim was rendered. Retry the review or inspect the error details.",
      tone: "withheld",
    };
  }
  if (lifecycle === "cancelled") {
    return {
      label: "Monitoring stopped",
      message: "This browser stopped waiting. Server processing may continue in the background.",
      tone: "neutral",
    };
  }
  return {
    label: "Not started",
    message: "Verification begins after a guideline question is submitted.",
    tone: "neutral",
  };
}

/** The compact page label used on the evidence rail, from the most precise anchor. */
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
