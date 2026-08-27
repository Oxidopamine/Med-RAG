import {
  BookOpenCheck,
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { ReactNode } from "react";

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
    </section>
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
