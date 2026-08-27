import { describe, expect, it } from "vitest";

import { contextRows, humanizeConcept, rankedCandidates, STATUS_LABELS } from "./presentation";
import type { EvidenceDetail, QuestionResult } from "./types";

describe("presentation helpers", () => {
  it("uses evidence-safe status language", () => {
    expect(STATUS_LABELS.VERIFYING).toBe("Running evidence gates");
    expect(STATUS_LABELS.ABSTAINED).toBe("Evidence unavailable");
  });

  it("formats explicit context without adding facts", () => {
    const rows = contextRows({
      age: 74,
      sex: null,
      conditions: ["ATRIAL_FIBRILLATION"],
      known_absent_conditions: [],
      measurements: [
        { concept: "EGFR", value: 28, unit: "mL/min/1.73m2", provenance: "USER_TEXT_EXPLICIT" },
      ],
      special_populations: ["RENAL_IMPAIRMENT"],
      known_absent_special_populations: [],
      care_setting: null,
      jurisdiction: null,
      question_type: "treatment_guideline",
      topic: "anticoagulation",
    });

    expect(rows).toEqual([
      ["Age", "74"],
      ["Condition", "Atrial fibrillation"],
      ["eGFR", "28 mL/min/1.73m2"],
      ["Population", "Renal impairment"],
    ]);
  });

  it("humanizes unknown concepts deterministically", () => {
    expect(humanizeConcept("SYNTHETIC_CONCEPT")).toBe("synthetic concept");
  });

  it("shows explicit negative context without inferring it from silence", () => {
    const rows = contextRows({
      age: null,
      sex: null,
      conditions: [],
      known_absent_conditions: ["MECHANICAL_HEART_VALVE"],
      measurements: [],
      special_populations: [],
      known_absent_special_populations: ["PREGNANCY"],
      care_setting: "OUTPATIENT",
      jurisdiction: null,
      question_type: null,
      topic: null,
    });

    expect(rows).toEqual([
      ["Known absent", "mechanical heart valve"],
      ["Known absent population", "pregnancy"],
      ["Care setting", "outpatient"],
    ]);
  });

  it("shows an explicitly documented sex without inferring one", () => {
    const rows = contextRows({
      age: null,
      sex: "FEMALE",
      conditions: [],
      known_absent_conditions: [],
      measurements: [],
      special_populations: [],
      known_absent_special_populations: [],
      care_setting: null,
      jurisdiction: null,
      question_type: null,
      topic: null,
    });

    expect(rows).toEqual([["Sex", "female"]]);
  });

  it("pairs uncited candidates with their detail and drops any it cannot attribute", () => {
    const result = {
      evidence_details: [
        { evidence_id: "evidence-2", source_title: "Ranked third" } as EvidenceDetail,
      ],
      retrieval_candidates: [
        { evidence_id: "evidence-2", retrieval_rank: 3 },
        { evidence_id: "evidence-9", retrieval_rank: 9 },
      ],
    } as QuestionResult;

    expect(rankedCandidates(result)).toEqual([
      { detail: result.evidence_details[0], retrievalRank: 3 },
    ]);
    expect(rankedCandidates(null)).toEqual([]);
  });
});
