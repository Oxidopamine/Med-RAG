import { describe, expect, it } from "vitest";

import {
  contextRows,
  humanizeConcept,
  STATUS_LABELS,
  withheldReasonLabel,
} from "./presentation";

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

  it("names which part of a claim failed rather than which module decided", () => {
    expect(withheldReasonLabel({ validator: "UNIT", status: "UNSUPPORTED", count: 1 })).toBe(
      "1 claim withheld for a unit that does not match the cited source",
    );
    expect(withheldReasonLabel({ validator: "NUMERIC", status: "UNSUPPORTED", count: 2 })).toBe(
      "2 claims withheld for a value that is not in the cited source",
    );
  });

  it("distinguishes a check that could not run from a claim found wrong", () => {
    expect(withheldReasonLabel({ validator: "NUMERIC", status: "UNRESOLVED", count: 1 })).toBe(
      "1 claim could not be checked against the cited source",
    );
  });

  it("degrades to a generic phrase for a validator it does not know", () => {
    expect(withheldReasonLabel({ validator: "FUTURE", status: "UNSUPPORTED", count: 1 })).toBe(
      "1 claim withheld by an automated check",
    );
  });
});
