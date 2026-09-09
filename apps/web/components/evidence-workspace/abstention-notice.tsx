import { humanizeCode, locationSummary, presentAbstention } from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

interface AbstentionNoticeProps {
  onRetry: () => void;
  result: QuestionResult;
}

/**
 * The state shown when no clinical claim was rendered.
 *
 * Abstention is a correct outcome here, not an error, so this reads as an answer rather
 * than a warning: no icon, no wash, no border. The lead sentence says what happened and
 * why, the next step says what a reader can do about it, and the reason code stays
 * available in a closed disclosure for a support conversation - including when this
 * build does not recognise it.
 */
export function AbstentionNotice({ onRetry, result }: AbstentionNoticeProps) {
  const abstention = presentAbstention(result.abstention, result.status);

  return (
    <div className={styles["abstention-content"]}>
      <p className={`${styles["abstention-lead"]} reading`}>
        <strong>{abstention.title}</strong> {abstention.message}
      </p>

      {abstention.missingEvidenceRoles.length ? (
        <p className={styles["abstention-roles"]}>
          The review could not verify:{" "}
          {abstention.missingEvidenceRoles.map(humanizeCode).join(", ")}.
        </p>
      ) : null}

      <p className={styles["abstention-next"]}>
        {abstention.retryable ? (
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        ) : null}
        {abstention.nextStep}
      </p>

      {abstention.closestEvidenceIds.length ? (
        <>
          <h3>What came closest</h3>
          <p>
            These ranked highest for the question and supported no claim. Compare them
            with the question to see whether the guideline is thin here or the question
            needs rephrasing.
          </p>
          <ol className={styles.references} aria-label="Closest passages">
            {abstention.closestEvidenceIds.map((evidenceId, index) => {
              const detail = abstention.closestEvidence.find(
                (candidate) => candidate.evidence_id === evidenceId,
              );
              return (
                <li key={evidenceId}>
                  <span className={styles["reference-number"]}>{index + 1}</span>
                  <span className={styles["reference-copy"]}>
                    {detail ? (
                      <>
                        {detail.publisher_name}. <em>{detail.source_title}</em>.{" "}
                        {detail.source_version_label}, {locationSummary(detail)}.{" "}
                        <a href={detail.source_url} target="_blank" rel="noreferrer">
                          Open source
                          <span className={styles.srOnly}> (opens in a new tab)</span>
                        </a>
                      </>
                    ) : (
                      <>
                        <code>{evidenceId}</code>. Record could not be resolved.
                      </>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
        </>
      ) : null}

      {abstention.rawCode ? (
        <details className={styles["abstention-details"]}>
          <summary>Details</summary>
          <p>
            Reason code <code>{abstention.rawCode}</code>
            {abstention.code === null ? " (not recognised by this interface)" : null}
          </p>
        </details>
      ) : null}
    </div>
  );
}
