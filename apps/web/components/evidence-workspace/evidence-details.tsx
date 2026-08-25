import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  FileLock2,
  FileSearch,
  FileText,
  Info,
  MapPin,
  Pencil,
  Quote,
} from "lucide-react";

import { contextRows, humanizeConcept } from "@/lib/presentation";
import type {
  ClinicalContext,
  EvidenceDetail,
  QuestionResult,
  RenderedClaim,
} from "@/lib/types";

import styles from "./workspace.module.css";

interface EvidenceDetailsProps {
  allowContextEditing?: boolean;
  context: ClinicalContext | null;
  onEditContext: () => void;
  onSelectEvidence: (evidenceId: string) => void;
  result: QuestionResult;
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}

export function EvidenceDetails({
  allowContextEditing = true,
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
  const selected =
    claimEvidence.find((detail) => detail.evidence_id === selectedEvidenceId) ??
    claimEvidence[0] ??
    null;

  return (
    <>
      <ExactEvidencePanel claim={claim} evidence={selected} />
      <div className={styles["support-grid"]}>
        <InterpretedContextPanel
          allowEditing={allowContextEditing}
          context={context}
          onEdit={onEditContext}
        />
        <RelevantEvidencePanel
          evidence={claimEvidence}
          onSelectEvidence={onSelectEvidence}
          selectedEvidenceId={selected?.evidence_id ?? null}
        />
      </div>
      <ConflictPanel result={result} />
    </>
  );
}

function ExactEvidencePanel({
  claim,
  evidence,
}: {
  claim: RenderedClaim | null;
  evidence: EvidenceDetail | null;
}) {
  return (
    <section className={`${styles.panel} ${styles["evidence-panel"]}`} aria-labelledby="evidence-heading">
      <div className={styles["evidence-heading-row"]}>
        <div>
          <span className={styles["section-kicker"]}>Selected claim evidence</span>
          <h2 id="evidence-heading">
            {evidence?.exact_text ? "Exact guideline quotation" : "Evidence provenance"}
          </h2>
        </div>
        {evidence ? (
          <span className={styles["evidence-id"]}>{evidence.evidence_id}</span>
        ) : null}
      </div>

      {evidence?.exact_text ? (
        <blockquote className={styles["evidence-quote"]}>
          <Quote size={25} aria-hidden="true" />
          <div>
            <p>{evidence.exact_text}</p>
            <cite>
              {evidence.publisher_name} · {evidence.source_title}
            </cite>
          </div>
        </blockquote>
      ) : evidence ? (
        <div className={styles["licensed-evidence"]}>
          <FileLock2 size={24} aria-hidden="true" />
          <div>
            <strong>Exact text is not licensed for display</strong>
            <span>
              Verified metadata remains available. Open the publisher source to review the passage.
            </span>
          </div>
        </div>
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
              <dd>{locatorLabel(evidence)}</dd>
            </div>
            <div>
              <dt>Effective</dt>
              <dd>{dateRange(evidence.effective_from, evidence.effective_to)}</dd>
            </div>
            <div>
              <dt>Evidence roles</dt>
              <dd>{evidence.evidence_roles.map(humanizeLabel).join(", ")}</dd>
            </div>
            <div>
              <dt>Evidence type</dt>
              <dd>
                {evidence.evidence_type
                  ? humanizeLabel(evidence.evidence_type)
                  : "Not supplied"}
              </dd>
            </div>
            <div>
              <dt>Lifecycle</dt>
              <dd>{humanizeLabel(evidence.lifecycle_status)}</dd>
            </div>
          </dl>
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
  evidence,
  onSelectEvidence,
  selectedEvidenceId,
}: {
  evidence: EvidenceDetail[];
  onSelectEvidence: (evidenceId: string) => void;
  selectedEvidenceId: string | null;
}) {
  return (
    <section className={`${styles.panel} ${styles["mini-panel"]}`} aria-labelledby="related-heading">
      <h2 id="related-heading">Supporting evidence</h2>
      {evidence.length ? (
        <div className={styles["related-list"]}>
          {evidence.map((detail) => (
            <button
              className={detail.evidence_id === selectedEvidenceId ? styles.selected : ""}
              type="button"
              key={detail.evidence_id}
              onClick={() => onSelectEvidence(detail.evidence_id)}
              aria-pressed={detail.evidence_id === selectedEvidenceId}
            >
              <FileText size={22} aria-hidden="true" />
              <span>
                <strong>{detail.source_title}</strong>
                <small>
                  {detail.source_version_label} · {locatorLabel(detail)}
                </small>
              </span>
              <ArrowRight size={18} aria-hidden="true" />
            </button>
          ))}
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

export function ConflictPanel({ result }: { result: QuestionResult }) {
  const hasConflicts = result.conflicts.length > 0;
  return (
    <section className={`${styles.panel} ${styles["conflict-panel"]}`} aria-labelledby="conflict-heading">
      <div className={styles["conflict-heading-row"]}>
        <h2 id="conflict-heading">Guideline conflict review</h2>
        <span className={hasConflicts ? styles["conflict-count"] : styles["no-conflict"]}>
          {hasConflicts ? `${result.conflicts.length} for review` : "None returned"}
        </span>
      </div>
      {hasConflicts ? (
        <div className={styles["conflict-list"]}>
          {result.conflicts.map((conflict, index) => (
            <article key={index}>
              <AlertTriangle size={18} aria-hidden="true" />
              <div>
                <strong>Potential conflict {index + 1}</strong>
                <dl>
                  {Object.entries(conflict).map(([label, value]) => (
                    <div key={label}>
                      <dt>{humanizeLabel(label)}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className={styles["conflict-info"]}>
          <CheckCircle2 size={19} aria-hidden="true" />
          <span>No direct conflict was returned for the rendered claims.</span>
        </div>
      )}
    </section>
  );
}

function selectedClaim(result: QuestionResult, claimId: string | null): RenderedClaim | null {
  return result.claims.find((claim) => claim.claim_id === claimId) ?? result.claims[0] ?? null;
}

function locatorLabel(evidence: EvidenceDetail): string {
  const locator = evidence.locators[0];
  if (!locator) return "Location not supplied";
  if (locator.printed_page) return `Printed page ${locator.printed_page}`;
  if (locator.pdf_page) return `PDF page ${locator.pdf_page}`;
  return humanizeLabel(locator.kind);
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

function humanizeLabel(value: string): string {
  return humanizeConcept(value).replace(/^./, (character) => character.toUpperCase());
}
