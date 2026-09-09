import { Info, Pencil } from "lucide-react";

import type { CitationIndex } from "@/lib/evidence-presentation";
import { contextRows } from "@/lib/presentation";
import type { ClinicalContext, QuestionResult } from "@/lib/types";

import { ConflictPanel } from "./conflict-panel";
import styles from "./workspace.module.css";

interface EvidenceDetailsProps {
  allowContextEditing?: boolean;
  citations: CitationIndex;
  context: ClinicalContext | null;
  onCompareEvidence?: (evidenceId: string, againstEvidenceId: string) => void;
  onEditContext: () => void;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedEvidenceId: string | null;
}

/**
 * What the answer column carries besides the answer.
 *
 * It used to carry two more things, and both were second copies of something already on
 * screen. An "exact guideline quotation" panel rendered the selected passage in this
 * column while the inspector rendered the same passage in the other one, at the same
 * time, leaving the reader to work out which was authoritative. A "retrieved for this
 * question" panel listed the same records, in the same ranking, with the same
 * citation-stops-here boundary as the inspector's rail beside it.
 *
 * Both are gone. The passage is read in one place, and the ranking is navigated in one
 * place - the rail, which now prints the reference numbers this column's claims cite, so
 * a reader can still cross between them without re-reading titles.
 */
export function EvidenceDetails({
  allowContextEditing = true,
  citations,
  context,
  onCompareEvidence,
  onEditContext,
  onSelectEvidence,
  result,
  selectedEvidenceId,
}: EvidenceDetailsProps) {
  return (
    <>
      <InterpretedContextPanel
        allowEditing={allowContextEditing}
        context={context}
        onEdit={onEditContext}
      />
      <ConflictPanel
        citations={citations}
        onCompareEvidence={onCompareEvidence}
        onSelectEvidence={onSelectEvidence}
        result={result}
        selectedEvidenceId={selectedEvidenceId}
      />
    </>
  );
}

export function InterpretedContextPanel({
  allowEditing = true,
  context,
  onEdit,
}: {
  allowEditing?: boolean;
  context: ClinicalContext | null;
  onEdit: () => void;
}) {
  const rows = contextRows(context);
  return (
    <section className={`${styles.panel} ${styles["mini-panel"]}`} aria-labelledby="context-heading">
      <div className={styles["mini-panel-heading"]}>
        <div>
          <h2 id="context-heading">Interpreted patient context</h2>
          <span className={styles["neutral-label"]}>From the question</span>
        </div>
        {context && allowEditing ? (
          <button className={styles["edit-context"]} type="button" onClick={onEdit}>
            <Pencil size={15} aria-hidden="true" />
            Edit context
          </button>
        ) : null}
      </div>
      {rows.length ? (
        <dl className={styles["context-facts"]}>
          {rows.map((row, index) => (
            <div
              className={row.inferred ? styles["context-inferred"] : undefined}
              key={`${row.label}-${row.value}-${index}`}
            >
              <dt>{row.label}</dt>
              <dd>
                {row.value}
                {/* The eye should land on what was worked out rather than read. Marked
                    per row, because correcting a guess is what the editor is for. */}
                {row.inferred ? (
                  <span className={styles["inferred-mark"]}>
                    <span aria-hidden="true">inferred</span>
                    <span className={styles.srOnly}>, inferred from the question</span>
                  </span>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className={styles["compact-empty"]}>
          <Info size={20} aria-hidden="true" />
          <span>No clinical facts were extracted.</span>
        </div>
      )}
    </section>
  );
}
