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
      inferred_fields: ["special_populations"],
    });

    expect(rows).toEqual([
      { label: "Age", value: "74", inferred: false },
      { label: "Condition", value: "Atrial fibrillation", inferred: false },
      { label: "eGFR", value: "28 mL/min/1.73m2", inferred: false },
      // The extractor concluded this from the eGFR rather than reading it.
      { label: "Population", value: "Renal impairment", inferred: true },
    ]);
  });

  it("treats a hand-entered measurement as stated, not inferred", () => {
    // The context editor writes USER_ENTERED, which is also the schema default. Testing
    // only for USER_TEXT_EXPLICIT marked every measurement a reader typed as a guess,
    // which inverts the one distinction this panel exists to make.
    const rows = contextRows({
      age: null,
      sex: null,
      conditions: [],
      known_absent_conditions: [],
      measurements: [
        { concept: "EGFR", value: 28, unit: "mL/min/1.73m2", provenance: "USER_ENTERED" },
        { concept: "CRCL", value: 31, unit: "mL/min", provenance: "MODEL_DERIVED" },
      ],
      special_populations: [],
      known_absent_special_populations: [],
      care_setting: null,
      jurisdiction: null,
      question_type: null,
      topic: null,
      inferred_fields: [],
    });

    expect(rows).toEqual([
      { label: "eGFR", value: "28 mL/min/1.73m2", inferred: false },
      { label: "CrCl", value: "31 mL/min", inferred: true },
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
      inferred_fields: [],
    });

    expect(rows).toEqual([
      { label: "Known absent", value: "mechanical heart valve", inferred: false },
      { label: "Known absent population", value: "pregnancy", inferred: false },
      { label: "Care setting", value: "outpatient", inferred: false },
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
      inferred_fields: [],
    });

    expect(rows).toEqual([{ label: "Sex", value: "female", inferred: false }]);
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
