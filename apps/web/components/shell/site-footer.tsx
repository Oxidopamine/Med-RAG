import Link from "next/link";

import { AUTHOR } from "@/lib/author";

import { ReleaseChip } from "./release-chip";
import styles from "./shell.module.css";

export function SiteFooter() {
  return (
    <footer className={styles.footer}>
      <div className={styles["footer-inner"]}>
        <span className={styles["footer-product"]}>Sentinel RAG</span>
        <span>Research use only</span>
        <ReleaseChip />
        <nav className={styles["footer-links"]} aria-label="Footer">
          <Link href="/methods">Methods</Link>
          <Link href="/corpus">Corpus</Link>
          <Link href="/about#licences">Licences</Link>
          <Link href="/about">About</Link>
          {AUTHOR.github ? (
            <a href={AUTHOR.github} rel="noreferrer noopener" target="_blank">
              Contact
            </a>
          ) : null}
        </nav>
      </div>
    </footer>
  );
}
