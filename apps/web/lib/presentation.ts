import type { ClinicalContext, QuestionStatus, WithheldClaimSummary } from "./types";

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

// What each automated check means in a clinician's terms. The validator names are
// engineering vocabulary; a reader needs to know which part of a claim failed to match
// its source, not which module decided that.
const WITHHELD_REASONS: Record<string, string> = {
  NUMERIC: "a value that is not in the cited source",
  UNIT: "a unit that does not match the cited source",
  OPERATOR: "a threshold that does not match the cited source",
  QUOTE: "a quotation that is not in the cited source",
  PROVENANCE: "a citation that cannot support it",
};

export function withheldReasonLabel(summary: WithheldClaimSummary): string {
  const claims = `${summary.count} claim${summary.count === 1 ? "" : "s"}`;
  if (summary.status === "UNRESOLVED") {
    // An unresolved check is not a detected error, and the difference matters to a
    // reader: the usual cause is source text the release does not permit rendering, so
    // the check could not run rather than the claim being found wrong.
    return `${claims} could not be checked against the cited source`;
  }
  const reason = WITHHELD_REASONS[summary.validator];
  return reason
    ? `${claims} withheld for ${reason}`
    : `${claims} withheld by an automated check`;
}
