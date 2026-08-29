"use client";

import { useEffect } from "react";

import styles from "./error.module.css";
import "./globals.css";

/**
 * The boundary for a throw in the root layout itself.
 *
 * This one replaces the whole document, so it renders its own `html` and `body` and keeps
 * no dependency on anything the layout would have provided. It is deliberately plainer
 * than `error.tsx`: if the failure was in the shell, the less this page needs in order to
 * draw, the better its odds of drawing at all. The one thing it will not drop is the
 * research-use notice.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Sentinel Evidence failed to load", error);
  }, [error]);

  return (
    <html lang="en">
      <body>
        <div className={styles.shell}>
          <main className={styles.card} role="alert" aria-labelledby="global-error-title">
            <p className={styles.notice}>
              Research prototype. Not authorized for patient care.
            </p>

            <h1 className={styles.title} id="global-error-title">
              Sentinel Evidence could not start
            </h1>
            <p className={styles.body}>
              The application stopped before it could load. Nothing was retrieved, verified,
              or displayed, so there is no result on this page to act on.
            </p>

            <div className={styles.actions}>
              <button className={styles.primary} type="button" onClick={reset}>
                Reload the application
              </button>
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
      </body>
    </html>
  );
}
