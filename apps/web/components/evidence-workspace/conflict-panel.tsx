import { AlertTriangle, CheckCircle2, GitCompareArrows, History, Info } from "lucide-react";

import type {
  CitationIndex,
  ConflictTone,
  PresentedConflict,
} from "@/lib/evidence-presentation";
import {
  humanizeCode,
  locationSummary,
  presentConflicts,
  reviewableConflicts,
} from "@/lib/evidence-presentation";
import type { EvidenceDetail, QuestionResult } from "@/lib/types";

import { CitationChip } from "./claim-list";
import styles from "./workspace.module.css";

interface ConflictPanelProps {
  citations: CitationIndex;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedEvidenceId: string | null;
}

const TONE_ICONS: Record<ConflictTone, typeof AlertTriangle> = {
  cleared: CheckCircle2,
  informational: Info,
  caution: History,
  critical: GitCompareArrows,
};

/**
 * Guideline disagreements, typed.
 *
 * For guideline evidence the right output is almost never a resolution; it is both
 * clauses, labelled with the kind of disagreement, with their provenance attached. So
 * this panel names the type, states what that type means and what to do about it, and
 * links every passage involved. It resolves nothing and merges nothing.
 */
export function ConflictPanel({
  citations,
  onSelectEvidence,
  result,
  selectedEvidenceId,
}: ConflictPanelProps) {
  const conflicts = presentConflicts(result.conflicts, citations);
  const reviewable = reviewableConflicts(conflicts);
  const cleared = conflicts.length - reviewable.length;

  return (
    <section
      className={`${styles.panel} ${styles["conflict-panel"]}`}
      aria-labelledby="conflict-heading"
    >
      <div className={styles["conflict-heading-row"]}>
        <h2 id="conflict-heading">Guideline conflict review</h2>
        <span className={reviewable.length ? styles["conflict-count"] : styles["no-conflict"]}>
          {reviewable.length
            ? `${reviewable.length} for review`
            : conflicts.length
              ? "Checked, none open"
              : "None returned"}
        </span>
      </div>

      {reviewable.length ? (
        <div className={styles["conflict-list"]}>
          {reviewable.map((conflict) => (
            <ConflictCard
              conflict={conflict}
              key={conflict.key}
              onSelectEvidence={onSelectEvidence}
              result={result}
              selectedEvidenceId={selectedEvidenceId}
            />
          ))}
        </div>
      ) : (
        <div className={styles["conflict-info"]}>
          <CheckCircle2 size={19} aria-hidden="true" />
          <span>
            {conflicts.length
              ? `The answer lane compared the cited passages and reported no open disagreement across ${cleared} check${cleared === 1 ? "" : "s"}.`
              : "No conflict was returned for the rendered claims."}
          </span>
        </div>
      )}
    </section>
  );
}

function ConflictCard({
  conflict,
  onSelectEvidence,
  result,
  selectedEvidenceId,
}: {
  conflict: PresentedConflict;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedEvidenceId: string | null;
}) {
  const Icon = TONE_ICONS[conflict.descriptor.tone];
  const compared = comparedPassages(conflict, result);
  return (
    <article className={styles[`conflict-${conflict.descriptor.tone}`]}>
      <Icon size={18} aria-hidden="true" />
      <div>
        <strong className={styles["conflict-type"]}>{conflict.descriptor.label}</strong>
        <p className={styles["conflict-meaning"]}>{conflict.descriptor.meaning}</p>

        {/* The thing being compared is two passages. Describing them in prose asks the
            reader to take the comparison on trust, in the one section whose whole purpose
            is not resolving the disagreement for them. Shown only when exactly two
            records resolve - three passages are a list, not a comparison. */}
        {compared ? (
          <div className={styles["conflict-compare"]}>
            {compared.map((detail) => {
              const superseded = detail.lifecycle_status !== "CURRENT";
              return (
                <button
                  className={`${styles["compare-side"]} ${superseded ? styles["compare-superseded"] : ""} ${detail.evidence_id === selectedEvidenceId ? styles.selected : ""}`}
                  key={detail.evidence_id}
                  onClick={() => onSelectEvidence(detail.evidence_id)}
                  type="button"
                >
                  <span className={styles["compare-state"]}>
                    {superseded ? humanizeCode(detail.lifecycle_status) : "In force"}
                    <span>{detail.source_version_label}</span>
                  </span>
                  <span className={styles["compare-passage"]}>
                    {detail.exact_text ?? "Passage text withheld by licence."}
                  </span>
                  <span className={styles["compare-meta"]}>
                    {detail.publisher_name} · {locationSummary(detail)} ·{" "}
                    {effectiveRange(detail)}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}

        {conflict.unrecognizedType ? (
          <p className={styles["conflict-unrecognized"]}>
            Reported as <code>{conflict.unrecognizedType}</code>, which this interface does
            not classify.
          </p>
        ) : null}

        {conflict.summary ? (
          <p className={styles["conflict-summary"]}>{conflict.summary}</p>
        ) : null}

        {conflict.citations.length ? (
          <ul className={styles["conflict-citations"]} aria-label="Passages in conflict">
            {conflict.citations.map((citation) => (
              <li key={citation.detail.evidence_id}>
                <CitationChip
                  citation={citation}
                  isSelected={citation.detail.evidence_id === selectedEvidenceId}
                  onSelect={() => onSelectEvidence(citation.detail.evidence_id)}
                />
              </li>
            ))}
          </ul>
        ) : null}

        {conflict.unresolvedEvidenceIds.length ? (
          <p className={styles["conflict-unresolved"]}>
            Also names {conflict.unresolvedEvidenceIds.join(", ")}, which no rendered claim
            cites.
          </p>
        ) : null}

        {conflict.extraFields.length ? (
          <dl>
            {conflict.extraFields.map(([field, value]) => (
              <div key={field}>
                <dt>{humanizeCode(field)}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        <p className={styles["conflict-guidance"]}>{conflict.descriptor.guidance}</p>
      </div>
    </article>
  );
}

/**
 * The two records a conflict is about, ordered current-first.
 *
 * Exactly two, both resolvable, or nothing: a comparison of three is a list, and a
 * comparison with a passage the reader cannot open is an assertion about evidence they
 * cannot check. Where lifecycle separates them the current edition leads, because that is
 * the one in force.
 */
function comparedPassages(
  conflict: PresentedConflict,
  result: QuestionResult,
): [EvidenceDetail, EvidenceDetail] | null {
  if (conflict.evidenceIds.length !== 2) return null;
  const byId = new Map(result.evidence_details.map((detail) => [detail.evidence_id, detail]));
  const resolved = conflict.evidenceIds.flatMap((evidenceId) => {
    const detail = byId.get(evidenceId);
    return detail ? [detail] : [];
  });
  if (resolved.length !== 2) return null;
  const [left, right] = resolved as [EvidenceDetail, EvidenceDetail];
  if (left.lifecycle_status !== "CURRENT" && right.lifecycle_status === "CURRENT") {
    return [right, left];
  }
  return [left, right];
}

function effectiveRange(detail: EvidenceDetail): string {
  if (!detail.effective_from && !detail.effective_to) return "dates not supplied";
  if (detail.effective_from && detail.effective_to) {
    return `${detail.effective_from} to ${detail.effective_to}`;
  }
  return detail.effective_from ? `from ${detail.effective_from}` : `until ${detail.effective_to}`;
}
