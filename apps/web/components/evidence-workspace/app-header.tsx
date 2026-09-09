import Link from "next/link";

import { AppMenu } from "./app-menu";
import { BrandMark } from "./brand-mark";

import styles from "./workspace.module.css";

/**
 * Product identity and the research-use boundary.
 *
 * Corpus status lives in exactly one place at a time: the "before you ask" panel while
 * idle, the provenance strip once a review runs. A third, always-on copy here read the
 * same fact three ways on one screen and went stale relative to whichever of the other
 * two had last updated - showing it nowhere beats showing it wrong.
 *
 * The research-use badge moved to the footer for the same reason. What sits here now is the
 * menu, which holds the standing facts about the product - and the safety strip, which is
 * the one statement on this page that is repeated deliberately.
 */
export function AppHeader() {
  return (
    <>
      <a className={styles["skip-link"]} href="#main-content">
        Skip to main content
      </a>
      <header className={styles.topbar}>
        <div className={styles["topbar-inner"]}>
          <Link className={styles["brand-lockup"]} href="/" aria-label="Sentinel RAG home">
            <span className={styles["brand-mark"]} aria-hidden="true">
              <BrandMark />
            </span>
            <span className={styles["brand-name"]}>Sentinel RAG</span>
          </Link>

          <div className={styles["header-status"]}>
            <AppMenu />
          </div>
        </div>
      </header>
    </>
  );
}
