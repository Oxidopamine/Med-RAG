"use client";

import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import shell from "@/components/shell/shell.module.css";
import styles from "@/components/pages/reviews.module.css";
import { getCorpusReadiness, listQuestions } from "@/lib/api";
import { downloadText } from "@/lib/export";
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
type OutcomeFilter = "all" | "answered" | "abstained" | "other";

const TERMINAL: QuestionStatus[] = ["ANSWER_READY", "ABSTAINED", "FAILED"];

export function outcomeLabel(status: Row["status"]): string {
  if (status === "ANSWER_READY") return "Answered";
  if (status === "ABSTAINED") return "No answer";
  if (status === "FAILED") return "Failed";
  if (status === "UNKNOWN") return "Not held";
  return "Running";
}

function outcomeTone(status: Row["status"]): string {
  if (status === "ANSWER_READY") return shell.ok;
  if (status === "ABSTAINED") return shell.warn;
  if (status === "FAILED") return shell.danger;
  return shell.neutral;
}

/** A reason code such as `NO_EVIDENCE_FOUND`, read the way a reader writes a sentence. */
function sentenceCase(code: string): string {
  const words = code.toLowerCase().replaceAll("_", " ");
  return words.length ? words[0]!.toUpperCase() + words.slice(1) : words;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en", { timeStyle: "short" }).format(date);
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

/* ============================================================ shared state */

/**
 * The list's state, shared between the table body and the export action.
 *
 * The export button lives in the page's title band, set by `PageFrame`'s `actions` prop,
 * while the table it exports is a section further down the page. Both need the same
 * filtered rows, so the state is held once, above both, rather than fetched twice or
 * threaded through props across a page boundary.
 */
interface ReviewsState {
  load: Load;
  rows: Row[];
  visible: Row[];
  query: string;
  setQuery: (value: string) => void;
  outcome: OutcomeFilter;
  setOutcome: (value: OutcomeFilter) => void;
  servedRelease: string | null;
  clearFilters: () => void;
  exportCsv: () => void;
}

const ReviewsContext = createContext<ReviewsState | null>(null);

function useReviewsState(): ReviewsState {
  const [server, setServer] = useState<QuestionSummary[]>([]);
  const local = useSyncExternalStore(subscribeRuns, runsSnapshot, serverRunsSnapshot);
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState<OutcomeFilter>("all");
  const [servedRelease, setServedRelease] = useState<string | null>(null);

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

  useEffect(() => {
    let live = true;
    // Only used to flag a review that ran on a release other than the one served now; a
    // miss here just leaves that note off, so it gets no retries budget of its own beyond
    // the standard backoff.
    withRetries(getCorpusReadiness, { delays: [1_000, 3_000] })
      .then((value) => {
        if (live) setServedRelease(value.corpus_release_id);
      })
      .catch(() => {
        // The list still works without it.
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

  const clearFilters = useCallback(() => {
    setQuery("");
    setOutcome("all");
  }, []);

  const exportCsv = useCallback(() => {
    downloadText(`reviews-${new Date().toISOString().slice(0, 10)}.csv`, toCsv(visible), "text/csv");
  }, [visible]);

  return { load, rows, visible, query, setQuery, outcome, setOutcome, servedRelease, clearFilters, exportCsv };
}

/** Holds the list's state above both the export action and the table that share it. */
export function ReviewsProvider({ children }: { children: ReactNode }) {
  const state = useReviewsState();
  return <ReviewsContext.Provider value={state}>{children}</ReviewsContext.Provider>;
}

function useReviews(): ReviewsState {
  const value = useContext(ReviewsContext);
  if (!value) throw new Error("ReviewList and ReviewListExportAction must render inside ReviewsProvider.");
  return value;
}

/** The CSV export button, placed in the page's title band. */
export function ReviewListExportAction() {
  const { exportCsv, visible } = useReviews();
  return (
    <button className={shell.button} type="button" onClick={exportCsv} disabled={!visible.length}>
      Export CSV
    </button>
  );
}

/* ============================================================ the table */

function SkeletonRows() {
  return (
    <>
      {[0, 1, 2].map((index) => (
        <tr key={index} aria-hidden="true">
          <td>
            <span className={`${styles.skeletonBar} ${styles.wide}`} />
          </td>
          <td>
            <span className={`${styles.skeletonBar} ${styles.narrow}`} />
          </td>
          <td>
            <span className={`${styles.skeletonBar} ${styles.narrow}`} />
          </td>
          <td>
            <span className={styles.skeletonBar} />
          </td>
        </tr>
      ))}
    </>
  );
}

function Columns() {
  return (
    <colgroup>
      <col style={{ width: "46%" }} />
      <col style={{ width: "16%" }} />
      <col style={{ width: "20%" }} />
      <col style={{ width: "18%" }} />
    </colgroup>
  );
}

function Header() {
  return (
    <thead>
      <tr>
        <th scope="col">Question</th>
        <th scope="col">Outcome</th>
        <th scope="col">Claims</th>
        <th scope="col">When</th>
      </tr>
    </thead>
  );
}

export function ReviewList() {
  const { load, rows, visible, query, setQuery, outcome, setOutcome, servedRelease, clearFilters } = useReviews();

  return (
    <section aria-labelledby="reviews-list">
      <h2 id="reviews-list" className={shell.srOnly}>
        Review list
      </h2>
      {load.status === "ready" && load.unreachable ? (
        <div className={shell.notice} role="status">
          The evidence service could not be reached. Only reviews opened in this browser are
          listed, and each is fetched again when opened.
        </div>
      ) : null}
      <div className={styles.filters}>
        <label className={shell.srOnly} htmlFor="review-search">
          Search reviews
        </label>
        <input
          className={`${shell["text-input"]} ${styles.search}`}
          id="review-search"
          placeholder="Search questions"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label className={shell.srOnly} htmlFor="review-outcome">
          Outcome
        </label>
        <select
          className={`${shell.select} ${styles.outcomeSelect}`}
          id="review-outcome"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value as OutcomeFilter)}
        >
          <option value="all">All outcomes</option>
          <option value="answered">Answered</option>
          <option value="abstained">No answer</option>
          <option value="other">Running or failed</option>
        </select>
      </div>
      {load.status === "loading" ? (
        <div className={shell["table-wrap"]}>
          <table className={`${shell.table} ${styles.table}`} aria-label="Reviews, loading">
            <Columns />
            <Header />
            <tbody>
              <SkeletonRows />
            </tbody>
          </table>
        </div>
      ) : rows.length === 0 ? (
        <div className={shell.empty}>
          <strong>No reviews yet.</strong>
          <p>
            Ask a question in the <Link href="/">workspace</Link> and it will appear here.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className={shell.empty}>
          <strong>No reviews match these filters.</strong>
          <p>Try a different search term or outcome.</p>
          <button className={`${shell.button} ${shell.small}`} type="button" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      ) : (
        <div className={shell["table-wrap"]}>
          <table className={`${shell.table} ${styles.table}`} aria-label="Reviews">
            <Columns />
            <Header />
            <tbody>
              {visible.map((row) => {
                const mismatch =
                  row.onServer && row.releaseId && servedRelease && row.releaseId !== servedRelease
                    ? `Ran on release ${row.releaseId}`
                    : null;
                return (
                  <tr key={row.questionId}>
                    <td>
                      <Link
                        className={`${shell["row-link"]} ${styles.rowLink}`}
                        href={`/r/${encodeURIComponent(row.questionId)}`}
                      >
                        {row.question || row.questionId}
                      </Link>
                      {!row.onServer ? (
                        <span className={shell.sub}>Opened in this browser</span>
                      ) : mismatch ? (
                        <span className={shell.sub}>{mismatch}</span>
                      ) : null}
                    </td>
                    <td>
                      <span className={`${shell.status} ${outcomeTone(row.status)}`}>{outcomeLabel(row.status)}</span>
                      {row.reasonCode ? <span className={shell.sub}>{sentenceCase(row.reasonCode)}</span> : null}
                    </td>
                    <td>
                      {row.onServer ? (
                        <>
                          {row.supported} supported
                          <span className={shell.sub}>{row.withheld} withheld</span>
                        </>
                      ) : (
                        <span className={shell.muted}>Not fetched</span>
                      )}
                    </td>
                    <td className={shell.num}>
                      {formatDate(row.createdAt)}
                      <span className={shell.sub}>{formatTime(row.createdAt)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className={shell["table-note"]}>
            {visible.length} of {rows.length} review{rows.length === 1 ? "" : "s"}.
          </p>
        </div>
      )}
    </section>
  );
}
