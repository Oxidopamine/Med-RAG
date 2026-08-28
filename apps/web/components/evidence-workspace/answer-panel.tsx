import {
  AlertTriangle,
  BadgeCheck,
  BookText,
  CheckCircle2,
  Clipboard,
  FileLock2,
  History,
} from "lucide-react";

import type { CitationIndex } from "@/lib/evidence-presentation";
import { buildCitations, isFullyLicenceRestricted } from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

import { AbstentionNotice } from "./abstention-notice";
import { ClaimList } from "./claim-list";
import styles from "./workspace.module.css";

interface AnswerPanelProps {
  isPrevious?: boolean;
  onCopyAnswer: () => void;
  /** Select a claim and move the reader to the inspector; see `ClaimList`. */
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
          <div className={styles["answer-intro"]}>
            Automated evidence checks passed for {result.verification_summary.supported_claims}{" "}
            supported claim{result.verification_summary.supported_claims === 1 ? "" : "s"}, drawn
            from {citations.ordered.length} cited source
            {citations.ordered.length === 1 ? "" : "s"}. Review the cited source before use.
          </div>

          <SourceProvenance citations={citations} onSelectEvidence={onSelectEvidence} />

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

          <div className={styles["answer-signals"]} role="group" aria-label="Answer checks">
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
 * What the answer rests on, before a reader opens anything.
 *
 * Two of these facts previously took three clicks each to find: whether any cited edition
 * has been superseded, and whether any cited passage is withheld under licence. Both
 * change how the answer above should be read, and both were discoverable only by opening
 * each source in turn. Counted here, and each count opens the first record it counted, so
 * the summary is a way in rather than a statistic.
 */
function SourceProvenance({
  citations,
  onSelectEvidence,
}: {
  citations: CitationIndex;
  onSelectEvidence: (evidenceId: string) => void;
}) {
  if (!citations.ordered.length) return null;

  const superseded = citations.ordered.filter(
    (citation) => citation.detail.lifecycle_status !== "CURRENT",
  );
  const restricted = citations.ordered.filter((citation) => citation.policy.licenceRestricted);
  /*
   * Distinct documents and distinct editions, not citation counts.
   *
   * `citations.ordered` is one entry per cited *passage*, so counting it called two
   * paragraphs of one guideline "2 sources" - and then, because the edition count was
   * already distinct, appended ", 1 edition" to say so. In a strip whose whole job is to
   * tell a reader how broad the evidence under an answer is, before they open any of it,
   * that overstates the breadth by however many times the answer quotes the same document.
   */
  const sources = new Set(citations.ordered.map((citation) => citation.detail.source_id)).size;
  const editions = new Set(
    citations.ordered.map((citation) => citation.detail.source_version_id),
  ).size;

  return (
    <div className={styles["answer-provenance"]} role="group" aria-label="Cited source summary">
      <span className={styles["provenance-fact"]}>
        <BookText size={14} aria-hidden="true" />
        {sources} source{sources === 1 ? "" : "s"}
        {/* Named only when it adds something: one edition per source is the ordinary
            case, and saying so on every answer buries the case worth seeing - the same
            guideline cited at two editions at once. */}
        {editions === sources ? "" : `, ${editions} edition${editions === 1 ? "" : "s"}`}
      </span>

      {superseded.length ? (
        <button
          className={`${styles["provenance-fact"]} ${styles.warning}`}
          onClick={() => onSelectEvidence(superseded[0]!.detail.evidence_id)}
          type="button"
        >
          <History size={14} aria-hidden="true" />
          {superseded.length} superseded edition{superseded.length === 1 ? "" : "s"}
        </button>
      ) : null}

      {restricted.length ? (
        <button
          className={`${styles["provenance-fact"]} ${styles.warning}`}
          onClick={() => onSelectEvidence(restricted[0]!.detail.evidence_id)}
          type="button"
        >
          <FileLock2 size={14} aria-hidden="true" />
          {restricted.length} withheld by licence
        </button>
      ) : null}
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
