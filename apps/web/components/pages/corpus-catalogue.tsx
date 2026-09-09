"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import styles from "@/components/shell/shell.module.css";
import { getCorpusCatalogue } from "@/lib/api";
import { withRetries } from "@/lib/readiness";
import type { CorpusCatalogue, CorpusCatalogueSource } from "@/lib/contracts";

type Load =
  | { status: "loading" }
  | { status: "loaded"; catalogue: CorpusCatalogue }
  | { status: "error"; message: string };

function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

function effectiveRange(from: string | null, to: string | null): string {
  if (!from && !to) return "Not stated";
  if (from && !to) return `From ${formatDate(from)}`;
  if (!from && to) return `Until ${formatDate(to)}`;
  return `${formatDate(from)} to ${formatDate(to)}`;
}

function licence(source: CorpusCatalogueSource): string {
  if (source.license_render_allowed) return "Quote and render";
  if (source.license_excerpt_allowed) return "Quote only";
  return "Location only";
}

export function CorpusCatalogueView() {
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let live = true;
    withRetries(getCorpusCatalogue, { delays: [1_000, 3_000] })
      .then((catalogue) => {
        if (live) setLoad({ status: "loaded", catalogue });
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
  }, []);

  if (load.status === "loading") {
    return <p className={styles.empty}>Reading the catalogue.</p>;
  }
  if (load.status === "error") {
    return (
      <div className={styles.notice} role="alert">
        The evidence service could not be reached, so the catalogue cannot be shown. {load.message}
      </div>
    );
  }

  const { release, sources, trust_roots: trustRoots } = load.catalogue;
  const documents = sources.length;
  const editions = sources.reduce((count, source) => count + source.versions.length, 0);
  const pages = sources.reduce(
    (count, source) =>
      count + source.versions.reduce((inner, version) => inner + (version.page_count ?? 0), 0),
    0,
  );

  return (
    <>
      <section aria-labelledby="corpus-release">
        <h2 id="corpus-release">Served release</h2>
        {release ? (
          <>
            <p className={`${styles.status} ${release.serving_mode === "ACTIVATED" ? styles.ok : styles.warn}`}>
              {release.serving_mode === "ACTIVATED"
                ? `Approved release ${release.corpus_release_id}`
                : `Research release ${release.corpus_release_id}, validated but not activated`}
            </p>
            <dl className={styles.facts}>
              <div>
                <dt>Documents</dt>
                <dd>{documents}</dd>
              </div>
              <div>
                <dt>Editions</dt>
                <dd>{editions}</dd>
              </div>
              <div>
                <dt>Evidence records</dt>
                <dd>{release.evidence_count}</dd>
              </div>
              <div>
                <dt>Pages</dt>
                <dd>{pages || "Not recorded"}</dd>
              </div>
              <div>
                <dt>{release.activated_at ? "Activated" : "Validated"}</dt>
                <dd>{formatDate(release.activated_at ?? release.validated_at) || "Not stated"}</dd>
              </div>
            </dl>
            <details className={styles.details}>
              <summary>Release details</summary>
              <dl>
                <dt>Release ID</dt>
                <dd>{release.corpus_release_id}</dd>
                <dt>Contract</dt>
                <dd>{release.contract_version}</dd>
                <dt>State</dt>
                <dd>{release.state}</dd>
                <dt>Index</dt>
                <dd>{release.index_status}</dd>
                <dt>Cut-off</dt>
                <dd>{release.cutoff_at}</dd>
                <dt>Manifest SHA-256</dt>
                <dd>{release.manifest_sha256}</dd>
                {release.activated_by ? (
                  <>
                    <dt>Activated by</dt>
                    <dd>{release.activated_by}</dd>
                  </>
                ) : null}
              </dl>
            </details>
          </>
        ) : (
          <p className={`${styles.status} ${styles.danger}`}>
            No release is being served. Reviews will abstain until one is activated.
          </p>
        )}
      </section>

      <section aria-labelledby="corpus-documents">
        <h2 id="corpus-documents">Documents in the release</h2>
        {sources.length ? (
          <div className={styles["table-wrap"]}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Title</th>
                  <th scope="col">Publisher</th>
                  <th scope="col">Edition</th>
                  <th scope="col">In force</th>
                  <th scope="col">Status</th>
                  <th scope="col" className={styles.num}>
                    Evidence
                  </th>
                  <th scope="col" className={styles.num}>
                    Pages
                  </th>
                  <th scope="col">Licence</th>
                </tr>
              </thead>
              <tbody>
                {sources.flatMap((source) =>
                  source.versions.map((version, index) => (
                    <tr key={version.source_version_id}>
                      {index === 0 ? (
                        <td rowSpan={source.versions.length}>
                          <Link href={`/sources/${encodeURIComponent(source.source_id)}`}>
                            {source.title}
                          </Link>
                        </td>
                      ) : null}
                      {index === 0 ? (
                        <td rowSpan={source.versions.length}>{source.publisher_name}</td>
                      ) : null}
                      <td>{version.version_label}</td>
                      <td>{effectiveRange(version.effective_from, version.effective_to)}</td>
                      <td>{version.status.toLowerCase().replaceAll("_", " ")}</td>
                      <td className={styles.num}>{version.evidence_count}</td>
                      <td className={styles.num}>{version.page_count ?? ""}</td>
                      {index === 0 ? (
                        <td rowSpan={source.versions.length}>{licence(source)}</td>
                      ) : null}
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <p className={styles.empty}>No documents: the served release carries no evidence.</p>
        )}
      </section>

      <section aria-labelledby="corpus-roots">
        <h2 id="corpus-roots">Registered publishers and scopes</h2>
        <p className={`${styles.prose} ${styles.muted}`}>
          What the instrument is registered to acquire. A scope that is registered is not
          necessarily served: only the release above answers questions.
        </p>
        {trustRoots.length ? (
          <div className={styles["table-wrap"]}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Scope</th>
                  <th scope="col">Publisher</th>
                  <th scope="col">Jurisdictions</th>
                  <th scope="col">Enabled</th>
                  <th scope="col">Last reconciled</th>
                </tr>
              </thead>
              <tbody>
                {trustRoots.map((root) => (
                  <tr key={root.trust_root_id}>
                    <td>
                      <strong>{root.title ?? root.trust_root_id}</strong>
                      {root.scope ? (
                        <>
                          <br />
                          <span className={styles.muted}>{root.scope}</span>
                        </>
                      ) : null}
                    </td>
                    <td>{root.publisher_name}</td>
                    <td>{root.jurisdictions.join(", ") || "World"}</td>
                    <td>{root.enabled ? "Yes" : "No"}</td>
                    <td>{formatDate(root.last_reconciled_at) || "Never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className={styles.empty}>No publishers are registered.</p>
        )}
      </section>
    </>
  );
}
