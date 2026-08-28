import {
  BookOpenCheck,
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  History,
  ShieldCheck,
  TriangleAlert,
  Trash2,
} from "lucide-react";
import { useSyncExternalStore, type ReactNode } from "react";

import {
  forgetRuns,
  runsSnapshot,
  serverRunsSnapshot,
  subscribeRuns,
} from "@/lib/run-history";

import styles from "./workspace.module.css";

export interface CorpusStatus {
  approvedCorpusAvailable: boolean;
  error: string | null;
  isLoading: boolean;
  registryAvailable: boolean;
  releaseId: string | null;
  /** Null when nothing is being served; see the readiness endpoint for why this is
   *  separate from `approvedCorpusAvailable`. */
  servingMode: "ACTIVATED" | "RESEARCH_UNACTIVATED" | null;
}

export function GettingStarted({ corpusStatus }: { corpusStatus: CorpusStatus }) {
  const coverage = coverageMessage(corpusStatus);

  return (
    <section className={`${styles.panel} ${styles["getting-started"]}`} aria-labelledby="start-heading">
      <div className={styles["getting-started-heading"]}>
        <div>
          <span className={styles["section-kicker"]}>Before you ask</span>
          <h2 id="start-heading">Evidence-gated guideline review</h2>
        </div>
        <span className={`${styles["coverage-badge"]} ${styles[coverage.tone]}`}>
          {coverage.icon}
          {coverage.label}
        </span>
      </div>

      <p className={styles["getting-started-copy"]}>{coverage.message}</p>

      <div className={styles["start-principles"]}>
        <div>
          <BookOpenCheck size={20} aria-hidden="true" />
          <span>
            <strong>Be specific</strong>
            Include the population, condition, and decision you are evaluating.
          </span>
        </div>
        <div>
          <ShieldCheck size={20} aria-hidden="true" />
          <span>
            <strong>Evidence stays visible</strong>
            Supported claims link to exact source details; unsupported claims are withheld.
          </span>
        </div>
      </div>

      <RecentRuns />
    </section>
  );
}

/**
 * The reviews this browser has opened.
 *
 * A run takes minutes and has always had an address; nothing wrote it down, so keeping the
 * tab open was the only way back to one. Subscribed rather than read on mount: the list
 * exists only in one browser, so the server renders none and hydration crosses to the real
 * one exactly once - and a run recorded while this panel is on screen, in this tab or
 * another, arrives without anything having to remount.
 */
function RecentRuns() {
  const runs = useSyncExternalStore(subscribeRuns, runsSnapshot, serverRunsSnapshot);

  if (!runs.length) return null;

  return (
    <div className={styles["recent-runs"]}>
      <div className={styles["recent-runs-heading"]}>
        <h3>
          <History size={15} aria-hidden="true" />
          Recent reviews
        </h3>
        <button onClick={forgetRuns} type="button">
          <Trash2 size={14} aria-hidden="true" />
          Clear
        </button>
      </div>
      <ul>
        {runs.map((run) => (
          <li key={run.questionId}>
            <a href={`/r/${encodeURIComponent(run.questionId)}`}>
              <span>{run.question || "Question not recorded"}</span>
              <small>
                <time dateTime={run.openedAt}>{formatOpened(run.openedAt)}</time>
                {" · "}
                {run.questionId}
              </small>
            </a>
          </li>
        ))}
      </ul>
      <p>
        {/* Said plainly, because a list of clinical questions in a browser invites the
            assumption that the answers are there too. They are not: this is a list of
            addresses, and every result is fetched from the server when one is opened. */}
        Kept in this browser only. Opening one fetches the review from the server again.
      </p>
    </div>
  );
}

function formatOpened(value: string): string {
  const opened = new Date(value);
  if (Number.isNaN(opened.getTime())) return "date not recorded";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(
    opened,
  );
}

function coverageMessage(status: CorpusStatus): {
  icon: ReactNode;
  label: string;
  message: string;
  tone: "neutral" | "passed" | "research" | "withheld";
} {
  if (status.isLoading) {
    return {
      icon: <CircleDashed className={styles.spin} size={15} aria-hidden="true" />,
      label: "Checking corpus",
      message: "Confirming which approved guideline release is available for retrieval.",
      tone: "neutral",
    };
  }
  /*
   * Research serving answers from a release that never passed activation.
   *
   * It is neither of the other two states, and saying "no clinical answer will be
   * rendered" here is false in the opposite direction from calling it approved - answers
   * *are* rendered, they simply carry no clinical acceptance. This branch exists because
   * the header already reports it correctly, and one screen disagreeing with itself about
   * whether a result can appear is worse than either message alone.
   */
  if (status.servingMode === "RESEARCH_UNACTIVATED") {
    return {
      icon: <FlaskConical size={15} aria-hidden="true" />,
      label: "Research release",
      message: status.releaseId
        ? `Questions will be checked against research release ${status.releaseId}, which has not passed clinical acceptance. Answers are for research review only.`
        : "Questions will be checked against a research release that has not passed clinical acceptance. Answers are for research review only.",
      tone: "research",
    };
  }
  if (status.approvedCorpusAvailable) {
    return {
      icon: <CheckCircle2 size={15} aria-hidden="true" />,
      label: "Approved corpus available",
      message: status.releaseId
        ? `Questions will be checked against approved corpus release ${status.releaseId}.`
        : "Questions will be checked against the currently approved guideline corpus.",
      tone: "passed",
    };
  }
  return {
    icon: <TriangleAlert size={15} aria-hidden="true" />,
    label: status.registryAvailable ? "No active corpus" : "Corpus status unavailable",
    message: status.error
      ? "Coverage could not be confirmed. The evidence gate will fail closed rather than display an unsupported answer."
      : "No approved release is active. You can still review interpreted context, but no clinical answer will be rendered.",
    tone: "withheld",
  };
}
