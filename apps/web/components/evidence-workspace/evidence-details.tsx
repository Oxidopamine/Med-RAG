import {
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  FileSearch,
  FileText,
  Info,
  MapPin,
  Pencil,
} from "lucide-react";

import type { CitationIndex } from "@/lib/evidence-presentation";
import {
  humanizeCode,
  locationSummary,
  renderPolicy,
} from "@/lib/evidence-presentation";
import { contextRows } from "@/lib/presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type {
  ClinicalContext,
  EvidenceDetail,
  QuestionResult,
  RenderedClaim,
} from "@/lib/types";

import { ConflictPanel } from "./conflict-panel";
import { PassageBody, SourceAnchorList } from "./source-anchor";
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
      <div className={styles["support-grid"]}>
        <InterpretedContextPanel
          allowEditing={allowContextEditing}
          context={context}
          onEdit={onEditContext}
        />
        <RelevantEvidencePanel
          citations={citations}
          evidence={claimEvidence}
          onSelectEvidence={onSelectEvidence}
          selectedEvidenceId={selectedCandidate ? null : selected?.evidence_id ?? null}
        />
      </div>
      <RankedCandidatePanel
        candidates={candidates}
        onSelectEvidence={onSelectEvidence}
        selectedEvidenceId={selectedCandidate?.detail.evidence_id ?? null}
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

      {evidence ? (
        <>
          <dl className={styles["evidence-metadata"]}>
            <div>
              <dt>Document</dt>
              <dd>{evidence.source_title}</dd>
            </div>
            <div>
              <dt>Version</dt>
              <dd>{evidence.source_version_label}</dd>
            </div>
            <div>
              <dt>Publisher</dt>
              <dd>{evidence.publisher_name}</dd>
            </div>
            <div>
              <dt>Jurisdiction</dt>
              <dd>{evidence.jurisdiction}</dd>
            </div>
            <div>
              <dt>Section</dt>
              <dd>{evidence.section_path.join(" › ") || "Not supplied"}</dd>
            </div>
            <div>
              <dt>Location</dt>
              <dd>{locationSummary(evidence)}</dd>
            </div>
            <div>
              <dt>Effective</dt>
              <dd>{dateRange(evidence.effective_from, evidence.effective_to)}</dd>
            </div>
            <div>
              <dt>Evidence roles</dt>
              <dd>{evidence.evidence_roles.map(humanizeCode).join(", ")}</dd>
            </div>
            <div>
              <dt>Evidence type</dt>
              <dd>
                {evidence.evidence_type ? humanizeCode(evidence.evidence_type) : "Not supplied"}
              </dd>
            </div>
            <div>
              <dt>Lifecycle</dt>
              <dd>{humanizeCode(evidence.lifecycle_status)}</dd>
            </div>
          </dl>

          <div className={styles["anchor-block"]}>
            <h3>Source anchors</h3>
            <SourceAnchorList detail={evidence} />
          </div>

          <div className={styles["evidence-actions"]}>
            <a href={evidence.source_url} target="_blank" rel="noreferrer">
              <ExternalLink size={16} aria-hidden="true" />
              Open publisher source
            </a>
            <a href="#source-heading">
              <MapPin size={16} aria-hidden="true" />
              Inspect source location
            </a>
          </div>
        </>
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
          {rows.map(([label, value], index) => (
            <div key={`${label}-${value}-${index}`}>
              <dt>{label}</dt>
              <dd>{value}</dd>
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

function RelevantEvidencePanel({
  citations,
  evidence,
  onSelectEvidence,
  selectedEvidenceId,
}: {
  citations: CitationIndex;
  evidence: EvidenceDetail[];
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}) {
  return (
    <section className={`${styles.panel} ${styles["mini-panel"]}`} aria-labelledby="related-heading">
      <h2 id="related-heading">Supporting evidence</h2>
      {evidence.length ? (
        <div className={styles["related-list"]}>
          {evidence.map((detail) => {
            const number = citations.byEvidenceId.get(detail.evidence_id)?.number ?? null;
            return (
              <button
                className={detail.evidence_id === selectedEvidenceId ? styles.selected : ""}
                type="button"
                key={detail.evidence_id}
                onClick={() => onSelectEvidence(detail.evidence_id)}
                aria-pressed={detail.evidence_id === selectedEvidenceId}
              >
                {number === null ? (
                  <FileText size={22} aria-hidden="true" />
                ) : (
                  <span className={styles["citation-number"]} aria-hidden="true">
                    {number}
                  </span>
                )}
                <span>
                  <strong>{detail.source_title}</strong>
                  <small>
                    {detail.source_version_label} · {locationSummary(detail)}
                  </small>
                </span>
                <ArrowRight size={18} aria-hidden="true" />
              </button>
            );
          })}
        </div>
      ) : (
        <div className={styles["compact-empty"]}>
          <FileText size={21} aria-hidden="true" />
          <span>No verified evidence details are available.</span>
        </div>
      )}
    </section>
  );
}

function RankedCandidatePanel({
  candidates,
  onSelectEvidence,
  selectedEvidenceId,
}: {
  candidates: RankedEvidence[];
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}) {
  if (!candidates.length) return null;
  return (
    <section
      className={`${styles.panel} ${styles["mini-panel"]}`}
      aria-labelledby="ranked-candidates-heading"
    >
      <div className={styles["mini-panel-heading"]}>
        <div>
          <h2 id="ranked-candidates-heading">Other ranked passages</h2>
          <span className={styles["neutral-label"]}>
            Retrieved for this question, cited by no claim, and verified as support for nothing
          </span>
        </div>
        <span className={styles["candidate-count"]}>
          {candidates.length} uncited
        </span>
      </div>
      <div className={styles["related-list"]}>
        {candidates.map(({ detail, retrievalRank }) => (
          <button
            className={detail.evidence_id === selectedEvidenceId ? styles.selected : ""}
            type="button"
            key={detail.evidence_id}
            onClick={() => onSelectEvidence(detail.evidence_id)}
            aria-pressed={detail.evidence_id === selectedEvidenceId}
          >
            <span className={styles["candidate-rank"]} aria-hidden="true">
              #{retrievalRank}
            </span>
            <span>
              <strong>{detail.source_title}</strong>
              <small>
                Rank {retrievalRank} · {detail.source_version_label} · {locationSummary(detail)}
              </small>
            </span>
            <ArrowRight size={18} aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  );
}

function selectedClaim(result: QuestionResult, claimId: string | null): RenderedClaim | null {
  return result.claims.find((claim) => claim.claim_id === claimId) ?? result.claims[0] ?? null;
}

function dateRange(start: string | null, end: string | null): string {
  if (!start && !end) return "Dates not supplied";
  if (start && end) return `${formatDate(start)}–${formatDate(end)}`;
  if (start) return `From ${formatDate(start)}`;
  return `Until ${formatDate(end!)}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}
