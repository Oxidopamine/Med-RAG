import type { ClinicalContext, QuestionStatus } from "./types";

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
  for (const condition of context.conditions) {
    rows.push(["Condition", humanizeConcept(condition)]);
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
  if (context.jurisdiction) rows.push(["Jurisdiction", context.jurisdiction]);
  return rows;
}
