"use client";

import {
  BookOpenCheck,
  Github,
  Linkedin,
  Menu,
  ScanSearch,
  ShieldAlert,
  X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { AUTHOR, hasAuthorLinks } from "@/lib/author";

import styles from "./workspace.module.css";

/**
 * The one piece of chrome that holds what is true of the product rather than of a run.
 *
 * Everything in here is background a reader wants once - what the tool reads, what it does
 * with it, where its boundary is - and never again while they work. Kept behind a control,
 * it stops competing with the answer; kept out of a route, it stops being a page nobody
 * navigates to. It is a disclosure, not a navigation menu: no item here goes anywhere
 * except the two author links, so it is announced as expandable rather than as a menubar.
 */
export function AppMenu() {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      // Escape returns the caret to the control that opened the panel; leaving focus on a
      // node that has just been unmounted drops the keyboard user back at the document.
      triggerRef.current?.focus();
    }

    // The outside click, not pointerdown: closing before the click completes can move the
    // control under the pointer, and the click then lands on whatever took its place.
    function onOutsideClick(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (containerRef.current?.contains(target)) return;
      setOpen(false);
    }

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("click", onOutsideClick);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("click", onOutsideClick);
    };
  }, [open]);

  return (
    <div className={styles["app-menu"]} ref={containerRef}>
      <button
        aria-controls={panelId}
        aria-expanded={open}
        aria-label={open ? "Close menu" : "Open menu"}
        className={styles["menu-trigger"]}
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        {open ? (
          <X size={18} aria-hidden="true" />
        ) : (
          <Menu size={18} aria-hidden="true" />
        )}
      </button>

      {open ? (
        <div className={styles["menu-panel"]} id={panelId}>
          <section>
            <h2>
              <ScanSearch size={14} aria-hidden="true" />
              What this is
            </h2>
            <p>
              A research instrument for asking clinical questions of published guidelines. It
              answers only from guideline text it retrieved for your question, and every claim
              carries the page it came from so you can read the source rather than trust the
              summary.
            </p>
          </section>

          <section>
            <h2>
              <BookOpenCheck size={14} aria-hidden="true" />
              How a review runs
            </h2>
            <ol className={styles["menu-steps"]}>
              <li>
                <span>Retrieve</span> passages from the served corpus, scoped to the publishers
                you selected.
              </li>
              <li>
                <span>Verify</span> each drafted claim against those passages, and drop the ones
                the text does not carry.
              </li>
              <li>
                <span>Cite or abstain</span> — an answer arrives with anchors, or it does not
                arrive at all.
              </li>
            </ol>
          </section>

          <section>
            <h2>
              <ShieldAlert size={14} aria-hidden="true" />
              Boundary
            </h2>
            <p>
              Research use only. Not authorized for patient care, and not a substitute for the
              guideline or for clinical judgement. Do not enter names, identifiers, or other
              protected health information — questions are sent to a server and retained with
              the run record.
            </p>
          </section>

          <footer className={styles["menu-author"]}>
            <p>
              Built by <span>{AUTHOR.name}</span>
            </p>
            {hasAuthorLinks() ? (
              <div>
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
          </footer>
        </div>
      ) : null}
    </div>
  );
}
