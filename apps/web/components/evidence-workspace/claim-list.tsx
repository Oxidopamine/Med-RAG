import type { Citation, CitationIndex } from "@/lib/evidence-presentation";
import { claimCitations, claimRoles, humanizeCode } from "@/lib/evidence-presentation";
import type { QuestionResult, RenderedClaim } from "@/lib/types";

import { ClaimFlag } from "./claim-flag";
import styles from "./workspace.module.css";

interface ClaimListProps {
  citations: CitationIndex;
  claims: RenderedClaim[];
  onInspectClaim?: (claimId: string) => void;
  onSelectClaim: (claimId: string) => void;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}

/**
 * The claims as numbered recommendations.
 *
 * Each is set in the reading face with its citations as superscript numbers, the way a
 * recommendation is printed in the guideline it came from. The number opens the passage
 * in the source pane; the line under the claim says what kind of evidence carried it.
 */
export function ClaimList({
  citations,
  claims,
  onInspectClaim,
  onSelectClaim,
  onSelectEvidence,
  result,
  selectedClaimId,
  selectedEvidenceId,
}: ClaimListProps) {
  return (
    <ol className={`${styles.recommendations} reading`} aria-label="Recommendations">
      {claims.map((claim) => {
        const cited = claimCitations(claim, citations);
        const roles = claimRoles(claim, result);
        const isSelected = claim.claim_id === selectedClaimId;
        return (
          <li
            className={`${styles.recommendation} ${isSelected ? styles["selected-claim"] : ""}`}
            key={claim.claim_id}
          >
            <p className={styles["recommendation-text"]}>
              {claim.text}
              {cited.length ? (
                <sup className={styles.cites}>
                  {cited.map((citation) => (
                    <CitationMark
                      citation={citation}
                      isSelected={isSelected && citation.detail.evidence_id === selectedEvidenceId}
                      key={citation.detail.evidence_id}
                      onSelect={() => {
                        onSelectClaim(claim.claim_id);
                        onSelectEvidence(citation.detail.evidence_id);
                        onInspectClaim?.(claim.claim_id);
                      }}
                    />
                  ))}
                </sup>
              ) : null}
            </p>
            {cited.length ? (
              <p className={styles["recommendation-meta"]}>
                {humanizeCode(claim.verification_status)}
                {roles.length ? (
                  <>
                    {" · "}
                    {roles.map((role, roleIndex) => (
                      <span className={styles[`role-${role.tone}`]} key={role.code}>
                        {roleIndex > 0 ? ", " : ""}
                        {role.label.toLowerCase()}
                        {role.count > 1 ? ` (${role.count})` : ""}
                      </span>
                    ))}
                  </>
                ) : null}
              </p>
            ) : (
              <p className={styles["recommendation-missing"]} role="note">
                The evidence for this claim could not be shown.
              </p>
            )}
            <ClaimFlag
              claimId={claim.claim_id}
              claimText={claim.text}
              evidenceIds={claim.evidence_ids}
              questionId={result.question_id}
            />
          </li>
        );
      })}
    </ol>
  );
}

interface CitationMarkProps {
  citation: Citation;
  isSelected: boolean;
  onSelect: () => void;
}

/** A superscript reference number that opens its passage. */
export function CitationMark({ citation, isSelected, onSelect }: CitationMarkProps) {
  const restricted = citation.policy.licenceRestricted;
  return (
    <button
      aria-label={`Reference ${citation.number}: ${citation.shortLabel}, ${citation.locationLabel}${
        restricted ? ". Licence does not permit showing the passage text" : ""
      }`}
      aria-pressed={isSelected}
      className={`${styles.cite} ${restricted ? styles["cite-restricted"] : ""}`}
      onClick={onSelect}
      type="button"
    >
      {citation.number}
    </button>
  );
}

/** Kept for the conflict comparison, which names passages by their reference number. */
export function CitationChip({ citation, isSelected, onSelect }: CitationMarkProps) {
  const restricted = citation.policy.licenceRestricted;
  return (
    <button
      aria-label={`Reference ${citation.number}: ${citation.shortLabel}, ${citation.locationLabel}${
        restricted ? ". Licence does not permit showing the passage text" : ""
      }`}
      aria-pressed={isSelected}
      className={`${styles["reference-number"]} ${isSelected ? styles.selected : ""}`}
      onClick={onSelect}
      type="button"
    >
      {citation.number}
      {" "}
      <span className={styles["reference-copy"]}>
        {citation.shortLabel}, {citation.locationLabel}
        {restricted ? <span className={styles["reference-note"]}>Text not shown (licence).</span> : null}
      </span>
    </button>
  );
}
