import type { ReactNode } from "react";

import styles from "./shell.module.css";

/**
 * A content page: a full-bleed title band on the surface colour, then the body on the
 * canvas.
 *
 * The band exists so the top of every page lands in the same place and so a page stops
 * being a stack of blocks floating at the top of a flat field. Page-level controls belong
 * in `actions`, at the band's right edge, where they read as the page's own rather than as
 * the first item of its first section.
 */
export function PageFrame({
  title,
  lede,
  actions,
  narrow = false,
  children,
}: {
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
  narrow?: boolean;
  children: ReactNode;
}) {
  return (
    <main id="main-content">
      <div className={styles.band}>
        <div className={`${styles["band-inner"]} ${narrow ? styles.narrow : ""}`}>
          <div className={styles["page-head"]}>
            <h1>{title}</h1>
            {lede ? <p>{lede}</p> : null}
          </div>
          {actions ? <div className={styles["band-actions"]}>{actions}</div> : null}
        </div>
      </div>
      <div className={`${styles.page} ${narrow ? styles.narrow : ""}`}>{children}</div>
    </main>
  );
}
