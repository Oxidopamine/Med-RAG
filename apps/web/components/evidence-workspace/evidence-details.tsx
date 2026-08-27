import {
  AlertTriangle,
  ArrowRight,
  FileSearch,
  FileText,
  Info,
  MapPin,
  Pencil,
} from "lucide-react";

import type { CitationIndex } from "@/lib/evidence-presentation";
import { humanizeCode, locationSummary, renderPolicy } from "@/lib/evidence-presentation";
import { contextRows } from "@/lib/presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type {
  ClinicalContext,
  EvidenceDetail,
  QuestionResult,
  RenderedClaim,
} from "@/lib/types";

import { ConflictPanel } from "./conflict-panel";
import { PassageBody } from "./source-anchor";
import styles from "./workspace.module.css";

interface EvidenceDetailsProps {
  allowContextEditing?: boolean;
  candidates: RankedEvidence[];
  citations: CitationIndex;
  context: ClinicalContext | null;
  onEditContext: () => void;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}

export function EvidenceDetails({
  allowContextEditing = true,
  candidates,
  citations,
  context,
  onEditContext,
  onSelectEvidence,
  result,
  selectedClaimId,
  selectedEvidenceId,
}: EvidenceDetailsProps) {
  const claim = selectedClaim(result, selectedClaimId);
  const claimEvidence = claim
    ? result.evidence_details.filter((detail) => claim.evidence_ids.includes(detail.evidence_id))
    : [];
  const selectedCandidate =
    candidates.find(({ detail }) => detail.evidence_id === selectedEvidenceId) ?? null;
  const selected =
    selectedCandidate?.detail ??
    claimEvidence.find((detail) => detail.evidence_id === selectedEvidenceId) ??
    claimEvidence[0] ??
    null;

  return (
    <>
      <ExactEvidencePanel
        candidateRank={selectedCandidate?.retrievalRank ?? null}
        citations={citations}
        claim={claim}
        evidence={selected}
      />
      <InterpretedContextPanel
        allowEditing={allowContextEditing}
        context={context}
        onEdit={onEditContext}
      />
      <RankedEvidencePanel
        candidates={candidates}
        citations={citations}
        evidence={claimEvidence}
        onSelectEvidence={onSelectEvidence}
        selectedEvidenceId={selectedEvidenceId}
      />
      <ConflictPanel
        citations={citations}
        onSelectEvidence={onSelectEvidence}
        result={result}
        selectedEvidenceId={selectedEvidenceId}
      />
    </>
  );
}

