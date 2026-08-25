import { FlaskConical, ShieldAlert, ShieldPlus } from "lucide-react";
import Link from "next/link";

import styles from "./workspace.module.css";

export function AppHeader() {
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

          <nav className={styles["primary-nav"]} aria-label="Primary navigation">
            <a className={styles.active} href="#ask" aria-current="page">
              Ask
            </a>
          </nav>

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
