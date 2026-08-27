import type { ClinicalContext, EvidenceDetail, QuestionResult, QuestionStatus } from "./types";

export const STATUS_LABELS: Record<QuestionStatus, string> = {
  QUEUED: "Queued",
  CONTEXT_EXTRACTED: "Context extracted",
  RETRIEVING: "Retrieving evidence",
  RERANKING: "Reranking evidence",
  SEARCHING_COUNTER_EVIDENCE: "Searching exceptions and conflicts",
  CHECKING_EVIDENCE_COMPLETENESS: "Checking evidence completeness",
  VERIFYING: "Running evidence gates",
  ANSWER_READY: "Answer ready",
  ABSTAINED: "Evidence unavailable",
  FAILED: "Run failed safely",
};

export const TERMINAL_STATUSES = new Set<QuestionStatus>([
  "ANSWER_READY",
  "ABSTAINED",
  "FAILED",
]);

export function humanizeConcept(concept: string): string {
  const known: Record<string, string> = {
    ATRIAL_FIBRILLATION: "Atrial fibrillation",
    CHRONIC_KIDNEY_DISEASE: "Chronic kidney disease",
    RENAL_IMPAIRMENT: "Renal impairment",
    EGFR: "eGFR",
    CRCL: "CrCl",
  };
  return known[concept] ?? concept.replaceAll("_", " ").toLowerCase();
}

/**
 * One row of the interpreted-context panel.
 *
 * `inferred` says the extractor derived this rather than reading it from the question.
 * A stated age and an inferred one are not the same claim, and a panel that renders both
 * identically asks a reader to audit everything or nothing.
 */
/**
 * Provenance values that mean a person supplied the value.
 *
 * `USER_TEXT_EXPLICIT` is read out of the question; `USER_ENTERED` is typed into the
 * context editor and is the schema default. Both are stated. Testing for one of them alone
 * marked every hand-entered measurement "inferred" - inverting the distinction on the
 * panel whose only job is to draw the eye to what the system guessed.
 */
const STATED_PROVENANCE = new Set(["USER_TEXT_EXPLICIT", "USER_ENTERED"]);

export interface ContextRow {
  label: string;
  value: string;
  inferred: boolean;
}

export function contextRows(context: ClinicalContext | null): ContextRow[] {
  if (!context) return [];

  const inferred = new Set(context.inferred_fields);
  const rows: ContextRow[] = [];
  const push = (label: string, value: string, field: string) => {
    rows.push({ label, value, inferred: inferred.has(field) });
  };

  if (context.age !== null) push("Age", String(context.age), "age");
  if (context.sex) push("Sex", humanizeConcept(context.sex), "sex");
  for (const condition of context.conditions) {
    push("Condition", humanizeConcept(condition), "conditions");
  }
  for (const condition of context.known_absent_conditions) {
    push("Known absent", humanizeConcept(condition), "known_absent_conditions");
  }
  for (const measurement of context.measurements) {
    // A measurement carries its own provenance, which is more specific than the
    // field-level marker: one derived value does not make the whole list derived.
    rows.push({
      label: humanizeConcept(measurement.concept),
      value: `${measurement.value} ${measurement.unit}`,
      inferred: !STATED_PROVENANCE.has(measurement.provenance) || inferred.has("measurements"),
    });
  }
  for (const population of context.special_populations) {
    push("Population", humanizeConcept(population), "special_populations");
  }
  for (const population of context.known_absent_special_populations) {
    push(
      "Known absent population",
      humanizeConcept(population),
      "known_absent_special_populations",
    );
  }
  if (context.care_setting) {
    push("Care setting", humanizeConcept(context.care_setting), "care_setting");
  }
  if (context.jurisdiction) push("Jurisdiction", context.jurisdiction, "jurisdiction");
  return rows;
}

export interface RankedEvidence {
  detail: EvidenceDetail;
  retrievalRank: number;
}

/**
 * Retrieved passages that no rendered claim cited, paired with their canonical detail
 * and kept in retrieval-rank order.
 *
 * A candidate whose detail is missing is dropped rather than rendered bare. The API
 * contract keeps the two in step, so a gap means the payload is not what it claims to
 * be, and a passage we cannot attribute to a source is not one to put in front of a
 * reader.
 */
export function rankedCandidates(result: QuestionResult | null): RankedEvidence[] {
  if (!result) return [];
  const detailById = new Map(
    result.evidence_details.map((detail) => [detail.evidence_id, detail]),
  );
  return result.retrieval_candidates.flatMap((candidate) => {
    const detail = detailById.get(candidate.evidence_id);
    return detail ? [{ detail, retrievalRank: candidate.retrieval_rank }] : [];
  });
}
