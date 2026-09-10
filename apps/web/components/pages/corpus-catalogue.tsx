"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import styles from "@/components/shell/shell.module.css";
import pageStyles from "@/components/pages/corpus.module.css";
import { getCorpusCatalogue } from "@/lib/api";
import { withRetries } from "@/lib/readiness";
import type {
  CorpusCatalogue,
  CorpusCatalogueSource,
  CorpusCatalogueVersion,
  CorpusTrustRoot,
} from "@/lib/contracts";

type Load =
  | { status: "loading" }
  | { status: "loaded"; catalogue: CorpusCatalogue }
  | { status: "error"; message: string };

type Tone = "ok" | "warn" | "danger" | "neutral";

function toneClass(tone: Tone): string {
  return `${styles.status} ${styles[tone]}`;
}

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

// `status` is the edition's place in the source's history, not a raw enum the reader
// has to decode. Anything unrecognised still gets a readable label rather than a code.
const VERSION_STATUS: Record<string, { label: string; tone: Tone }> = {
  APPROVED: { label: "Approved", tone: "ok" },
  EFFECTIVE: { label: "Effective", tone: "ok" },
  PENDING: { label: "Pending", tone: "warn" },
  PARTIALLY_SUPERSEDED: { label: "Partially superseded", tone: "warn" },
  SUPERSEDED: { label: "Superseded", tone: "neutral" },
  WITHDRAWN: { label: "Withdrawn", tone: "danger" },
};

function sentenceCase(value: string): string {
  const words = value.toLowerCase().replaceAll("_", " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : value;
}

function versionStatus(version: CorpusCatalogueVersion): { label: string; tone: Tone } {
  return (
    VERSION_STATUS[version.status] ?? {
      // A status this build has not met is still written as a word, not as the raw
      // enum: "approved" in lower case beside five sentence-case labels reads as a bug.
      label: sentenceCase(version.status),
      tone: "neutral",
    }
  );
}

// Licence terms as the two things a reader actually does with a document: see the page
// image, or only ever quote text located on it.
function licence(source: CorpusCatalogueSource): { label: string; tone: Tone } {
  if (source.license_render_allowed) return { label: "Page images", tone: "ok" };
  if (source.license_excerpt_allowed) return { label: "Quotation only", tone: "neutral" };
  return { label: "Location only", tone: "warn" };
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
            <p className={`${toneClass(release.serving_mode === "ACTIVATED" ? "ok" : "warn")} ${styles.pill}`}>
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
          <div className={styles.empty}>
            <strong>No release is being served</strong>
            <p>Reviews will abstain until one is activated.</p>
          </div>
        )}
      </section>

      <section aria-labelledby="corpus-documents">
        <h2 id="corpus-documents">Documents in the release</h2>
        {sources.length ? (
          <div className={styles["table-wrap"]}>
            <table className={styles.table} aria-label="Documents in the served release">
              <colgroup>
                <col style={{ width: "30%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "10%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Title</th>
                  <th scope="col" className={pageStyles.nowrap}>
                    Edition
                  </th>
                  <th scope="col" className={pageStyles.nowrap}>
                    In force
                  </th>
                  <th scope="col" className={pageStyles.nowrap}>
                    Status
                  </th>
                  <th scope="col" className={`${styles.num} ${pageStyles.nowrap}`}>
                    Evidence
                  </th>
                  <th scope="col" className={`${styles.num} ${pageStyles.nowrap}`}>
                    Pages
                  </th>
                  <th scope="col" className={pageStyles.nowrap}>
                    Licence
                  </th>
                </tr>
              </thead>
              <tbody>
                {sources.flatMap((source) => {
                  const sourceLicence = licence(source);
                  return source.versions.map((version, index) => {
                    const status = versionStatus(version);
                    return (
                      <tr key={version.source_version_id}>
                        {index === 0 ? (
                          <td rowSpan={source.versions.length}>
                            <Link
                              href={`/sources/${encodeURIComponent(source.source_id)}`}
                              className={styles["row-link"]}
                            >
                              {source.title}
                            </Link>
                            <span className={styles.sub}>{source.publisher_name}</span>
                          </td>
                        ) : null}
                        <td className={pageStyles.nowrap}>{version.version_label}</td>
                        <td className={pageStyles.nowrap}>
                          {effectiveRange(version.effective_from, version.effective_to)}
                        </td>
                        <td className={pageStyles.nowrap}>
                          <span className={toneClass(status.tone)}>{status.label}</span>
                        </td>
                        <td className={`${styles.num} ${pageStyles.nowrap}`}>{version.evidence_count}</td>
                        <td className={`${styles.num} ${pageStyles.nowrap}`}>{version.page_count ?? ""}</td>
                        {index === 0 ? (
                          <td rowSpan={source.versions.length} className={pageStyles.nowrap}>
                            <span className={toneClass(sourceLicence.tone)}>
                              {sourceLicence.label}
                            </span>
                          </td>
                        ) : null}
                      </tr>
                    );
                  });
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className={styles.empty}>
            <strong>No documents</strong>
            <p>The served release carries no evidence.</p>
          </div>
        )}
      </section>

      <section aria-labelledby="corpus-roots">
        <h2 id="corpus-roots">Registered publishers and scopes</h2>
        <p className={styles["section-lede"]}>
          What the instrument is registered to acquire. A scope that is registered is not
          necessarily served: only the release above answers questions.
        </p>
        {trustRoots.length ? (
          <div className={styles["table-wrap"]}>
            <table className={styles.table} aria-label="Registered publishers and scopes">
              <colgroup>
                <col style={{ width: "34%" }} />
                <col style={{ width: "20%" }} />
                <col style={{ width: "20%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "14%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Scope</th>
                  <th scope="col" className={pageStyles.nowrap}>
                    Publisher
                  </th>
                  <th scope="col">Jurisdictions</th>
                  <th scope="col" className={pageStyles.nowrap}>
                    Enabled
                  </th>
                  <th scope="col" className={pageStyles.nowrap}>
                    Last reconciled
                  </th>
                </tr>
              </thead>
              <tbody>
                {trustRoots.map((root: CorpusTrustRoot) => (
                  <tr key={root.trust_root_id}>
                    <td>
                      <strong>{root.title ?? root.trust_root_id}</strong>
                      {root.scope ? <span className={styles.sub}>{root.scope}</span> : null}
                    </td>
                    <td className={pageStyles.nowrap}>{root.publisher_name}</td>
                    <td>{root.jurisdictions.join(", ") || "World"}</td>
                    <td className={pageStyles.nowrap}>
                      <span className={toneClass(root.enabled ? "ok" : "neutral")}>
                        {root.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </td>
                    <td className={pageStyles.nowrap}>{formatDate(root.last_reconciled_at) || "Never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className={styles.empty}>
            <strong>No publishers registered</strong>
            <p>The instrument has no registered scope to acquire evidence from.</p>
          </div>
        )}
      </section>
    </>
  );
}
