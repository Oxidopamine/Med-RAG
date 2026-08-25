import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Clipboard,
  Download,
  FileSearch,
  RotateCw,
  ShieldAlert,
} from "lucide-react";

import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

interface AnswerPanelProps {
  isPrevious?: boolean;
  onCopyAnswer: () => void;
  onExportAudit: () => void;
  onRetry: () => void;
  onSelectClaim: (claimId: string) => void;
  result: QuestionResult;
  selectedClaimId: string | null;
}

export function AnswerPanel({
  isPrevious = false,
  onCopyAnswer,
  onExportAudit,
  onRetry,
  onSelectClaim,
  result,
  selectedClaimId,
}: AnswerPanelProps) {
  const isReady = result.status === "ANSWER_READY";

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
            supported claim{result.verification_summary.supported_claims === 1 ? "" : "s"}. Review
            the cited source before use.
          </div>
          <ol className={styles["claim-list"]}>
            {result.claims.map((claim, index) => (
              <li
                className={claim.claim_id === selectedClaimId ? styles["selected-claim"] : ""}
                key={claim.claim_id}
              >
                <p>{claim.text}</p>
                <div className={styles["claim-footer"]}>
                  <button type="button" onClick={() => onSelectClaim(claim.claim_id)}>
                    <FileSearch size={16} aria-hidden="true" />
                    {claim.evidence_ids.length
                      ? `${claim.evidence_ids.length} source${claim.evidence_ids.length === 1 ? "" : "s"}`
                      : "Inspect evidence"}
                  </button>
                  <span>
                    Claim {index + 1} · {humanize(claim.verification_status)}
                  </span>
                </div>
              </li>
            ))}
          </ol>
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
        <AbstainedAnswer result={result} onRetry={onRetry} />
      )}
    </section>
  );
}

function AbstainedAnswer({ result, onRetry }: { result: QuestionResult; onRetry: () => void }) {
  const guidance = abstentionGuidance(result.abstention?.reason_code, result.status);
  return (
    <div className={styles["abstention-content"]}>
      <div className={styles["abstention-summary"]}>
        <ShieldAlert size={24} aria-hidden="true" />
        <div>
          <strong>{guidance.title}</strong>
          <p>{result.abstention?.message ?? guidance.message}</p>
        </div>
      </div>

      {result.abstention?.missing_evidence_roles.length ? (
        <details className={styles["withheld-details"]}>
          <summary>Why was this withheld?</summary>
          <p>The review did not verify these required evidence roles:</p>
          <ul>
            {result.abstention.missing_evidence_roles.map((role) => (
              <li key={role}>{humanize(role)}</li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className={styles["abstention-actions"]}>
        <button type="button" onClick={onRetry}>
          <RotateCw size={16} aria-hidden="true" />
          Try again
        </button>
        <span>{guidance.nextStep}</span>
      </div>
    </div>
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

function Signal({ label, warning = false }: { label: string; warning?: boolean }) {
  return (
    <span className={`${styles.signal} ${warning ? styles.warning : styles.success}`}>
      {warning ? (
        <AlertTriangle size={16} aria-hidden="true" />
      ) : (
        <CheckCircle2 size={16} aria-hidden="true" />
      )}
      {label}
    </span>
  );
}

function abstentionGuidance(
  reasonCode: string | undefined,
  status: QuestionResult["status"],
): { message: string; nextStep: string; title: string } {
  if (reasonCode === "NO_APPROVED_CORPUS") {
    return {
      title: "No approved guideline corpus is active",
      message: "The evidence gate did not have an approved source collection to search.",
      nextStep: "Activate an approved corpus release, then retry this question.",
    };
  }
  if (reasonCode === "RETRIEVAL_PIPELINE_NOT_CONFIGURED") {
    return {
      title: "Guideline retrieval is not available",
      message: "An approved corpus exists, but the retrieval pipeline is not ready.",
      nextStep: "Check retrieval configuration or try again after service restoration.",
    };
  }
  if (reasonCode === "PIPELINE_FAILURE" || status === "FAILED") {
    return {
      title: "The review could not be completed",
      message: "The system failed closed and did not display a clinical claim.",
      nextStep: "Retry once. If the problem continues, share the run ID with support.",
    };
  }
  return {
    title: "The evidence gate withheld the answer",
    message: "The available evidence was not complete enough to support a clinical claim.",
    nextStep: "Refine the population or decision in the question, then try again.",
  };
}

function humanize(value: string): string {
  const words = value.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
