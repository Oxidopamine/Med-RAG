import { CircleDashed, FlaskConical, ShieldAlert, ShieldPlus, TriangleAlert } from "lucide-react";
import Link from "next/link";

import type { CorpusStatus } from "@/components/evidence-workspace/getting-started";

import styles from "./workspace.module.css";

/**
 * Product identity, the corpus being queried, and the research-use boundary.
 *
 * The release chip replaces a primary navigation that had exactly one destination and so
 * promised somewhere to go while delivering nothing. What belongs in that space is the
 * product's boundary: which approved release a question will be checked against. It was
 * previously discoverable only after a run, inside the provenance strip, which is
 * backwards - the corpus is what decides whether a question can be answered at all.
 */
export function AppHeader({ corpusStatus }: { corpusStatus: CorpusStatus }) {
  return (
    <>
      <a className={styles["skip-link"]} href="#main-content">
        Skip to main content
      </a>
      <header className={styles.topbar}>
        <div className={styles["topbar-inner"]}>
          <Link className={styles["brand-lockup"]} href="/" aria-label="Guideline Evidence QA home">
            <span className={styles["brand-mark"]} aria-hidden="true">
              <ShieldPlus size={25} strokeWidth={2.4} />
            </span>
            <span className={styles["brand-name"]}>Guideline Evidence QA</span>
          </Link>

          <ReleaseChip corpusStatus={corpusStatus} />

          <div className={styles["header-status"]}>
            <span className={styles["environment-badge"]}>
              <FlaskConical size={16} aria-hidden="true" />
              Research use only
            </span>
          </div>
        </div>
        <div className={styles["safety-strip"]} role="note" aria-label="Research use notice">
          <ShieldAlert size={16} aria-hidden="true" />
          <span>
            Research prototype. Not authorized for patient care. Do not enter names, identifiers,
            or other protected health information.
          </span>
        </div>
      </header>
    </>
  );
}

/**
 * The active release, stated rather than implied.
 *
 * Readiness is preflight information, so the chip reports what the registry says and
 * nothing more: it never claims a release is serving, and the terminal result stays
 * authoritative if readiness changes between page load and submission.
 */
function ReleaseChip({ corpusStatus }: { corpusStatus: CorpusStatus }) {
  if (corpusStatus.isLoading) {
    return (
      <div className={styles["release-chip"]}>
        <CircleDashed className={styles.spin} size={13} aria-hidden="true" />
        <span className={styles["release-name"]}>Checking corpus</span>
      </div>
    );
  }

  // Research serving answers from a release that never passed activation. Reporting it
  // as "answers will be withheld" would be false in the opposite direction from calling
  // it approved, so it gets its own chip rather than being folded into either.
  if (corpusStatus.servingMode === "RESEARCH_UNACTIVATED") {
    return (
      <div className={`${styles["release-chip"]} ${styles["release-none"]}`}>
        <FlaskConical size={13} aria-hidden="true" />
        <span className={styles["release-name"]}>Research release</span>
        <span className={styles["release-detail"]}>not clinically accepted</span>
      </div>
    );
  }

  if (!corpusStatus.approvedCorpusAvailable) {
    return (
      <div className={`${styles["release-chip"]} ${styles["release-none"]}`}>
        <TriangleAlert size={13} aria-hidden="true" />
        <span className={styles["release-name"]}>
          {corpusStatus.registryAvailable ? "No active release" : "Corpus status unavailable"}
        </span>
        <span className={styles["release-detail"]}>answers will be withheld</span>
      </div>
    );
  }

  return (
    <div className={styles["release-chip"]}>
      <span className={styles["release-dot"]} aria-hidden="true" />
      <span className={styles["release-name"]}>Approved release</span>
      {corpusStatus.releaseId ? (
        <span className={styles["release-detail"]}>{corpusStatus.releaseId}</span>
      ) : null}
    </div>
  );
}
