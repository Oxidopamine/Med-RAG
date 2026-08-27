import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Clipboard,
  Download,
  FileLock2,
} from "lucide-react";

import { buildCitations, isFullyLicenceRestricted } from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

import { AbstentionNotice } from "./abstention-notice";
import { ClaimList } from "./claim-list";
import styles from "./workspace.module.css";

interface AnswerPanelProps {
  isPrevious?: boolean;
  onCopyAnswer: () => void;
  onExportAudit: () => void;
  onRetry: () => void;
  onSelectClaim: (claimId: string) => void;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}

export function AnswerPanel({
  isPrevious = false,
  onCopyAnswer,
  onExportAudit,
  onRetry,
  onSelectClaim,
  onSelectEvidence,
  result,
  selectedClaimId,
  selectedEvidenceId,
}: AnswerPanelProps) {
  const isReady = result.status === "ANSWER_READY";
  const citations = buildCitations(result);
  const licenceRestricted = isFullyLicenceRestricted(citations);

  return (
    <section
      className={`${styles.panel} ${styles["answer-panel"]}`}
      aria-labelledby="answer-heading"
    >
      <div className={styles["answer-heading-row"]}>
        <div className={styles["answer-title"]}>
          <h2 id="answer-heading" tabIndex={-1}>{isPrevious ? "Previous answer" : "Answer"}</h2>
          {isPrevious ? <span>Kept visible while the new review runs</span> : null}
        </div>
        <AnswerBadge result={result} />
      </div>

      {isReady ? (
        <>
          <div className={styles["answer-intro"]}>
            Automated evidence checks passed for {result.verification_summary.supported_claims}{" "}
            supported claim{result.verification_summary.supported_claims === 1 ? "" : "s"}, drawn
            from {citations.ordered.length} cited source
            {citations.ordered.length === 1 ? "" : "s"}. Review the cited source before use.
          </div>

          <ClaimList
            citations={citations}
            claims={result.claims}
            onSelectClaim={onSelectClaim}
            onSelectEvidence={onSelectEvidence}
            selectedClaimId={selectedClaimId}
            selectedEvidenceId={selectedEvidenceId}
          />

          <div className={styles["answer-signals"]} aria-label="Answer checks">
            <Signal label="Evidence gate passed" />
            <Signal
              label={
                result.verification_summary.withheld_claims
                  ? `${result.verification_summary.withheld_claims} claim${result.verification_summary.withheld_claims === 1 ? "" : "s"} withheld`
                  : "No unsupported claims rendered"
              }
              warning={Boolean(result.verification_summary.withheld_claims)}
            />
            {licenceRestricted ? (
              <Signal
                icon={FileLock2}
                label="Passage text withheld by licence"
                warning
              />
            ) : null}
          </div>

          <div className={styles["answer-actions"]}>
            <button type="button" onClick={onCopyAnswer}>
              <Clipboard size={16} aria-hidden="true" />
              Copy with citations
            </button>
            <button type="button" onClick={onExportAudit}>
              <Download size={16} aria-hidden="true" />
              Export audit record
            </button>
          </div>
        </>
      ) : (
        <AbstentionNotice onRetry={onRetry} result={result} />
      )}
    </section>
  );
}

function AnswerBadge({ result }: { result: QuestionResult }) {
  if (result.status === "ANSWER_READY") {
    return (
      <span className={`${styles["result-badge"]} ${styles.passed}`} role="status">
        <BadgeCheck size={16} aria-hidden="true" />
        Evidence-gated result
      </span>
    );
  }
  if (result.status === "FAILED") {
    return (
      <span className={`${styles["result-badge"]} ${styles["feedback-error"]}`} role="status">
        <AlertTriangle size={16} aria-hidden="true" />
        Review failed safely
      </span>
    );
  }
  return (
    <span className={`${styles["result-badge"]} ${styles.withheld}`} role="status">
      <AlertTriangle size={16} aria-hidden="true" />
      Answer withheld
    </span>
  );
}

function Signal({
  icon: Icon,
  label,
  warning = false,
}: {
  icon?: typeof CheckCircle2;
  label: string;
  warning?: boolean;
}) {
  const Resolved = Icon ?? (warning ? AlertTriangle : CheckCircle2);
  return (
    <span className={`${styles.signal} ${warning ? styles.warning : styles.success}`}>
      <Resolved size={16} aria-hidden="true" />
      {label}
    </span>
  );
}
