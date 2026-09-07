import { describe, expect, it } from "vitest";

import { detectIdentifiers } from "./identifiers";
import {
  FEATURED_QUESTIONS,
  QUESTION_BANK,
  QUESTION_BANK_AREAS,
  filterQuestionBank,
  questionBankAreaLabel,
} from "./question-bank";

describe("the question bank", () => {
  it("has unique ids, known areas, and a question and purpose on every entry", () => {
    const ids = new Set(QUESTION_BANK.map((entry) => entry.id));
    expect(ids.size).toBe(QUESTION_BANK.length);
    const areas = new Set(QUESTION_BANK_AREAS.map((area) => area.id));
    for (const entry of QUESTION_BANK) {
      expect(areas.has(entry.area)).toBe(true);
      expect(entry.question.trim().length).toBeGreaterThan(20);
      expect(entry.purpose.trim().length).toBeGreaterThan(3);
    }
  });

  it("never trips the identifier guard, so a bank question is always submittable", () => {
    for (const entry of QUESTION_BANK) {
      expect(detectIdentifiers(entry.question), entry.id).toEqual([]);
    }
  });

  it("features a handful of answerable questions from active areas", () => {
    expect(FEATURED_QUESTIONS.length).toBeGreaterThanOrEqual(3);
    expect(FEATURED_QUESTIONS.length).toBeLessThanOrEqual(6);
    const active = new Set(
      QUESTION_BANK_AREAS.filter((area) => area.status === "active").map((area) => area.id),
    );
    for (const entry of FEATURED_QUESTIONS) {
      expect(active.has(entry.area), entry.id).toBe(true);
      expect(entry.area).not.toBe("checks");
    }
  });

  it("covers every area with at least three questions", () => {
    for (const area of QUESTION_BANK_AREAS) {
      const count = QUESTION_BANK.filter((entry) => entry.area === area.id).length;
      expect(count, area.id).toBeGreaterThanOrEqual(3);
    }
  });

  it("filters by area and by every term of the query, case-insensitively", () => {
    const all = filterQuestionBank(QUESTION_BANK, { query: "", area: "all" });
    expect(all).toHaveLength(QUESTION_BANK.length);

    const treatment = filterQuestionBank(QUESTION_BANK, { query: "", area: "treatment" });
    expect(treatment.length).toBeGreaterThan(0);
    expect(treatment.every((entry) => entry.area === "treatment")).toBe(true);

    const viralLoad = filterQuestionBank(QUESTION_BANK, { query: "Viral LOAD", area: "all" });
    expect(viralLoad.length).toBeGreaterThan(0);
    expect(
      viralLoad.every((entry) =>
        `${entry.question} ${entry.purpose}`.toLocaleLowerCase().includes("viral"),
      ),
    ).toBe(true);

    const byAreaLabel = filterQuestionBank(QUESTION_BANK, { query: "hypertension", area: "all" });
    expect(byAreaLabel.some((entry) => entry.area === "hypertension")).toBe(true);

    expect(filterQuestionBank(QUESTION_BANK, { query: "zzzz", area: "all" })).toEqual([]);
  });

  it("labels areas by their display name", () => {
    expect(questionBankAreaLabel("delivery")).toBe("Service delivery");
  });
});
