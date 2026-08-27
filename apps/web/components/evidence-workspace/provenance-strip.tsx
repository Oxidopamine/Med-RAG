import { Database, Download, FlaskConical, Link2, ShieldCheck, TriangleAlert } from "lucide-react";

import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

export function ProvenanceStrip({
  onCopyLink,
  onExportAudit,
  result,
}: {
  onCopyLink: () => void;
  onExportAudit: () => void;
  result: QuestionResult;
}) {
  const release = result.corpus_release;
  // Three states, not two. A release served for research has not passed activation, and
  // presenting it with the same shield and the same "Approved" wording as an activated
  // one would make this strip assert the exact governance claim the release does not
  // carry. The API distinguishes them; so must the surface that reads it.
  const isResearch = release?.serving_mode === "RESEARCH_UNACTIVATED";
  return (
    <section
      className={`${styles["provenance-strip"]} ${
        release && !isResearch ? "" : styles["provenance-unreleased"]
      }`}
      aria-label="Review provenance"
    >
      <div className={styles["provenance-primary"]}>
        {!release ? (
          <TriangleAlert size={18} aria-hidden="true" />
        ) : isResearch ? (
          <FlaskConical size={18} aria-hidden="true" />
        ) : (
          <ShieldCheck size={18} aria-hidden="true" />
        )}
        <div>
          <strong>
            {!release
              ? "No approved corpus release"
              : isResearch
                ? "Research serving — not clinically accepted"
                : "Approved corpus release"}
          </strong>
          <span>
            {!release
              ? "The answer gate did not have an active approved source release."
              : isResearch
                ? `${release.corpus_release_id} · validated release, served without activation`
                : `${release.corpus_release_id} · activated ${formatDate(release.activated_at ?? "")}`}
          </span>
          <span className={styles["provenance-run"]}>{result.question_id}</span>
        </div>
      </div>
      {release ? (
        <details>
          <summary>
            <Database size={15} aria-hidden="true" />
            Release details
          </summary>
          <dl>
            <div>
              <dt>Release ID</dt>
              <dd>{release.corpus_release_id}</dd>
            </div>
            <div>
              <dt>{isResearch ? "Activation" : "Activated"}</dt>
              <dd>
                {isResearch
                  ? "Not activated — no signed activation decision"
                  : formatDateTime(release.activated_at ?? "")}
              </dd>
            </div>
            <div>
              <dt>Manifest</dt>
              <dd title={release.manifest_sha256}>{shortHash(release.manifest_sha256)}</dd>
            </div>
          </dl>
        </details>
      ) : null}
      {/* A link, not an identifier. A run id is what you paste into a support ticket; a
          URL is what a reviewer opens - and runs have had one since /r/[questionId].
          Export sits here too, because it exports the run, and this strip is what names
          the run. */}
      <button className={styles["primary-action"]} type="button" onClick={onCopyLink}>
        <Link2 size={15} aria-hidden="true" />
        Copy link
      </button>
      <button type="button" onClick={onExportAudit}>
        <Download size={15} aria-hidden="true" />
        Export audit record
      </button>
    </section>
  );
}

function shortHash(value: string): string {
  return `${value.slice(0, 10)}…${value.slice(-8)}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
