import type { ReactNode } from "react";

import styles from "./shell.module.css";

/** A content page: title, one line of lede, then sections separated by rules. */
export function PageFrame({
  title,
  lede,
  narrow = false,
  children,
}: {
  title: string;
  lede?: ReactNode;
  narrow?: boolean;
  children: ReactNode;
}) {
  return (
    <main className={`${styles.page} ${narrow ? styles.narrow : ""}`} id="main-content">
      <header className={styles["page-head"]}>
        <h1>{title}</h1>
        {lede ? <p>{lede}</p> : null}
      </header>
      {children}
    </main>
  );
}
