"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageSurface, useSourcePage } from "@/components/evidence-workspace/source-page";
import styles from "@/components/shell/shell.module.css";
import { getCorpusCatalogue } from "@/lib/api";
import type { CorpusCatalogueSource } from "@/lib/contracts";
import { withRetries } from "@/lib/readiness";

type Load =
  | { status: "loading" }
  | { status: "found"; source: CorpusCatalogueSource }
  | { status: "missing" }
  | { status: "error"; message: string };

function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

/**
 * A guideline as a document: its editions and their terms, and the pages themselves
 * where the publisher's licence permits rendering them. Where it does not, the page
 * says so once and points at the publisher.
 */
export function SourceReader({ sourceId }: { sourceId: string }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let live = true;
    withRetries(getCorpusCatalogue, { delays: [1_000, 3_000] })
      .then((catalogue) => {
        if (!live) return;
        const source = catalogue.sources.find((item) => item.source_id === sourceId);
        setLoad(source ? { status: "found", source } : { status: "missing" });
      })
      .catch((error: unknown) => {
        if (live) {
          setLoad({
            status: "error",
            message: error instanceof Error ? error.message : "The catalogue could not be read.",
          });
        }
      });
    return () => {
      live = false;
    };
  }, [sourceId]);

  if (load.status === "loading") return <p className={styles.empty}>Reading the catalogue.</p>;
  if (load.status === "error") {
    return (
      <div className={styles.notice} role="alert">
        The evidence service could not be reached. {load.message}
      </div>
    );
  }
  if (load.status === "missing") {
    return (
      <p className={styles.empty}>
        No document with this identifier is in the served release. See the{" "}
        <Link href="/corpus">corpus</Link>.
      </p>
    );
  }

  const { source } = load;
  const pageCount = Math.max(...source.versions.map((version) => version.page_count ?? 0), 0);

  return (
    <>
      <section aria-labelledby="source-about">
        <h2 id="source-about">{source.title}</h2>
        <p className={styles.prose}>
          {source.publisher_name}. Jurisdiction {source.jurisdiction.toLowerCase() === "world" ? "world" : source.jurisdiction}.{" "}
          <a href={source.canonical_url} rel="noreferrer noopener" target="_blank">
            Open at the publisher
            <span className={styles.srOnly}> (opens in a new tab)</span>
          </a>
        </p>
        <div className={styles["table-wrap"]}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Edition</th>
                <th scope="col">Status</th>
                <th scope="col">In force</th>
                <th scope="col" className={styles.num}>
                  Evidence
                </th>
                <th scope="col" className={styles.num}>
                  Pages
                </th>
              </tr>
            </thead>
            <tbody>
              {source.versions.map((version) => (
                <tr key={version.source_version_id}>
                  <td>{version.version_label}</td>
                  <td>{version.status.toLowerCase().replaceAll("_", " ")}</td>
                  <td>
                    {version.effective_from ? `From ${formatDate(version.effective_from)}` : "Not stated"}
                    {version.effective_to ? ` to ${formatDate(version.effective_to)}` : ""}
                  </td>
                  <td className={styles.num}>{version.evidence_count}</td>
                  <td className={styles.num}>{version.page_count ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={`${styles.prose} ${styles.muted}`}>
          Licence:{" "}
          {source.license_render_allowed
            ? "passages may be quoted and pages rendered."
            : source.license_excerpt_allowed
              ? "passages may be quoted; page images are not reproduced."
              : "only locations are stated; passage text is not reproduced."}
        </p>
      </section>

      <section aria-labelledby="source-pages">
        <h2 id="source-pages">Pages</h2>
        {source.license_render_allowed ? (
          <PageBrowser pageCount={pageCount} sourceId={source.source_id} />
        ) : (
          <p className={styles.prose}>
            The publisher&apos;s licence does not permit rendering this document&apos;s pages
            here. Passages quoted in a review carry their page and location, and the document
            can be read at the publisher.
          </p>
        )}
      </section>
    </>
  );
}

function PageBrowser({ pageCount, sourceId }: { pageCount: number; sourceId: string }) {
  const [page, setPage] = useState(1);
  const { load, pageCount: knownCount, reload } = useSourcePage(sourceId, page, true);
  const last = knownCount || pageCount || 1;
  return (
    <div>
      <div className={styles.actions} data-print="hide">
        <button
          className={styles.button}
          disabled={page <= 1}
          onClick={() => setPage((value) => Math.max(1, value - 1))}
          type="button"
        >
          Previous page
        </button>
        <span className={styles.muted}>
          Page {page} of {last}
        </span>
        <button
          className={styles.button}
          disabled={page >= last}
          onClick={() => setPage((value) => Math.min(last, value + 1))}
          type="button"
        >
          Next page
        </button>
      </div>
      <PageSurface
        ariaLabel={`Page ${page} of the document`}
        canShowContent
        caption={`Page ${page}`}
        load={load}
        onRetry={reload}
        regions={[]}
        showRegions={false}
      />
    </div>
  );
}
