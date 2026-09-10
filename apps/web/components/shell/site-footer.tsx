import Link from "next/link";

import { BrandMark } from "@/components/evidence-workspace/brand-mark";
import { AUTHOR } from "@/lib/author";

import styles from "./shell.module.css";

/**
 * The footer states the boundary and lists the reference pages.
 *
 * It no longer repeats the served release: the header names it on every page, and on the
 * review list it was appearing in the bar, in every row and here, three times for one
 * fact.
 */
export function SiteFooter() {
  return (
    <footer className={styles.footer}>
      <div className={styles["footer-inner"]}>
        <span className={styles["footer-product"]}>
          <span className={styles["footer-mark"]} aria-hidden="true">
            <BrandMark />
          </span>
          Sentinel RAG
        </span>
        <span>Research use only. Not for patient care.</span>
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
