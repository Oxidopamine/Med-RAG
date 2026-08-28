import { FileLock2, FileSearch } from "lucide-react";

import type { Citation, CitationIndex } from "@/lib/evidence-presentation";
import { claimCitations, claimRoles, humanizeCode } from "@/lib/evidence-presentation";
import type { QuestionResult, RenderedClaim } from "@/lib/types";

import styles from "./workspace.module.css";

interface ClaimListProps {
  citations: CitationIndex;
  claims: RenderedClaim[];
  /**
   * Select a claim *and* take the reader to its evidence.
   *
   * Distinct from `onSelectClaim`, which the citation chips use: clicking a reference is
   * already a click on the thing you want to read, so the inspector updating under it is
   * the whole action. "Inspect evidence" is a request to go somewhere, and on a stacked
   * layout that somewhere was several screens below the button.
   */
  onInspectClaim?: (claimId: string) => void;
  onSelectClaim: (claimId: string) => void;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}

/**
 * The rendered claims and the evidence each one rests on.
 *
 * A claim is never shown without its citations attached. Numbering comes from one index
 * built for the whole answer, so reference 2 is the same source in the claim list, the
 * conflict review, and the source inspector, and a reader can carry a number between
 * them without re-reading the titles.
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
    <ol className={styles["claim-list"]}>
      {claims.map((claim, index) => {
        const cited = claimCitations(claim, citations);
        const roles = claimRoles(claim, result);
        const isSelected = claim.claim_id === selectedClaimId;
        return (
          <li className={isSelected ? styles["selected-claim"] : ""} key={claim.claim_id}>
            <p>{claim.text}</p>

            {cited.length ? (
              <ul
                className={styles["citation-row"]}
                aria-label={`Evidence cited by claim ${index + 1}`}
              >
                {cited.map((citation) => (
                  <li key={citation.detail.evidence_id}>
                    <CitationChip
                      citation={citation}
                      isSelected={
                        isSelected && citation.detail.evidence_id === selectedEvidenceId
                      }
                      onSelect={() => {
                        onSelectClaim(claim.claim_id);
                        onSelectEvidence(citation.detail.evidence_id);
                      }}
                    />
                  </li>
                ))}
              </ul>
            ) : (
              // An answer-ready payload cannot reach this branch: the runtime contract
              // requires canonical detail for every cited evidence ID. It stays as a
              // visible statement of absence rather than a claim rendered bare.
              <p className={styles["citation-missing"]} role="note">
                Cited evidence for this claim is unavailable in this result.
              </p>
            )}

            {/* What carried this claim, on the claim itself. The gate decided whether to
                render it by looking at these roles; showing them here is the difference
                between "supported" as a badge and "supported" as a statement a reader can
                weigh. A claim resting only on background reads differently from one
                resting on a current primary guideline, and it should. */}
            {roles.length ? (
              <ul className={styles["claim-roles"]} aria-label={`Evidence roles behind claim ${index + 1}`}>
                {roles.map((role) => (
                  <li className={styles[`role-${role.tone}`]} key={role.code}>
                    {role.label}
                    {role.count > 1 ? <span aria-hidden="true"> ×{role.count}</span> : null}
                    {role.count > 1 ? <span className={styles.srOnly}>, {role.count} sources</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}

            <div className={styles["claim-footer"]}>
              <button
                type="button"
                onClick={() => (onInspectClaim ?? onSelectClaim)(claim.claim_id)}
              >
                <FileSearch size={16} aria-hidden="true" />
                Inspect evidence
              </button>
              <span>
                Claim {index + 1} of {claims.length} &middot;{" "}
                {humanizeCode(claim.verification_status)}
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

interface CitationChipProps {
  citation: Citation;
  isSelected: boolean;
  onSelect: () => void;
}

/**
 * One inline reference to a cited source.
 *
 * The licence state is part of the reference, not a detail found later: a chip whose
 * source cannot be quoted says so before the reader opens it, so nobody selects it
 * expecting a passage this deployment is not permitted to show.
 */
export function CitationChip({ citation, isSelected, onSelect }: CitationChipProps) {
  const restricted = citation.policy.licenceRestricted;
  return (
    <button
      aria-label={`Reference ${citation.number}: ${citation.shortLabel}, ${citation.locationLabel}${
        restricted ? ". Licence does not permit showing the passage text" : ""
      }`}
      aria-pressed={isSelected}
      className={`${styles["citation-chip"]} ${isSelected ? styles.selected : ""} ${
        restricted ? styles["citation-restricted"] : ""
      }`}
      onClick={onSelect}
      type="button"
    >
      <span className={styles["citation-number"]} aria-hidden="true">
        {citation.number}
      </span>
      <span className={styles["citation-copy"]}>
        <strong>{citation.shortLabel}</strong>
        <small>{citation.locationLabel}</small>
      </span>
      {restricted ? <FileLock2 size={14} aria-hidden="true" /> : null}
    </button>
  );
}
