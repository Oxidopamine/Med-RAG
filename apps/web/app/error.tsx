"use client";

import { RotateCw, ShieldAlert } from "lucide-react";
import { useEffect } from "react";

import styles from "./error.module.css";

/**
 * What a render-time throw shows instead of a blank page.
 *
 * The asynchronous paths already fail safely - a rejected contract is caught before it
 * reaches state, and readiness has its own handler. This covers the one remaining way the
 * interface can go silent: a component throwing while rendering. Next's default screen
 * for that is indistinguishable from "no answer" while carrying none of the framing that
 * makes an abstention safe, so this boundary keeps the research-use notice, says plainly
 * that nothing clinical was rendered, and preserves the run identifier so the failure can
 * be reported against a specific review.
 */
export default function WorkspaceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Evidence workspace render error", error);
  }, [error]);

  return (
    <div className={styles.shell}>
      <main className={styles.card} role="alert" aria-labelledby="error-title">
        <p className={styles.notice}>
          <ShieldAlert size={16} aria-hidden="true" />
          Research prototype. Not authorized for patient care.
        </p>

        <h1 className={styles.title} id="error-title">
          This review could not be displayed
        </h1>
        <p className={styles.body}>
          The interface stopped before it finished drawing the page. No clinical claim,
          quotation, or source was rendered, and nothing shown before the failure should be
          relied on.
        </p>
        <p className={styles.body}>
          Reloading the review is safe: the result is held on the server and is re-fetched,
          not recomputed.
        </p>

        <RunIdentifier />

        <div className={styles.actions}>
          <button className={styles.primary} type="button" onClick={reset}>
            <RotateCw size={16} aria-hidden="true" />
            Try displaying it again
          </button>
          {/* A real navigation, not a client-side one. Something in the React tree has
              already thrown, so this deliberately reloads the document rather than asking
              the router that may be part of the problem to re-render into it. */}
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a className={styles.secondary} href="/">
            Start a new review
          </a>
        </div>

        <details className={styles.details}>
          <summary>Technical details</summary>
          <pre>
            {error.digest ? `digest: ${error.digest}\n` : ""}
            {error.message || "No message was attached to the error."}
          </pre>
        </details>
      </main>
    </div>
  );
}

/**
 * The run identifier, recovered from the address bar rather than from state.
 *
 * React state is gone by the time this boundary renders, but `/r/[questionId]` puts the
 * run in the URL, and that identifier is the only thing that makes this failure reportable
 * against a specific review.
 */
function RunIdentifier() {
  const questionId = readRunIdFromPath();
  if (!questionId) return null;
  return (
    <p className={styles.runId}>
      Run ID <code>{questionId}</code> — quote this when reporting the problem.
    </p>
  );
}

function readRunIdFromPath(): string | null {
  if (typeof window === "undefined") return null;
  const match = /^\/r\/([^/]+)/.exec(window.location.pathname);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}
