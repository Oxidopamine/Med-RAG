export type QuestionStatus =
  | "QUEUED"
  | "CONTEXT_EXTRACTED"
  | "RETRIEVING"
  | "RERANKING"
  | "SEARCHING_COUNTER_EVIDENCE"
  | "CHECKING_EVIDENCE_COMPLETENESS"
  | "VERIFYING"
  | "ANSWER_READY"
  | "ABSTAINED"
  | "FAILED";

export interface Measurement {
  concept: string;
  value: number;
  unit: string;
  provenance: string;
}

export interface ClinicalContext {
  age: number | null;
  sex: string | null;
  conditions: string[];
  measurements: Measurement[];
  special_populations: string[];
  jurisdiction: string | null;
  question_type: string | null;
  topic: string | null;
}

export interface QuestionAccepted {
  question_id: string;
  status: QuestionStatus;
}

export interface ProgressEvent {
  question_id: string;
  sequence: number;
  status: QuestionStatus;
  occurred_at: string;
}

export interface RenderedClaim {
  claim_id: string;
  text: string;
  evidence_ids: string[];
  verification_status: string;
}

export interface QuestionResult {
  question_id: string;
  question: string;
  status: QuestionStatus;
  interpreted_context: ClinicalContext | null;
  claims: RenderedClaim[];
  conflicts: Array<Record<string, string>>;
  verification_summary: {
    rendered_claims: number;
    supported_claims: number;
    withheld_claims: number;
  };
  abstention: {
    reason_code: string;
    message: string;
    missing_evidence_roles: string[];
    closest_evidence_ids: string[];
  } | null;
  created_at: string;
  updated_at: string;
}

