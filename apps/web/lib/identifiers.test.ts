import { describe, expect, it } from "vitest";

import { describeIdentifiers, detectIdentifiers, stripIdentifiers } from "@/lib/identifiers";

describe("detectIdentifiers", () => {
  it("finds the shapes that identify a person", () => {
    const question =
      "For Maria Okonkwo, DOB 14/03/1991, on first-line ART — what monitoring is recommended?";
    const found = detectIdentifiers(question);

    expect(found.map((item) => item.kind)).toEqual(["name", "date"]);
    expect(found[0]!.text).toBe("Maria Okonkwo");
    expect(found[1]!.text).toBe("14/03/1991");
    expect(describeIdentifiers(found)).toBe("a name and a date of birth");
  });

  it("finds emails, record numbers and phone numbers", () => {
    expect(detectIdentifiers("contact clinic@example.org")[0]!.kind).toBe("email");
    expect(detectIdentifiers("MRN 4482910 attached")[0]!.kind).toBe("record-number");
    expect(detectIdentifiers("call +44 20 7946 0958 for records")[0]!.kind).toBe("phone");
  });

  /*
   * The half that matters more. A guard that fires on ordinary clinical prose is the
   * permanent banner again - people learn to dismiss it, and then it protects nobody.
   */
  it("stays silent on clinical prose", () => {
    for (const question of [
      "How often should viral load be monitored for an adult established on antiretroviral therapy?",
      "What do current guidelines recommend when an adult on first-line ART has a confirmed viral load above 1000 copies/mL?",
      "Which adults should be offered pre-exposure prophylaxis, and what testing is required first?",
      "Does the World Health Organization recommend retesting after a reactive HIV rapid diagnostic test?",
      "For an adult with a CD4 count of 180 cells/mm3, what prophylaxis is recommended?",
      "For an adult with Chronic Kidney Disease and Type 2 Diabetes, what Blood Pressure target do the guidelines set?",
      "Should Tuberculosis Preventive Treatment be offered to an adult with Advanced HIV Disease?",
      "Is a Calcium Channel Blocker or a Thiazide Diuretic the preferred first medicine for Grade 1 Hypertension?",
    ]) {
      expect(detectIdentifiers(question)).toEqual([]);
    }
  });

  it("does not read a measurement as a record number", () => {
    expect(detectIdentifiers("a viral load of 1200000 copies/mL")).toEqual([]);
  });

  it("does not report one span under two kinds", () => {
    const found = detectIdentifiers("write to Maria.Okonkwo@example.org today");
    expect(found).toHaveLength(1);
    expect(found[0]!.kind).toBe("email");
  });
});

describe("stripIdentifiers", () => {
  it("removes every detected span and tidies what is left", () => {
    const question = "For Maria Okonkwo, DOB 14/03/1991, what monitoring is recommended?";
    const stripped = stripIdentifiers(question, detectIdentifiers(question));

    expect(stripped).toBe("For, DOB, what monitoring is recommended?");
    expect(detectIdentifiers(stripped)).toEqual([]);
  });
});
