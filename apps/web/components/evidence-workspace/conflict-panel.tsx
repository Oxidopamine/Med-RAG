import { AlertTriangle, CheckCircle2, GitCompareArrows, History, Info } from "lucide-react";

import type {
  CitationIndex,
  ConflictTone,
  PresentedConflict,
} from "@/lib/evidence-presentation";
import {
  humanizeCode,
  presentConflicts,
  reviewableConflicts,
} from "@/lib/evidence-presentation";
import type { QuestionResult } from "@/lib/types";

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
  selectedEvidenceId,
}: {
  conflict: PresentedConflict;
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}) {
  const Icon = TONE_ICONS[conflict.descriptor.tone];
  return (
    <article className={styles[`conflict-${conflict.descriptor.tone}`]}>
      <Icon size={18} aria-hidden="true" />
      <div>
        <strong className={styles["conflict-type"]}>{conflict.descriptor.label}</strong>
        <p className={styles["conflict-meaning"]}>{conflict.descriptor.meaning}</p>

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
