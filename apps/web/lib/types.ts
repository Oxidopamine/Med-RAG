import type { components } from "@/lib/generated/api-schema";

type ApiSchemas = components["schemas"];

export type QuestionStatus = ApiSchemas["QuestionStatus"];
export type SourceFilters = Required<ApiSchemas["SourceFilters"]>;
export type Measurement = ApiSchemas["Measurement"];
export type ClinicalContext = Required<ApiSchemas["ClinicalContext"]>;
export type QuestionAccepted = ApiSchemas["QuestionAccepted"];
export type RenderedClaim = ApiSchemas["RenderedClaim"];
/**
 * The serving locator.
 *
 * `Required` everywhere except the cell address. Those three fields are populated only
 * on a `TABLE_CELL` anchor, so requiring them would make every page locator in every
 * fixture declare three nulls it has no use for. Absent and null are handled alike
 * downstream, which is what lets a payload from a release that predates the projection
 * forwarding them read as an unaddressed cell rather than as a malformed one.
 */
export type EvidenceLocator = Omit<
  Required<ApiSchemas["EvidenceLocator"]>,
  "table_id" | "row_index" | "column_index"
> & {
  table_id?: string | null;
  row_index?: number | null;
  column_index?: number | null;
};
export type EvidenceDetail = Omit<Required<ApiSchemas["EvidenceDetail"]>, "locators"> & {
  locators: EvidenceLocator[];
};
export type RetrievalCandidate = ApiSchemas["RetrievalCandidate"];
export type AbstentionDetail = Required<ApiSchemas["AbstentionDetail"]>;
export type VerificationSummary = ApiSchemas["VerificationSummary"];
export type ActiveCorpusRelease = Required<ApiSchemas["ActiveCorpusRelease"]>;

export interface ProgressEvent {
  question_id: string;
  sequence: number;
  status: QuestionStatus;
  occurred_at: string;
}

export interface CorpusReadiness {
  approved_corpus_available: boolean;
  clinical_use_allowed: boolean;
  corpus_registry_available: boolean;
  corpus_release_id: string | null;
  status: string;
}

type GeneratedQuestionResult = Required<ApiSchemas["QuestionResult"]>;

export type QuestionResult = Omit<
  GeneratedQuestionResult,
  "abstention" | "corpus_release" | "evidence_details" | "interpreted_context"
> & {
  abstention: AbstentionDetail | null;
  corpus_release: ActiveCorpusRelease | null;
  evidence_details: EvidenceDetail[];
  interpreted_context: ClinicalContext | null;
};
