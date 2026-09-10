import { humanizeCode, locationSummary, presentAbstention } from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

interface AbstentionNoticeProps {
  onRetry: () => void;
  result: QuestionResult;
}

/**
 * Whether the service's own message repeats the headline this interface already shows.
 *
 * The service sends "No approved guideline corpus is configured." under a reason code
 * whose headline is "No approved guideline corpus is active", and both were rendered, one
 * under the other, in two different faces. Rather than dropping the service message
 * everywhere, which would lose the specifics it sometimes carries, it is suppressed only
 * when the headline already contains almost all of it.
 */
function repeatsHeadline(headline: string, message: string): boolean {
  const words = (value: string) =>
    new Set(
      value
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, " ")
        .split(/\s+/)
        .filter((word) => word.length > 3),
    );
  const inMessage = words(message);
  if (inMessage.size === 0) return true;
  const inHeadline = words(headline);
  let shared = 0;
  for (const word of inMessage) if (inHeadline.has(word)) shared += 1;
  return shared / inMessage.size >= 0.7;
}

/**
 * The state shown when no clinical claim was rendered.
 *
 * Abstention is a correct outcome here, not an error, so this reads as an answer rather
 * than a warning: the headline says what happened, one line says why, the next step is a
 * bordered instruction rather than a sentence adrift between two others, and the reason
 * code stays available in a closed disclosure for a support conversation.
 */
export function AbstentionNotice({ onRetry, result }: AbstentionNoticeProps) {
  const abstention = presentAbstention(result.abstention, result.status);
  const showMessage = !repeatsHeadline(abstention.title, abstention.message);

  return (
    <div className={styles["abstention-content"]}>
      <div className={styles["abstention-lead"]}>
        <h3>{abstention.title}</h3>
        {showMessage ? <p>{abstention.message}</p> : null}
      </div>

      {abstention.missingEvidenceRoles.length ? (
        <dl className={styles["abstention-facts"]}>
          <div>
            <dt>Could not verify</dt>
            <dd>{abstention.missingEvidenceRoles.map(humanizeCode).join(", ")}</dd>
          </div>
        </dl>
      ) : null}

      <div className={styles["abstention-next"]}>
        <p>{abstention.nextStep}</p>
        {abstention.retryable ? (
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </div>

      {abstention.closestEvidenceIds.length ? (
        <div className={styles["abstention-closest"]}>
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
        </div>
      ) : null}

      {abstention.rawCode || !showMessage ? (
        <details className={styles["abstention-details"]}>
          <summary>Details</summary>
          {abstention.rawCode ? (
            <p>
              Reason code <code>{abstention.rawCode}</code>
              {abstention.code === null ? " (not recognised by this interface)" : null}
            </p>
          ) : null}
          {showMessage ? null : (
            <p>
              Service message: <span>{abstention.message}</span>
            </p>
          )}
        </details>
      ) : null}
    </div>
  );
}