function ExactEvidencePanel({
  candidateRank,
  citations,
  claim,
  evidence,
}: {
  candidateRank: number | null;
  citations: CitationIndex;
  claim: RenderedClaim | null;
  evidence: EvidenceDetail | null;
}) {
  const isCandidate = candidateRank !== null;
  const policy = evidence ? renderPolicy(evidence) : null;
  const citationNumber = evidence
    ? citations.byEvidenceId.get(evidence.evidence_id)?.number ?? null
    : null;

  return (
    <section className={`${styles.panel} ${styles["evidence-panel"]}`} aria-labelledby="evidence-heading">
      <div className={styles["evidence-heading-row"]}>
        <div>
          <span className={styles["section-kicker"]}>
            {isCandidate
              ? `Uncited passage, retrieval rank ${candidateRank}`
              : citationNumber === null
                ? "Selected claim evidence"
                : `Reference ${citationNumber}`}
          </span>
          <h2 id="evidence-heading">
            {isCandidate
              ? "Retrieved passage, not cited"
              : policy?.canQuote
                ? "Exact guideline quotation"
                : "Evidence provenance"}
          </h2>
        </div>
        {evidence ? (
          <span className={styles["evidence-id"]}>{evidence.evidence_id}</span>
        ) : null}
      </div>

      {isCandidate ? (
        <p className={styles["uncited-inline-note"]} role="note">
          <AlertTriangle size={16} aria-hidden="true" />
          No rendered claim rests on this passage. It appears because retrieval ranked it,
          not because it was verified as support for anything above.
        </p>
      ) : null}

      {evidence ? (
        <PassageBody detail={evidence} policy={policy ?? undefined} />
      ) : (
        <div className={styles["evidence-empty"]}>
          <FileSearch size={25} aria-hidden="true" />
          <div>
            <strong>No source detail is selected</strong>
            <span>
              {claim
                ? "Select one of this claim’s verified evidence references."
                : "Select a rendered claim to inspect its verified evidence."}
            </span>
          </div>
        </div>
      )}

      {/* Identity only. The full provenance record - publisher, version, jurisdiction,
          section, effective range, evidence roles and type, lifecycle, and every source
          anchor - lives once, in the source inspector. It used to be printed verbatim in
          both places, which is most of what made this column twice as tall as the one
          beside it. */}
      {evidence ? (
        <div className={styles["evidence-identity"]}>
          <p>
            <strong>{evidence.source_title}</strong>
            <span>
              {evidence.publisher_name} &middot; {evidence.source_version_label} &middot;{" "}
              {locationSummary(evidence)}
            </span>
          </p>
          <a href="#source-heading">
            <MapPin size={16} aria-hidden="true" />
            Full provenance
          </a>
        </div>
      ) : null}
    </section>
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
          <span className={styles["neutral-label"]}>Extracted, not clinically validated</span>
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

/**
 * One ranking, with a hard line where citation stops.
 *
 * Supporting evidence and uncited candidates were two panels holding one ranking, split
 * by a distinction the reader had to carry in their head and numbered separately, which
 * hid the fact worth seeing: *where* in the ranking the gate stopped citing. Rank runs
 * continuously; the divider is the boundary; everything below it stays dashed, amber and
 * explicitly labelled as supporting nothing.
 */
function RankedEvidencePanel({
  candidates,
  citations,
  evidence,
  onSelectEvidence,
  selectedEvidenceId,
}: {
  candidates: RankedEvidence[];
  citations: CitationIndex;
  evidence: EvidenceDetail[];
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}) {
  const cited = evidence.length;
  const total = cited + candidates.length;

  return (
    <section
      className={`${styles.panel} ${styles["mini-panel"]} ${styles["ranked-evidence-panel"]}`}
      aria-labelledby="ranked-evidence-heading"
    >
      <div className={styles["mini-panel-heading"]}>
        <div>
          <h2 id="ranked-evidence-heading">Retrieved for this question</h2>
          <span className={styles["neutral-label"]}>
            Ranked by the serving path · {cited} of {total} carried a claim
          </span>
        </div>
      </div>

      {total === 0 ? (
        <div className={styles["compact-empty"]}>
          <FileText size={21} aria-hidden="true" />
          <span>No verified evidence details are available.</span>
        </div>
      ) : (
        <div className={styles["related-list"]}>
          {evidence.map((detail, index) => {
            const number = citations.byEvidenceId.get(detail.evidence_id)?.number ?? null;
            return (
              <button
                className={detail.evidence_id === selectedEvidenceId ? styles.selected : ""}
                type="button"
                key={detail.evidence_id}
                onClick={() => onSelectEvidence(detail.evidence_id)}
                aria-pressed={detail.evidence_id === selectedEvidenceId}
              >
                <span className={styles["rank-mark"]} aria-hidden="true">
                  {index + 1}
                </span>
                <span>
                  <strong>{detail.source_title}</strong>
                  <small>
                    {detail.source_version_label} · {locationSummary(detail)}
                  </small>
                </span>
                <span className={styles["rank-why"]}>
                  {number === null ? "cited" : `cited as [${number}]`}
                </span>
                <ArrowRight size={18} aria-hidden="true" />
              </button>
            );
          })}

          {candidates.length ? (
            <p className={styles["citation-boundary"]} role="note">
              <span>Citation stops here</span>
              <span>
                {candidates.length} retrieved, none carried a claim
              </span>
            </p>
          ) : null}

          {candidates.map(({ detail, retrievalRank }) => (
            <button
              className={`${styles.candidate} ${detail.evidence_id === selectedEvidenceId ? styles.selected : ""}`}
              type="button"
              key={detail.evidence_id}
              onClick={() => onSelectEvidence(detail.evidence_id)}
              aria-pressed={detail.evidence_id === selectedEvidenceId}
            >
              <span className={styles["candidate-rank"]} aria-hidden="true">
                {retrievalRank}
              </span>
              <span>
                <strong>{detail.source_title}</strong>
                <small>
                  {detail.source_version_label} · {locationSummary(detail)}
                </small>
              </span>
              <span className={styles["rank-why"]}>
                {detail.evidence_roles.map(humanizeCode).join(", ") || "no role"}
              </span>
              <ArrowRight size={18} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function selectedClaim(result: QuestionResult, claimId: string | null): RenderedClaim | null {
  return result.claims.find((claim) => claim.claim_id === claimId) ?? result.claims[0] ?? null;
}
