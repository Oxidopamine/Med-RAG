import { FlaskConical, Github, Linkedin } from "lucide-react";

import { AUTHOR, hasAuthorLinks } from "@/lib/author";

import styles from "./workspace.module.css";

/**
 * The page's closing boundary and its byline.
 *
 * "Research use only" reads here rather than in the top bar. It was a third statement of
 * the same fact on a screen that already carries the safety strip and the corpus status,
 * and a badge repeated until it is furniture stops being read. At the foot of the page it
 * is the last thing under an answer, which is where the boundary actually matters.
 */
export function AppFooter() {
  return (
    <footer className={styles["app-footer"]}>
      <div className={styles["app-footer-inner"]}>
        <span className={styles["environment-badge"]}>
          <FlaskConical size={16} aria-hidden="true" />
          Research use only
        </span>

        <p className={styles["footer-byline"]}>
          Built by <span>{AUTHOR.name}</span>
        </p>

        {/* The mark alone. Two known brand glyphs beside a name read as links without
            being labelled, and the words were the widest thing in a bar whose job is to end
            the page quietly. The name still reaches assistive technology in full - as
            screen-reader text rather than an `aria-label`, so the new-tab note travels with
            it - and `title` gives a pointer user the same word on hover. */}
        {hasAuthorLinks() ? (
          <div className={styles["footer-links"]}>
            {AUTHOR.linkedin ? (
              <a
                href={AUTHOR.linkedin}
                rel="noreferrer noopener"
                target="_blank"
                title="LinkedIn"
              >
                <Linkedin size={16} aria-hidden="true" />
                <span className={styles.srOnly}>LinkedIn (opens in a new tab)</span>
              </a>
            ) : null}
            {AUTHOR.github ? (
              <a
                href={AUTHOR.github}
                rel="noreferrer noopener"
                target="_blank"
                title="GitHub"
              >
                <Github size={16} aria-hidden="true" />
                <span className={styles.srOnly}>GitHub (opens in a new tab)</span>
              </a>
            ) : null}
          </div>
        ) : null}
      </div>
    </footer>
  );
}
