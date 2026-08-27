import { RotateCw, ShieldAlert } from "lucide-react";

import { humanizeCode, presentAbstention } from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

interface AbstentionNoticeProps {
  onRetry: () => void;
  result: QuestionResult;
}

/**
 * The state shown when no clinical claim was rendered.
 *
 * Abstention is a correct outcome here, not an error, so the surface has to say which
 * of the eight reason codes fired, what it means, and what the reader can do next. The
 * service message stays authoritative - it knows which release and evidence set ran -
 * and the raw reason code stays visible for a support conversation, including when this
 * build does not recognise it.
 */
export function AbstentionNotice({ onRetry, result }: AbstentionNoticeProps) {
  const abstention = presentAbstention(result.abstention, result.status);

  return (
    <div className={styles["abstention-content"]}>
      <div className={styles["abstention-summary"]}>
        <ShieldAlert size={24} aria-hidden="true" />
        <div>
          <strong>{abstention.title}</strong>
          <p>{abstention.message}</p>
        </div>
      </div>

      {abstention.missingEvidenceRoles.length ? (
        <details className={styles["withheld-details"]}>
          <summary>Which evidence was missing?</summary>
          <p>The review did not verify these required evidence roles:</p>
          <ul>
            {abstention.missingEvidenceRoles.map((role) => (
              <li key={role}>{humanizeCode(role)}</li>
            ))}
          </ul>
        </details>
      ) : null}

      {abstention.closestEvidenceIds.length ? (
        <details className={styles["withheld-details"]}>
          <summary>What came closest?</summary>
          <p>
            These passages ranked highest for the question and still did not support a
            claim. They are named, not shown: nothing here passed a claim gate.
          </p>
          <ul>
            {abstention.closestEvidenceIds.map((evidenceId) => (
              <li key={evidenceId}>
                <code>{evidenceId}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className={styles["abstention-actions"]}>
        {/* The retry stays available even where it cannot help on its own: the corpus or
            pipeline may have been fixed since the run, and removing the only action
            would leave the reader with nowhere to go. What changes is whether the
            interface pretends a retry is the fix. */}
        <button type="button" onClick={onRetry}>
          <RotateCw size={16} aria-hidden="true" />
          Try again
        </button>
        <span>
          {abstention.retryable ? null : (
            <strong className={styles["abstention-blocked"]}>
              Retrying alone will not change this.{" "}
            </strong>
          )}
          {abstention.nextStep}
        </span>
      </div>

      {abstention.rawCode ? (
        <p className={styles["abstention-code"]}>
          Reason code <code>{abstention.rawCode}</code>
          {abstention.code === null ? " (not recognised by this interface)" : null}
        </p>
      ) : null}
    </div>
  );
}
