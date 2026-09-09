import type { CitationIndex } from "@/lib/evidence-presentation";

import styles from "./workspace.module.css";

interface ReferenceListProps {
  citations: CitationIndex;
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}

/**
 * The footnotes. One entry per cited passage, numbered as the citations in the claims
 * are, in a reference form a clinician has read before: publisher, title, edition,
 * location. Choosing a number opens the passage in the source pane.
 */
export function ReferenceList({ citations, onSelectEvidence, selectedEvidenceId }: ReferenceListProps) {
  if (!citations.ordered.length) return null;
  return (
    <ol className={styles.references} aria-label="References">
      {citations.ordered.map((citation) => {
        const detail = citation.detail;
        const superseded = detail.lifecycle_status !== "CURRENT";
        return (
          <li key={detail.evidence_id} id={`reference-${citation.number}`}>
            <button
              aria-label={`Reference ${citation.number}: open the passage`}
              aria-pressed={detail.evidence_id === selectedEvidenceId}
              className={styles["reference-number"]}
              onClick={() => onSelectEvidence(detail.evidence_id)}
              type="button"
            >
              {citation.number}
            </button>
            <span className={styles["reference-copy"]}>
              {detail.publisher_name}. <em>{detail.source_title}</em>. {detail.source_version_label},{" "}
              {citation.locationLabel}.
              {superseded ? (
                <span className={styles["reference-note"]}>No longer in force.</span>
              ) : null}
              {citation.policy.licenceRestricted ? (
                <span className={styles["reference-note"]}>Text not shown (licence).</span>
              ) : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
