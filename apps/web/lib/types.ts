import type { components } from "@/lib/generated/api-schema";

type ApiSchemas = components["schemas"];

export type QuestionStatus = ApiSchemas["QuestionStatus"];
export type SourceFilters = Required<ApiSchemas["SourceFilters"]>;
export type Measurement = ApiSchemas["Measurement"];
export type ClinicalContext = Required<ApiSchemas["ClinicalContext"]>;
export type QuestionAccepted = ApiSchemas["QuestionAccepted"];
export type RenderedClaim = ApiSchemas["RenderedClaim"];
export type EvidenceLocator = Required<ApiSchemas["EvidenceLocator"]>;
export type EvidenceDetail = Omit<Required<ApiSchemas["EvidenceDetail"]>, "locators"> & {
  locators: EvidenceLocator[];
};
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
