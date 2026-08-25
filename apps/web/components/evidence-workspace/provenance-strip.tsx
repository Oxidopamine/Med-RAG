import { Clipboard, Database, ShieldCheck, TriangleAlert } from "lucide-react";

import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

export function ProvenanceStrip({
  onCopyRunId,
  result,
}: {
  onCopyRunId: () => void;
  result: QuestionResult;
}) {
  const release = result.corpus_release;
  return (
    <section className={styles["provenance-strip"]} aria-label="Review provenance">
      <div className={styles["provenance-primary"]}>
        {release ? (
          <ShieldCheck size={18} aria-hidden="true" />
        ) : (
          <TriangleAlert size={18} aria-hidden="true" />
        )}
        <div>
          <strong>{release ? "Approved corpus release" : "No approved corpus release"}</strong>
          <span>
            {release
              ? `${release.corpus_release_id} · activated ${formatDate(release.activated_at)}`
              : "The answer gate did not have an active approved source release."}
          </span>
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
              <dt>Activated</dt>
              <dd>{formatDateTime(release.activated_at)}</dd>
            </div>
            <div>
              <dt>Manifest</dt>
              <dd title={release.manifest_sha256}>{shortHash(release.manifest_sha256)}</dd>
            </div>
          </dl>
        </details>
      ) : null}
      <button type="button" onClick={onCopyRunId}>
        <Clipboard size={15} aria-hidden="true" />
        Copy run ID
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
