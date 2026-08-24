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
      measurements: [
        { concept: "EGFR", value: 28, unit: "mL/min/1.73m2", provenance: "USER_TEXT_EXPLICIT" },
      ],
      special_populations: ["RENAL_IMPAIRMENT"],
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
});
