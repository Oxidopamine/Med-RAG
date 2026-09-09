"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";

import styles from "@/components/shell/shell.module.css";
import { downloadText } from "@/lib/export";
import { clearFlags, flagsSnapshot, reasonLabel, serverFlagsSnapshot, subscribeFlags, withdrawFlag } from "@/lib/flags";

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/** The claims a reader flagged while reading, with the way to export them beside a label file. */
export function FlaggedClaims() {
  const flags = useSyncExternalStore(subscribeFlags, flagsSnapshot, serverFlagsSnapshot);

  return (
    <section aria-labelledby="flagged-claims">
      <h2 id="flagged-claims">Flagged claims</h2>
      <p className={`${styles.prose} ${styles.muted}`}>
        Claims flagged while reading a review, kept in this browser. Export them to sit
        beside a label file; they are the reader&apos;s judgement, not a label.
      </p>
      {flags.length ? (
        <>
          <div className={styles.actions}>
            <button
              className={styles.button}
              onClick={() =>
                downloadText(
                  `flags-${new Date().toISOString().slice(0, 10)}.json`,
                  JSON.stringify({ schema_version: 1, exported_at: new Date().toISOString(), flags }, null, 2),
                  "application/json",
                )
              }
              type="button"
            >
              Export flags (JSON)
            </button>
            <button
              className={styles.button}
              onClick={() => {
                if (window.confirm("Remove every flag kept in this browser?")) clearFlags();
              }}
              type="button"
            >
              Clear all
            </button>
          </div>
          <div className={styles["table-wrap"]}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Claim</th>
                  <th scope="col">Reason</th>
                  <th scope="col">Note</th>
                  <th scope="col">When</th>
                  <th scope="col">
                    <span className={styles.srOnly}>Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {flags.map((flag) => (
                  <tr key={`${flag.questionId}:${flag.claimId}`}>
                    <td>
                      <Link href={`/r/${encodeURIComponent(flag.questionId)}`}>{flag.claimText}</Link>
                    </td>
                    <td>{reasonLabel(flag.reason)}</td>
                    <td>{flag.note}</td>
                    <td>{formatWhen(flag.raisedAt)}</td>
                    <td>
                      <button
                        className={styles.button}
                        onClick={() => withdrawFlag(flag.questionId, flag.claimId)}
                        type="button"
                      >
                        Withdraw
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className={styles.empty}>No claims flagged yet. The control sits under each claim in a review.</p>
      )}
    </section>
  );
}
