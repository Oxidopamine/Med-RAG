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

export function contextRows(context: ClinicalContext | null): Array<[string, string]> {
  if (!context) return [];

  const rows: Array<[string, string]> = [];
  if (context.age !== null) rows.push(["Age", String(context.age)]);
  if (context.sex) rows.push(["Sex", humanizeConcept(context.sex)]);
  for (const condition of context.conditions) {
    rows.push(["Condition", humanizeConcept(condition)]);
  }
  for (const condition of context.known_absent_conditions) {
    rows.push(["Known absent", humanizeConcept(condition)]);
  }
  for (const measurement of context.measurements) {
    rows.push([
      humanizeConcept(measurement.concept),
      `${measurement.value} ${measurement.unit}`,
    ]);
  }
  for (const population of context.special_populations) {
    rows.push(["Population", humanizeConcept(population)]);
  }
  for (const population of context.known_absent_special_populations) {
    rows.push(["Known absent population", humanizeConcept(population)]);
  }
  if (context.care_setting) rows.push(["Care setting", humanizeConcept(context.care_setting)]);
  if (context.jurisdiction) rows.push(["Jurisdiction", context.jurisdiction]);
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
