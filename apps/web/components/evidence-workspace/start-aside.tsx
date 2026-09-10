"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import { getCorpusCatalogue } from "@/lib/api";
import type { CorpusCatalogue } from "@/lib/contracts";
import { withRetries } from "@/lib/readiness";
import { forgetRuns, runsSnapshot, serverRunsSnapshot, subscribeRuns } from "@/lib/run-history";

import styles from "./workspace.module.css";

export interface CorpusStatus {
  approvedCorpusAvailable: boolean;
  error: string | null;
  isLoading: boolean;
  registryAvailable: boolean;
  releaseId: string | null;
  servingMode: "ACTIVATED" | "RESEARCH_UNACTIVATED" | null;
}

/**
 * The start page's second column: what the instrument is answering from, at a glance,
 * and the reviews this browser opened. Content, where a slogan used to be.
 */
export function StartAside({
  corpusStatus,
  onRecheck,
}: {
  corpusStatus: CorpusStatus;
  onRecheck?: () => void;
}) {
  const coverage = coverageMessage(corpusStatus);
  const [catalogue, setCatalogue] = useState<CorpusCatalogue | null>(null);
  const served = Boolean(corpusStatus.releaseId) && !corpusStatus.isLoading && !corpusStatus.error;

  useEffect(() => {
    if (!served) return;
    let live = true;
    withRetries(getCorpusCatalogue, { delays: [1_000, 3_000] })
      .then((value) => {
        if (live) setCatalogue(value);
      })
      .catch(() => {
        // The facts are a courtesy; the status line above already says what is served.
      });
    return () => {
      live = false;
    };
  }, [served]);

  const documents = catalogue?.sources.length ?? null;
  const editions =
    catalogue?.sources.reduce((count, source) => count + source.versions.length, 0) ?? null;

  return (
    <aside className={styles["start-aside"]} aria-label="Corpus and recent reviews">
      <section aria-labelledby="start-corpus">
        <h2 id="start-corpus">Corpus</h2>
        <p className={`${styles["coverage-badge"]} ${styles[coverage.tone]}`}>{coverage.label}</p>
        <p className={styles["start-aside-copy"]}>
          {coverage.message}
          {corpusStatus.error && onRecheck ? (
            <button className={styles["coverage-recheck"]} type="button" onClick={onRecheck}>
              Check again
            </button>
          ) : null}
        </p>
        {catalogue?.release ? (
          <dl className={styles["aside-facts"]}>
            <div>
              <dt>Documents</dt>
              <dd>{documents}</dd>
            </div>
            <div>
              <dt>Editions</dt>
              <dd>{editions}</dd>
            </div>
            <div>
              <dt>Evidence records</dt>
              <dd>{catalogue.release.evidence_count}</dd>
            </div>
            <div>
              <dt>{catalogue.release.activated_at ? "Activated" : "Validated"}</dt>
              <dd>{formatDay(catalogue.release.activated_at ?? catalogue.release.validated_at)}</dd>
            </div>
          </dl>
        ) : null}
        <Link className={styles["aside-link"]} href="/corpus">
          Every document in the release
        </Link>
      </section>
      <RecentReviews />
    </aside>
  );
}

function RecentReviews() {
  const runs = useSyncExternalStore(subscribeRuns, runsSnapshot, serverRunsSnapshot);
  return (
    <section aria-labelledby="start-recent">
      <h2 id="start-recent">Recent reviews</h2>
      {runs.length ? (
        <>
          <ul className={styles["aside-runs"]}>
            {runs.slice(0, 6).map((run) => (
              <li key={run.questionId}>
                <a href={`/r/${encodeURIComponent(run.questionId)}`}>
                  {run.question || "Question not recorded"}
                  <small>
                    <time dateTime={run.openedAt}>{formatOpened(run.openedAt)}</time>
                  </small>
                </a>
              </li>
            ))}
          </ul>
          <p className={styles["start-aside-copy"]}>
            Kept in this browser only.{" "}
            <button className={styles["coverage-recheck"]} onClick={forgetRuns} type="button">
              Clear
            </button>
          </p>
        </>
      ) : (
        <p className={styles["start-aside-copy"]}>None yet in this browser.</p>
      )}
      <Link className={styles["aside-link"]} href="/reviews">
        All reviews
      </Link>
    </section>
  );
}

function formatOpened(value: string): string {
  const opened = new Date(value);
  if (Number.isNaN(opened.getTime())) return "date not recorded";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(opened);
}

function formatDay(value: string | null | undefined): string {
  if (!value) return "Not stated";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

export function coverageMessage(status: CorpusStatus): {
  label: string;
  message: string;
  tone: "neutral" | "passed" | "research" | "withheld";
} {
  if (status.isLoading) {
    return {
      label: "Checking corpus",
      message: "Confirming which approved guideline release is available for retrieval.",
      tone: "neutral",
    };
  }
  if (status.servingMode === "RESEARCH_UNACTIVATED") {
    return {
      label: "Research release",
      message: status.releaseId
        ? `Questions will be checked against research release ${status.releaseId}, which has not passed clinical acceptance.`
        : "Questions will be checked against a research release that has not passed clinical acceptance.",
      tone: "research",
    };
  }
  if (status.approvedCorpusAvailable) {
    return {
      label: "Approved corpus available",
      message: status.releaseId
        ? `Questions will be checked against approved release ${status.releaseId}.`
        : "Questions will be checked against the currently approved guideline release.",
      tone: "passed",
    };
  }
  if (status.error) {
    return {
      label: "Evidence service not reached",
      message: "The corpus could not be checked just now. It is checked again when this tab regains focus.",
      tone: "neutral",
    };
  }
  return {
    label: "No active corpus",
    message: "No approved release is active. Reviews will abstain until one is activated.",
    tone: "withheld",
  };
}
