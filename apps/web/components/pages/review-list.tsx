"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import styles from "@/components/shell/shell.module.css";
import { listQuestions } from "@/lib/api";
import { withRetries } from "@/lib/readiness";
import { runsSnapshot, serverRunsSnapshot, subscribeRuns } from "@/lib/run-history";
import type { QuestionSummary } from "@/lib/contracts";
import type { QuestionStatus } from "@/lib/types";

interface Row {
  questionId: string;
  question: string;
  status: QuestionStatus | "UNKNOWN";
  createdAt: string;
  releaseId: string | null;
  supported: number;
  withheld: number;
  reasonCode: string | null;
  onServer: boolean;
}

type Load = { status: "loading" } | { status: "ready"; unreachable: boolean };

const TERMINAL: QuestionStatus[] = ["ANSWER_READY", "ABSTAINED", "FAILED"];

export function outcomeLabel(status: Row["status"]): string {
  if (status === "ANSWER_READY") return "Answered";
  if (status === "ABSTAINED") return "No answer";
  if (status === "FAILED") return "Failed";
  if (status === "UNKNOWN") return "Not held";
  return "Running";
}

function outcomeTone(status: Row["status"]): string {
  if (status === "ANSWER_READY") return styles.ok;
  if (status === "ABSTAINED") return styles.warn;
  if (status === "FAILED") return styles.danger;
  return styles.neutral;
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function mergeRows(
  server: QuestionSummary[],
  local: Array<{ questionId: string; question: string; openedAt: string }>,
): Row[] {
  const rows = new Map<string, Row>();
  for (const item of server) {
    rows.set(item.question_id, {
      questionId: item.question_id,
      question: item.question,
      status: item.status,
      createdAt: item.created_at,
      releaseId: item.corpus_release_id,
      supported: item.supported_claims,
      withheld: item.withheld_claims,
      reasonCode: item.abstention_reason_code,
      onServer: true,
    });
  }
  for (const item of local) {
    if (rows.has(item.questionId)) continue;
    rows.set(item.questionId, {
      questionId: item.questionId,
      question: item.question,
      status: "UNKNOWN",
      createdAt: item.openedAt,
      releaseId: null,
      supported: 0,
      withheld: 0,
      reasonCode: null,
      onServer: false,
    });
  }
  return [...rows.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function toCsv(rows: Row[]): string {
  const escape = (value: string | number | null) =>
    `"${String(value ?? "").replaceAll('"', '""')}"`;
  const header = ["question_id", "question", "outcome", "created_at", "release", "supported", "withheld", "reason"];
  const lines = rows.map((row) =>
    [
      row.questionId,
      row.question,
      outcomeLabel(row.status),
      row.createdAt,
      row.releaseId ?? "",
      row.supported,
      row.withheld,
      row.reasonCode ?? "",
    ]
      .map(escape)
      .join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

export function ReviewList() {
  const [server, setServer] = useState<QuestionSummary[]>([]);
  const local = useSyncExternalStore(subscribeRuns, runsSnapshot, serverRunsSnapshot);
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState<"all" | "answered" | "abstained" | "other">("all");

  useEffect(() => {
    let live = true;
    withRetries(() => listQuestions(200), { delays: [1_000, 3_000] })
      .then((items) => {
        if (!live) return;
        setServer(items);
        setLoad({ status: "ready", unreachable: false });
      })
      .catch(() => {
        if (live) setLoad({ status: "ready", unreachable: true });
      });
    return () => {
      live = false;
    };
  }, []);

  const rows = useMemo(() => mergeRows(server, local), [server, local]);
  const visible = useMemo(() => {
    const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return rows.filter((row) => {
      if (outcome === "answered" && row.status !== "ANSWER_READY") return false;
      if (outcome === "abstained" && row.status !== "ABSTAINED") return false;
      if (outcome === "other" && (TERMINAL as string[]).includes(row.status) && row.status !== "FAILED") {
        return false;
      }
      const haystack = `${row.question} ${row.questionId}`.toLocaleLowerCase();
      return terms.every((term) => haystack.includes(term));
    });
  }, [rows, query, outcome]);

  function exportCsv() {
    const blob = new Blob([toCsv(visible)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `reviews-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section aria-labelledby="reviews-list">
      <h2 id="reviews-list" className={styles.srOnly}>
        Review list
      </h2>
      {load.status === "ready" && load.unreachable ? (
        <div className={styles.notice} role="status">
          The evidence service could not be reached. Only reviews opened in this browser are
          listed, and each is fetched again when opened.
        </div>
      ) : null}
      <div className={styles.actions}>
        <label className={styles.srOnly} htmlFor="review-search">
          Search reviews
        </label>
        <input
          className={styles["text-input"]}
          id="review-search"
          placeholder="Search questions"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label className={styles.srOnly} htmlFor="review-outcome">
          Outcome
        </label>
        <select
          className={styles.select}
          id="review-outcome"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value as typeof outcome)}
        >
          <option value="all">All outcomes</option>
          <option value="answered">Answered</option>
          <option value="abstained">No answer</option>
          <option value="other">Running or failed</option>
        </select>
        <button className={styles.button} type="button" onClick={exportCsv} disabled={!visible.length}>
          Export CSV
        </button>
      </div>
      {load.status === "loading" ? (
        <p className={styles.empty}>Reading reviews.</p>
      ) : visible.length ? (
        <div className={styles["table-wrap"]}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">When</th>
                <th scope="col">Outcome</th>
                <th scope="col">Release</th>
                <th scope="col" className={styles.num}>
                  Supported
                </th>
                <th scope="col" className={styles.num}>
                  Withheld
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.questionId}>
                  <td>
                    <Link href={`/r/${encodeURIComponent(row.questionId)}`}>
                      {row.question || row.questionId}
                    </Link>
                    {!row.onServer ? (
                      <>
                        <br />
                        <span className={styles.muted}>Opened in this browser</span>
                      </>
                    ) : null}
                  </td>
                  <td>{formatWhen(row.createdAt)}</td>
                  <td>
                    <span className={`${styles.status} ${outcomeTone(row.status)}`}>
                      {outcomeLabel(row.status)}
                    </span>
                    {row.reasonCode ? (
                      <>
                        <br />
                        <span className={styles.muted}>{row.reasonCode.toLowerCase().replaceAll("_", " ")}</span>
                      </>
                    ) : null}
                  </td>
                  <td>{row.releaseId ?? ""}</td>
                  <td className={styles.num}>{row.onServer ? row.supported : ""}</td>
                  <td className={styles.num}>{row.onServer ? row.withheld : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.empty}>
          No reviews yet. Ask a question in the <Link href="/">workspace</Link> and it will appear here.
        </p>
      )}
    </section>
  );
}
