"use client";

import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  CircleDashed,
  ExternalLink,
  FileSearch,
  FileText,
  Minus,
  Plus,
} from "lucide-react";
import { useState } from "react";

import type {
  EvidenceProgressEvent,
  EvidenceRunLifecycle,
} from "@/components/evidence-workspace/use-evidence-run";
import { primaryAnchor, renderPolicy } from "@/lib/evidence-presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type { EvidenceDetail, QuestionResult, QuestionStatus } from "@/lib/types";

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

export function SourceViewer({
  candidates,
  evidence,
  onSelectEvidence,
  selectedEvidenceId,
}: SourceViewerProps) {
  const [zoom, setZoom] = useState(100);
  // Cited evidence first, then the ranked passages nothing cited. Reading order is the
  // retrieval ranking, so stepping past the last citation is what the next ranked result
  // means here - but the two halves stay labelled apart everywhere they are shown.
  const items: Array<{ detail: EvidenceDetail; retrievalRank: number | null }> = [
    ...evidence.map((detail) => ({ detail, retrievalRank: null })),
    ...candidates.map(({ detail, retrievalRank }) => ({ detail, retrievalRank })),
  ];
  const selectedIndex = Math.max(
    0,
    items.findIndex((item) => item.detail.evidence_id === selectedEvidenceId),
  );
  const selectedItem = items[selectedIndex] ?? null;
  const selected = selectedItem?.detail ?? null;
  const candidateRank = selectedItem?.retrievalRank ?? null;

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

      <div className={styles["pdf-toolbar"]} aria-label="Source inspector controls">
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
        </div>
        <div className={styles["toolbar-center"]}>
          <button
            type="button"
            onClick={() => setZoom((current) => Math.max(80, current - 10))}
            disabled={zoom <= 80}
            aria-label="Decrease source text size"
          >
            <Minus size={18} aria-hidden="true" />
          </button>
          <span className={styles["zoom-control"]}>{zoom}%</span>
          <button
            type="button"
            onClick={() => setZoom((current) => Math.min(140, current + 10))}
            disabled={zoom >= 140}
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
        <div className={styles.thumbnails} aria-label="Ranked results">
          {items.map(({ detail, retrievalRank }, index) => (
            <button
              className={`${styles.thumbnail} ${retrievalRank === null ? "" : styles.candidate} ${detail.evidence_id === selected.evidence_id ? styles.selected : ""}`}
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
              <FileText size={22} aria-hidden="true" />
              <span>
                {locatorPage(detail) ?? (retrievalRank === null ? index + 1 : `#${retrievalRank}`)}
              </span>
            </button>
          ))}
        </div>

        <div className={styles["document-stage"]}>
          <article className={styles["document-paper"]} style={{ fontSize: `${zoom}%` }}>
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

            <footer className={styles["paper-footer"]}>
              <span>{selected.evidence_id}</span>
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
          <dt>Page</dt>
          <dd>{locatorPage(evidence) ?? "Not supplied"}</dd>
        </div>
        <div>
          <dt>Highlight</dt>
          <dd>{highlightSummary(evidence, policy.canHighlightExactly)}</dd>
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
        View publisher source
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
  const isCompleted = result?.status === "ANSWER_READY";

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

      <details className={styles["verification-details"]} open={isRunning || !isCompleted}>
        <summary>{isCompleted ? "View audit steps" : "Audit steps"}</summary>
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
            const event = [...progressEvents]
              .reverse()
              .find((progressEvent) => step.statuses.includes(progressEvent.status));
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
                <small>{event ? formatTime(event.occurredAt) : stepStateLabel(state)}</small>
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

/** The compact page label used on thumbnails, from the most precise anchor available. */
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
