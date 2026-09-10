"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";

import shell from "@/components/shell/shell.module.css";
import styles from "@/components/pages/reviews.module.css";
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
      <p className={`${shell.prose} ${shell.muted}`}>
        Claims flagged while reading a review, kept in this browser. Export them to sit
        beside a label file; they are the reader&apos;s judgement, not a label.
      </p>
      {flags.length ? (
        <>
          <div className={shell.actions}>
            <button
              className={shell.button}
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
              className={`${shell.button} ${shell.ghost}`}
              onClick={() => {
                if (window.confirm("Remove every flag kept in this browser?")) clearFlags();
              }}
              type="button"
            >
              Clear all
            </button>
          </div>
          <div className={shell["table-wrap"]}>
            <table className={`${shell.table} ${styles.table}`} aria-label="Flagged claims">
              <colgroup>
                <col style={{ width: "30%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "26%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "12%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Claim</th>
                  <th scope="col">Reason</th>
                  <th scope="col">Note</th>
                  <th scope="col">When</th>
                  <th scope="col">
                    <span className={shell.srOnly}>Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {flags.map((flag) => (
                  <tr key={`${flag.questionId}:${flag.claimId}`}>
                    <td>
                      <Link
                        className={`${shell["row-link"]} ${styles.rowLink}`}
                        href={`/r/${encodeURIComponent(flag.questionId)}`}
                      >
                        {flag.claimText}
                      </Link>
                    </td>
                    <td>
                      <span className={`${shell.status} ${shell.neutral}`}>{reasonLabel(flag.reason)}</span>
                    </td>
                    <td>{flag.note ? flag.note : <span className={shell.muted}>No note</span>}</td>
                    <td className={shell.num}>{formatWhen(flag.raisedAt)}</td>
                    <td>
                      <button
                        className={`${shell.button} ${shell.small}`}
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
        <div className={shell.empty}>
          <strong>No claims flagged yet.</strong>
          <p>The control sits under each claim in a review.</p>
        </div>
      )}
    </section>
  );
}
