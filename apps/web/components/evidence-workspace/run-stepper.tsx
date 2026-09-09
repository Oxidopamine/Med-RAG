import { VerificationPanel } from "@/components/evidence-workspace/checks-strip";
import type {
  EvidenceProgressEvent,
  EvidenceRunLifecycle,
} from "@/components/evidence-workspace/use-evidence-run";
import { STATUS_LABELS } from "@/lib/presentation";
import type { QuestionStatus } from "@/lib/types";

import styles from "./workspace.module.css";

interface RunStepperProps {
  lifecycle: EvidenceRunLifecycle;
  onCopyRunId: () => void;
  progressEvents: EvidenceProgressEvent[];
  questionId: string | null;
  status: QuestionStatus | null;
}

export function RunStepper({
  lifecycle,
  onCopyRunId,
  progressEvents,
  questionId,
  status,
}: RunStepperProps) {
  return (
    <section className={`${styles.panel} ${styles["run-stepper"]}`} aria-labelledby="run-progress-heading">
      <h2 id="run-progress-heading">{status ? STATUS_LABELS[status] : "Starting review"}</h2>

      <VerificationPanel
        isRunning
        lifecycle={lifecycle}
        progressEvents={progressEvents}
        result={null}
        status={status}
      />

      <div className={styles.skeleton} aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </div>

      <p>
        {progressMessage(status)}
        {questionId ? (
          <button type="button" onClick={onCopyRunId}>
            Copy run ID
          </button>
        ) : null}
      </p>
    </section>
  );
}

function progressMessage(status: QuestionStatus | null): string {
  if (!status || status === "QUEUED") {
    return "The request was accepted and is waiting for the evidence pipeline.";
  }
  if (status === "CONTEXT_EXTRACTED") {
    return "Clinical facts were interpreted. Source retrieval will begin next.";
  }
  if (status === "RETRIEVING" || status === "RERANKING") {
    return "Approved sources are being retrieved and ranked for this question.";
  }
  if (status === "SEARCHING_COUNTER_EVIDENCE") {
    return "The review is looking for exceptions, contraindications, and conflicting guidance.";
  }
  if (status === "CHECKING_EVIDENCE_COMPLETENESS") {
    return "Required support, applicability, and exception evidence are being checked.";
  }
  return "Each rendered claim is being checked against the configured evidence checks.";
}
