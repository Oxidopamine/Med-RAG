import type {
  EvidenceProgressEvent,
  EvidenceRunLifecycle,
} from "@/components/evidence-workspace/use-evidence-run";
import type { QuestionResult, QuestionStatus } from "@/lib/types";

import styles from "./workspace.module.css";

/**
 * The five checks.
 *
 * `label` is the short name the strip shows, sized to a fifth of the panel: "Completeness"
 * did not fit and was truncated to "Complete...". The log has the room for the full name,
 * which is what `completeLabel` and `activeLabel` carry.
 */
const VERIFICATION_STEPS: Array<{
  activeLabel: string;
  completeLabel: string;
  label: string;
  statuses: QuestionStatus[];
}> = [
  {
    label: "Context",
    activeLabel: "Reading context",
    completeLabel: "Context read",
    statuses: ["CONTEXT_EXTRACTED"],
  },
  {
    label: "Evidence",
    activeLabel: "Retrieving evidence",
    completeLabel: "Evidence retrieved",
    statuses: ["RETRIEVING", "RERANKING"],
  },
  {
    label: "Exceptions",
    activeLabel: "Searching for exceptions",
    completeLabel: "Exceptions searched",
    statuses: ["SEARCHING_COUNTER_EVIDENCE"],
  },
  {
    label: "Coverage",
    activeLabel: "Checking completeness",
    completeLabel: "Completeness checked",
    statuses: ["CHECKING_EVIDENCE_COMPLETENESS"],
  },
  {
    label: "Answer",
    activeLabel: "Checking the answer",
    completeLabel: "Answer checked",
    statuses: ["VERIFYING"],
  },
];

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
          <h2 id="verification-heading">Checks</h2>
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

      <ol className={styles["checks-strip"]}>
        {VERIFICATION_STEPS.map((step, index) => {
          const state = verificationStepState({
            index,
            isRunning,
            lifecycle,
            progressEvents,
            result,
            status,
          });
          return (
            <li className={`${styles.check} ${styles[state]}`} key={step.label}>
              <span className={styles["check-name"]}>{step.label}</span>
              <span className={styles["check-state"]}>{stepStateLabel(state)}</span>
            </li>
          );
        })}
      </ol>

      <details className={styles["checks-log"]}>
        <summary>Log</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Stage</th>
              <th scope="col">Started</th>
              <th scope="col">Duration</th>
              <th scope="col">Outcome</th>
            </tr>
          </thead>
          <tbody>
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
                <tr key={step.label}>
                  <td>{stepLabel(step, state)}</td>
                  <td data-tabular="">{event ? formatTime(event.occurredAt) : "—"}</td>
                  <td data-tabular="">{duration ?? "—"}</td>
                  <td>{stepStateLabel(state)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </details>
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
 * Measured from the run *entering* this stage to the run entering the next one. The
 * previous version measured from the previous stage's last event to this stage's last
 * event, which is not this stage at all: `RETRIEVING` is emitted when retrieval begins and
 * `CONTEXT_EXTRACTED` immediately before it, so retrieval reported the microseconds
 * between two adjacent writes - a confident, permanent `0ms` against a stage that had in
 * fact taken most of the run.
 *
 * A stage with no event after it is still in it, and reports nothing rather than timing
 * against `now` - a number that grows while a reader looks at it is not a duration. The
 * first stage also reports nothing: the only marker before `CONTEXT_EXTRACTED` is `QUEUED`,
 * and the gap between those is mostly time spent waiting, not extracting.
 */
function stepDuration(events: EvidenceProgressEvent[], index: number): string | null {
  if (index === 0) return null;
  const step = VERIFICATION_STEPS[index]!;
  const entered = events.findIndex((event) => step.statuses.includes(event.status));
  if (entered === -1) return null;
  const left = events
    .slice(entered + 1)
    .find((event) => !step.statuses.includes(event.status));
  if (!left) return null;

  const ms = Date.parse(left.occurredAt) - Date.parse(events[entered]!.occurredAt);
  if (!Number.isFinite(ms) || ms < 0) return null;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function stepStateLabel(state: ReturnType<typeof verificationStepState>): string {
  if (state === "complete") return "Passed";
  if (state === "currentStep") return "In progress";
  if (state === "blockedStep") return "No answer";
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
      label: "Checks passed",
      message: `${result.verification_summary.supported_claims} rendered claim${result.verification_summary.supported_claims === 1 ? "" : "s"} passed the configured source and evidence checks.`,
      tone: "passed",
    };
  }
  if (result?.status === "ABSTAINED") {
    return {
      label: "No answer",
      message: result.abstention?.message ?? "The evidence checks withheld the answer.",
      tone: "withheld",
    };
  }
  if (result?.status === "FAILED" || lifecycle === "failed") {
    return {
      label: "Review failed",
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
