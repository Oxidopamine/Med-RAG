"use client";

import {
  ChevronDown,
  Download,
  FlaskConical,
  Link2,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useId, useState } from "react";

import type { QuestionResult } from "@/lib/types";

import styles from "./workspace.module.css";

/**
 * Where this answer's authority comes from, and the three ways to carry it off the page.
 *
 * Three states, not two. A release served for research has not passed activation, and
 * presenting it with the same shield and the same "Approved" wording as an activated one
 * would make this strip assert the exact governance claim the release does not carry. The
 * API distinguishes them; so must the surface that reads it. The state is set once, on
 * the section, and the pill, the rail and the icon all read it from there - three
 * independent conditionals over the same fact is how they drift apart.
 *
 * Layout is three zones, in the order the question gets asked: what was served, which
 * release and run served it, and what you can do with the record. The actions are one
 * segmented group rather than three loose controls, because they are one kind of thing -
 * ways of taking this run somewhere else - and none of them outranks the other two. In
 * particular none of them is the black button: the loudest control on the page belongs to
 * asking a question, not to copying a link to one.
 */
export function ProvenanceStrip({
  onCopyLink,
  onExportAudit,
  result,
}: {
  onCopyLink: () => void;
  onExportAudit: () => void;
  result: QuestionResult;
}) {
  const [recordOpen, setRecordOpen] = useState(false);
  const recordId = useId();
  const release = result.corpus_release;
  const isResearch = release?.serving_mode === "RESEARCH_UNACTIVATED";
  const state = !release ? "unreleased" : isResearch ? "research" : "approved";
  const StateIcon = !release ? TriangleAlert : isResearch ? FlaskConical : ShieldCheck;

  return (
    <section
      className={`${styles["provenance-strip"]} ${styles[`provenance-${state}`]}`}
      aria-label="Review provenance"
    >
      <div className={styles["provenance-bar"]}>
        <div className={styles["provenance-claim"]}>
          <span className={styles["provenance-status"]}>
            <StateIcon size={13} aria-hidden="true" />
            {!release
              ? "No approved corpus release"
              : isResearch
                ? "Research serving — not clinically accepted"
                : "Approved corpus release"}
          </span>
          <p className={styles["provenance-note"]}>
            {!release
              ? "The answer gate did not have an active approved source release."
              : isResearch
                ? "Validated release, served without activation."
                : `Passed the signed activation gate · activated ${formatDate(
                    release.activated_at ?? "",
                  )}`}
          </p>
        </div>

        {/* Truncated here, in full in the record below: the bar is for telling two runs
            apart at a glance, and a 34-character identifier set at full length crowds out
            the claim it sits beside. Both carry the whole value in `title`. */}
        <dl className={styles["provenance-ids"]}>
          {release ? (
            <div>
              <dt>Release</dt>
              <dd title={release.corpus_release_id}>{shortId(release.corpus_release_id)}</dd>
            </div>
          ) : null}
          <div>
            <dt>Run</dt>
            <dd title={result.question_id}>{shortId(result.question_id)}</dd>
          </div>
        </dl>

        {/* A link, not an identifier. A run id is what you paste into a support ticket; a
            URL is what a reviewer opens - and runs have had one since /r/[questionId].
            Export sits here too, because it exports the run, and this strip is what names
            the run. */}
        <div className={styles["provenance-actions"]}>
          {release ? (
            <button
              type="button"
              aria-controls={recordId}
              aria-expanded={recordOpen}
              onClick={() => setRecordOpen((open) => !open)}
            >
              <ChevronDown className={styles["provenance-caret"]} size={14} aria-hidden="true" />
              Release details
            </button>
          ) : null}
          <button type="button" onClick={onCopyLink}>
            <Link2 size={14} aria-hidden="true" />
            Copy link
          </button>
          <button type="button" onClick={onExportAudit}>
            <Download size={14} aria-hidden="true" />
            Export audit record
          </button>
        </div>
      </div>

      {/* Opens in flow rather than as a popover. A floating panel outlives the reader's
          interest in it - nothing dismisses it but the same click again - and 320px of
          overlay is a worse place to read a 64-character digest than the full width of
          the strip that already owns the fact. */}
      {release ? (
        <dl className={styles["provenance-record"]} hidden={!recordOpen} id={recordId}>
          <div>
            <dt>Release ID</dt>
            <dd>{release.corpus_release_id}</dd>
          </div>
          <div>
            <dt>Run ID</dt>
            <dd>{result.question_id}</dd>
          </div>
          <div>
            <dt>{isResearch ? "Activation" : "Activated"}</dt>
            <dd>
              {isResearch
                ? "Not activated — no signed activation decision"
                : formatDateTime(release.activated_at ?? "")}
            </dd>
          </div>
          <div className={styles["provenance-record-wide"]}>
            <dt>Manifest SHA-256</dt>
            <dd>{release.manifest_sha256}</dd>
          </div>
        </dl>
      ) : null}
    </section>
  );
}

/* Head and tail, never the middle: the prefix says what kind of identifier it is and the
   tail is what actually differs between two runs of the same release. */
function shortId(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 11)}…${value.slice(-4)}`;
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
