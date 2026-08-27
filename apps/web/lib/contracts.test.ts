import { describe, expect, it } from "vitest";

import { parseContract, progressEventSchema, questionResultSchema } from "./contracts";
import type { ClinicalContext, QuestionResult } from "./types";

const validContext: ClinicalContext = {
  age: 74,
  sex: null,
  conditions: ["ATRIAL_FIBRILLATION"],
  known_absent_conditions: [],
  measurements: [
    {
      concept: "EGFR",
      value: 28,
      unit: "mL/min/1.73m2",
      provenance: "USER_TEXT_EXPLICIT",
    },
  ],
  special_populations: ["RENAL_IMPAIRMENT"],
  known_absent_special_populations: [],
  care_setting: null,
  jurisdiction: null,
  question_type: "treatment_guideline",
  topic: "anticoagulation",
};

const validResult: QuestionResult = {
  question_id: "question-1",
  question: "What do guidelines say?",
  status: "ABSTAINED",
  corpus_release: null,
  interpreted_context: validContext,
  claims: [],
  evidence_details: [],
  retrieval_candidates: [],
  conflicts: [],
  verification_summary: {
    rendered_claims: 0,
    supported_claims: 0,
    withheld_claims: 0,
  },
  abstention: {
    reason_code: "NO_APPROVED_CORPUS",
    message: "No approved guideline corpus is configured.",
    missing_evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
    closest_evidence_ids: [],
  },
  created_at: "2026-08-25T10:00:00+00:00",
  updated_at: "2026-08-25T10:00:01+00:00",
};

const validAnswerReadyResult: QuestionResult = {
  ...validResult,
  status: "ANSWER_READY",
  corpus_release: {
    corpus_release_id: "release-2026-08",
    manifest_sha256: "a".repeat(64),
    qdrant_collection: "guidelines-release-2026-08",
    activated_at: "2026-08-25T09:00:00+00:00",
  },
  claims: [
    {
      claim_id: "claim-1",
      text: "Reduced renal function should be considered when selecting anticoagulant therapy.",
      evidence_ids: ["evidence-1"],
      verification_status: "SUPPORTED",
    },
  ],
  evidence_details: [
    {
      evidence_id: "evidence-1",
      exact_text:
        "Reduced renal function should be considered when selecting and dosing anticoagulant therapy.",
      evidence_type: "RECOMMENDATION",
      evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
      section_path: ["Anticoagulation"],
      source_id: "source-1",
      source_version_id: "source-version-1",
      source_title: "Guideline for the Management of Atrial Fibrillation",
      source_version_label: "2026",
      publisher_name: "Example Cardiology Society",
      source_url: "https://guidelines.example/atrial-fibrillation",
      source_class: "PROFESSIONAL_GUIDELINE",
      jurisdiction: "US",
      language: "en",
      lifecycle_status: "CURRENT",
      effective_from: "2026-01-01",
      effective_to: null,
      approval_status: "APPROVED",
      render_allowed: true,
      locators: [
        {
          kind: "PDF_PAGE",
          source_uri: "source://source-version-1/page/47",
          pdf_page: 47,
          printed_page: "231",
          bbox: null,
          exact_highlight_available: false,
        },
      ],
    },
  ],
  verification_summary: {
    rendered_claims: 1,
    supported_claims: 1,
    withheld_claims: 0,
  },
  abstention: null,
};

describe("runtime API contracts", () => {
  it("accepts a complete fail-closed result", () => {
    expect(questionResultSchema.parse(validResult)).toEqual(validResult);
  });

  it("accepts an answer only when every rendered claim has canonical source detail", () => {
    expect(questionResultSchema.parse(validAnswerReadyResult)).toEqual(validAnswerReadyResult);

    const incomplete = structuredClone(validAnswerReadyResult);
    incomplete.evidence_details = [];
    expect(() => questionResultSchema.parse(incomplete)).toThrow(
      "Evidence details must exactly cover cited and candidate evidence IDs",
    );
  });

  it("accepts uncited ranked candidates that carry canonical source detail", () => {
    const withCandidate = structuredClone(validAnswerReadyResult);
    const candidateDetail = structuredClone(withCandidate.evidence_details[0]!);
    candidateDetail.evidence_id = "evidence-2";
    withCandidate.evidence_details.push(candidateDetail);
    withCandidate.retrieval_candidates = [{ evidence_id: "evidence-2", retrieval_rank: 3 }];

    expect(questionResultSchema.parse(withCandidate)).toEqual(withCandidate);

    const orphaned = structuredClone(validAnswerReadyResult);
    orphaned.retrieval_candidates = [{ evidence_id: "evidence-2", retrieval_rank: 3 }];
    expect(() => questionResultSchema.parse(orphaned)).toThrow(
      "Evidence details must exactly cover cited and candidate evidence IDs",
    );
  });

  it("rejects a cited evidence ID replayed as an uncited candidate", () => {
    const replayed = structuredClone(validAnswerReadyResult);
    replayed.retrieval_candidates = [{ evidence_id: "evidence-1", retrieval_rank: 2 }];

    expect(() => questionResultSchema.parse(replayed)).toThrow(
      "A cited evidence ID cannot also be an uncited retrieval candidate",
    );
  });

  it("rejects ranked candidates on a result that withheld its answer", () => {
    const abstained = structuredClone(validResult);
    abstained.retrieval_candidates = [{ evidence_id: "evidence-2", retrieval_rank: 3 }];

    expect(() => questionResultSchema.parse(abstained)).toThrow(
      "Only an answer-ready result can expose retrieval candidates",
    );
  });

  it("rejects exact text or highlights for license-restricted evidence", () => {
    const restricted = structuredClone(validAnswerReadyResult);
    restricted.evidence_details[0]!.render_allowed = false;

    expect(() => questionResultSchema.parse(restricted)).toThrow(
      "Restricted evidence cannot expose exact text",
    );

    restricted.evidence_details[0]!.exact_text = null;
    restricted.evidence_details[0]!.locators[0]!.bbox = [0, 0, 10, 10];
    restricted.evidence_details[0]!.locators[0]!.exact_highlight_available = true;
    expect(() => questionResultSchema.parse(restricted)).toThrow(
      "Restricted evidence cannot expose exact highlights",
    );
  });

  it("rejects contradictory clinical context", () => {
    const result = structuredClone(validResult);
    result.interpreted_context!.known_absent_conditions = ["ATRIAL_FIBRILLATION"];

    expect(() => questionResultSchema.parse(result)).toThrow(
      "ATRIAL_FIBRILLATION cannot be both present and absent",
    );
  });

  it("rejects a terminal failure without abstention details", () => {
    expect(() =>
      questionResultSchema.parse({ ...validResult, status: "FAILED", abstention: null }),
    ).toThrow("require abstention details");
  });

  it("turns malformed SSE data into a user-safe contract error", () => {
    expect(() =>
      parseContract(
        progressEventSchema,
        { question_id: "question-1", sequence: 1, status: "NOT_A_STATUS" },
        "progress event",
      ),
    ).toThrow("The API returned an invalid progress event");
  });
});
