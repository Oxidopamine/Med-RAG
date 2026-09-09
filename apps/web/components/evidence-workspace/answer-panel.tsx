import { Clipboard } from "lucide-react";

import { buildCitations, isFullyLicenceRestricted } from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

import { AbstentionNotice } from "./abstention-notice";
import { ClaimList } from "./claim-list";
import { ReferenceList } from "./reference-list";
import styles from "./workspace.module.css";

interface AnswerPanelProps {
  isPrevious?: boolean;
  onCopyAnswer: () => void;
  onInspectClaim?: (claimId: string) => void;
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
  onInspectClaim,
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
          <AnswerSummary
            citations={citations}
            licenceRestricted={licenceRestricted}
            onSelectEvidence={onSelectEvidence}
            result={result}
          />
          <ClaimList
            citations={citations}
            claims={result.claims}
            onInspectClaim={onInspectClaim}
            onSelectClaim={onSelectClaim}
            onSelectEvidence={onSelectEvidence}
            result={result}
            selectedClaimId={selectedClaimId}
            selectedEvidenceId={selectedEvidenceId}
          />
          <ReferenceList
            citations={citations}
            onSelectEvidence={onSelectEvidence}
            selectedEvidenceId={selectedEvidenceId}
          />
          <div className={styles["answer-actions"]}>
            <button className={styles["primary-action"]} type="button" onClick={onCopyAnswer}>
              <Clipboard size={16} aria-hidden="true" />
              Copy with citations
            </button>
          </div>
        </>
      ) : (
        <AbstentionNotice onRetry={onRetry} result={result} />
      )}
    </section>
  );
}

/**
 * One line of facts about the answer: how many claims, from how many sources and
 * editions, and what was withheld. Superseded editions and licence-withheld passages are
 * named here once, each as a control that opens the first record it is about.
 */
function AnswerSummary({
  citations,
  licenceRestricted,
  onSelectEvidence,
  result,
}: {
  citations: ReturnType<typeof buildCitations>;
  licenceRestricted: boolean;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
}) {
  const supported = result.verification_summary.supported_claims;
  const withheld = result.verification_summary.withheld_claims;
  const sources = new Set(citations.ordered.map((citation) => citation.detail.source_id)).size;
  const editions = new Set(citations.ordered.map((citation) => citation.detail.source_version_id)).size;
  const superseded = citations.ordered.filter(
    (citation) => citation.detail.lifecycle_status !== "CURRENT",
  );
  const restricted = citations.ordered.filter((citation) => citation.policy.licenceRestricted);
  if (!citations.ordered.length) return null;

  return (
    <div className={styles["answer-summary"]} role="group" aria-label="Cited source summary">
      <span>
        {supported} supported claim{supported === 1 ? "" : "s"} from {sources} source
        {sources === 1 ? "" : "s"}
        {editions === sources ? "" : `, ${editions} edition${editions === 1 ? "" : "s"}`}
      </span>
      {withheld ? (
        <span className={styles["withheld-count"]}>
          {withheld} claim{withheld === 1 ? "" : "s"} withheld
        </span>
      ) : null}
      {superseded.length ? (
        <button
          className={styles["summary-link"]}
          onClick={() => onSelectEvidence(superseded[0]!.detail.evidence_id)}
          type="button"
        >
          {superseded.length} superseded edition{superseded.length === 1 ? "" : "s"}
        </button>
      ) : null}
      {restricted.length ? (
        <button
          className={styles["summary-link"]}
          onClick={() => onSelectEvidence(restricted[0]!.detail.evidence_id)}
          type="button"
        >
          {restricted.length} withheld by licence
        </button>
      ) : licenceRestricted ? (
        <span className={styles["withheld-count"]}>Passage text withheld by licence</span>
      ) : null}
    </div>
  );
}

function AnswerBadge({ result }: { result: QuestionResult }) {
  if (result.status === "ANSWER_READY") {
    return (
      <span className={`${styles["result-badge"]} ${styles.passed}`}>Checks passed</span>
    );
  }
  if (result.status === "FAILED") {
    return (
      <span className={`${styles["result-badge"]} ${styles["feedback-error"]}`}>Review failed</span>
    );
  }
  return <span className={`${styles["result-badge"]} ${styles.withheld}`}>No answer</span>;
}
