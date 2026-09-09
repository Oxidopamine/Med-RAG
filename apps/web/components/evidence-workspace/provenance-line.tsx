"use client";

import { useId, useState } from "react";

import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

/**
 * Where the answer came from, on one line: the release and its standing, the activation
 * date, the run. Identifiers and the manifest digest sit behind "Details"; the line is for
 * telling two reviews apart, not for reading a hash.
 */
export function ProvenanceLine({
  exports,
  onCopyLink,
  result,
}: {
  exports: Array<{ label: string; run: () => void }>;
  onCopyLink: () => void;
  result: QuestionResult;
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const release = result.corpus_release;
  const research = release?.serving_mode === "RESEARCH_UNACTIVATED";
  const tone = !release ? styles.none : research ? styles.research : "";

  return (
    <section className={styles["provenance-line"]} aria-label="Review provenance">
      <span className={`${styles["provenance-fact"]} ${tone}`}>
        {/* One child beside the mark, so the flex gap never splits a sentence. */}
        <span>
          {!release ? (
            "No approved release"
          ) : research ? (
            <>
              Research release <strong>{release.corpus_release_id}</strong>, validated but not
              activated
            </>
          ) : (
            <>
              Approved release <strong>{release.corpus_release_id}</strong>
              {release.activated_at ? `, activated ${formatDate(release.activated_at)}` : ""}
            </>
          )}
        </span>
      </span>
      <span>
        Run <strong title={result.question_id}>{shortId(result.question_id)}</strong>
      </span>
      <span className={styles["provenance-actions"]}>
        <button
          aria-controls={detailsId}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          type="button"
        >
          Details
        </button>
        <button onClick={onCopyLink} type="button">
          Copy link
        </button>
        <details className={styles["export-menu"]}>
          <summary>Export</summary>
          <ul>
            {exports.map((item) => (
              <li key={item.label}>
                <button
                  onClick={(event) => {
                    item.run();
                    event.currentTarget.closest("details")?.removeAttribute("open");
                  }}
                  type="button"
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </details>
      </span>
      <dl className={styles["provenance-details"]} hidden={!open} id={detailsId}>
        {release ? (
          <>
            <dt>Release</dt>
            <dd>{release.corpus_release_id}</dd>
            <dt>{research ? "Activation" : "Activated"}</dt>
            <dd>
              {research
                ? "Not activated: no signed activation decision"
                : release.activated_at
                  ? formatDateTime(release.activated_at)
                  : "Not stated"}
            </dd>
            <dt>Manifest SHA-256</dt>
            <dd>{release.manifest_sha256}</dd>
          </>
        ) : null}
        <dt>Run</dt>
        <dd>{result.question_id}</dd>
        <dt>Updated</dt>
        <dd>{formatDateTime(result.updated_at)}</dd>
      </dl>
    </section>
  );
}

function shortId(value: string): string {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
