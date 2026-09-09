import { CircleDashed, Clipboard, Clock3 } from "lucide-react";

import type { EvidenceProgressEvent } from "@/components/evidence-workspace/use-evidence-run";
import { STATUS_LABELS } from "@/lib/presentation";
import type { QuestionStatus } from "@/lib/types";

import styles from "./workspace.module.css";

interface RunProgressProps {
  onCopyRunId: () => void;
  progressEvents: EvidenceProgressEvent[];
  questionId: string | null;
  status: QuestionStatus | null;
}

export function RunProgress({
  onCopyRunId,
  progressEvents,
  questionId,
  status,
}: RunProgressProps) {
  return (
    <section className={`${styles.panel} ${styles["run-progress"]}`} aria-labelledby="run-progress-heading">
      <div className={styles["run-progress-icon"]} aria-hidden="true">
        <CircleDashed className={styles.spin} size={25} />
      </div>
      <div className={styles["run-progress-copy"]}>
        <h2 id="run-progress-heading">{status ? STATUS_LABELS[status] : "Starting review"}</h2>
        <p>{progressMessage(status)}</p>
        <div className={styles["run-progress-meta"]}>
          <span>
            <Clock3 size={15} aria-hidden="true" />
            {progressEvents.length} update{progressEvents.length === 1 ? "" : "s"} received
          </span>
        </div>
      </div>
      {questionId ? (
        <button type="button" onClick={onCopyRunId}>
          <Clipboard size={15} aria-hidden="true" />
          Copy run ID
        </button>
      ) : null}
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
  return "Each rendered claim is being checked against the configured evidence gates.";
}
