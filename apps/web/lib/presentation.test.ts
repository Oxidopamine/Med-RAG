import { describe, expect, it } from "vitest";

import { contextRows, humanizeConcept, STATUS_LABELS } from "./presentation";

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
});
