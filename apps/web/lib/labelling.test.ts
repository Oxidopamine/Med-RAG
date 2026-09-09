import { describe, expect, it } from "vitest";

import {
  Census,
  PythonRandom,
  abstentionItems,
  agreementItems,
  claimTriggers,
  emptyLabelFile,
  labelKey,
  misleadItems,
  passProgress,
  recordOutcome,
  type RunFile,
} from "./labelling";

// Vectors produced by CPython 3.10's random module for the same seeds.
describe("PythonRandom", () => {
  it("reproduces CPython's Mersenne Twister for an integer seed", () => {
    const random = new PythonRandom(20260906);
    expect([random.genrandUint32(), random.genrandUint32(), random.genrandUint32(), random.genrandUint32()]).toEqual([
      3814748531, 1610325755, 1315063953, 1956154695,
    ]);
    const bits = new PythonRandom(20260906);
    expect([bits.getrandbits(5), bits.getrandbits(7), bits.getrandbits(32)]).toEqual([28, 47, 1315063953]);
  });

  it("shuffles exactly as random.shuffle does", () => {
    expect(new PythonRandom(20260906).shuffle([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])).toEqual([1, 6, 3, 2, 9, 0, 8, 7, 4, 5]);
    expect(new PythonRandom(20260907).shuffle("abcdefghijklmnopqrstuvwxyz".split("")).join("")).toBe(
      "bwplmkvraxjesfcudqioztyngh",
    );
  });

  it("samples exactly as random.sample does, by the pool and the set method", () => {
    const range = (n: number) => Array.from({ length: n }, (_, index) => index);
    expect(new PythonRandom(20260906).sample(range(80), 20).sort((a, b) => a - b)).toEqual([
      1, 5, 6, 12, 14, 15, 19, 25, 26, 32, 38, 39, 47, 58, 64, 65, 70, 72, 73, 74,
    ]);
    expect(new PythonRandom(20260906).sample(range(160), 30).sort((a, b) => a - b)).toEqual([
      3, 10, 13, 14, 20, 25, 29, 30, 39, 43, 50, 57, 60, 65, 66, 76, 78, 90, 95, 96, 105, 113, 116, 125, 128, 136, 149, 150,
      151, 156,
    ]);
    expect(new PythonRandom(20260906).sample(range(400), 30).sort((a, b) => a - b)).toEqual([
      6, 20, 26, 27, 40, 50, 59, 60, 79, 86, 101, 114, 120, 130, 131, 153, 156, 180, 191, 193, 210, 233, 257, 299, 301, 302,
      307, 346, 366, 397,
    ]);
  });
});

function record(
  questionId: string,
  claims: Array<{ text: string; evidence_ids: string[] }> | null,
  options: { abstained?: boolean; gate?: string } = {},
): RunFile["results"][number] {
  return {
    question_id: questionId,
    question: `Question ${questionId}`,
    chapter_no: 2,
    chapter: "Testing",
    gate_reason: options.gate ?? null,
    retrieval: {
      passages: [
        { evidence_id: "EV_A", rendered_text: "Passage A", kind: "NARRATIVE", qualified_roles: ["PRIMARY_SUPPORT"] },
        { evidence_id: "EV_B", rendered_text: "Passage B", kind: "NARRATIVE", qualified_roles: ["EXCEPTION"] },
      ],
    },
    generation:
      claims === null
        ? null
        : { abstained: options.abstained ?? false, claims, message: options.abstained ? "Not enough" : undefined },
  };
}

function census(seed: number | null = 20260906) {
  const production: RunFile = {
    run_label: "production-a",
    question_set: { sha256: "q".repeat(64) },
    results: [
      record("Q1", [{ text: "Claim one", evidence_ids: ["EV_A", "EV_B"] }]),
      record("Q2", [{ text: "Claim two", evidence_ids: ["EV_A"] }, { text: "Claim three", evidence_ids: [] }]),
      record("Q3", null, { gate: "MISSING_REQUIRED_ROLES" }),
      record("Q4", [], { abstained: true }),
      record("Q5", [], { abstained: true }),
    ],
  };
  const closedBook: RunFile = {
    results: [record("Q1", [{ text: "Closed book claim", evidence_ids: [] }]), record("Q3", [{ text: "Closed book on Q3", evidence_ids: [] }])],
  };
  const naive: RunFile = {
    results: [record("Q3", [{ text: "Naive claim on the gate-blocked question", evidence_ids: ["EV_B"] }]), record("Q1", [{ text: "Naive on Q1", evidence_ids: [] }])],
  };
  return new Census(
    { production, closed_book: closedBook, naive },
    {
      questions: { items: [{ question_id: "Q1", source_statement: "Gold one" }] },
      renderFull: (id) => (id === "EV_A" ? "Full passage A from the bundle" : null),
      shuffleSeed: seed,
      abstentionSample: 1,
    },
  );
}

describe("the census", () => {
  it("classifies records the way the worksheet builder does", () => {
    expect(recordOutcome(record("Q", null))).toBe("ABSTAINED");
    expect(recordOutcome(record("Q", [], { abstained: true }))).toBe("ABSTAINED");
    expect(recordOutcome(record("Q", [{ text: "t", evidence_ids: [] }]))).toBe("ANSWERED");
    expect(recordOutcome({ question_id: "Q", question: "q", generation: { error: "quota" } })).toBe("ERROR");
    expect(labelKey("Q1", "production")).toBe("Q1");
    expect(labelKey("Q1", "naive")).toBe("Q1:naive");
  });

  it("selects every gate-blocked record and a seeded sample of the model-declared ones", () => {
    const value = census();
    expect(value.gateBlocked).toEqual(["Q3"]);
    expect(value.sampled).toHaveLength(1);
    expect(value.modelDeclaredTotal).toBe(2);
    // The naive arm enters only on the questions selected for abstention review.
    expect(value.armRecords("naive").map((item) => item.question_id)).toEqual(["Q3"]);
    expect(value.armRecords("closed_book").map((item) => item.question_id)).toEqual(["Q1", "Q3"]);
  });

  it("interleaves arms in Pass A and conceals them behind item ids", () => {
    const items = agreementItems(census());
    expect(items.map((item) => item.id)).toEqual(["A-001", "A-002", "A-003", "A-004", "A-005", "A-006"]);
    expect(new Set(items.map((item) => item.arm))).toEqual(new Set(["production", "closed_book", "naive"]));
    expect(items.find((item) => item.questionId === "Q1")?.gold).toBe("Gold one");
    expect(items.find((item) => item.questionId === "Q2")?.gold).toMatch(/no gold statement/);
    // The order is the seeded one, not the sorted one.
    const sorted = agreementItems(census(null)).map((item) => item.key + item.index);
    expect(items.map((item) => item.key + item.index)).not.toEqual(sorted);
  });

  it("writes the label skeleton with every label null and every text source recorded", () => {
    const value = census();
    const labels = emptyLabelFile(value, { runPaths: { production: "production-a.json" }, rubricSha256: "r".repeat(64) });
    expect(Object.keys(labels.records).sort()).toEqual(["Q1", "Q1:closed_book", "Q2", "Q3:closed_book", "Q3:naive"]);
    const first = labels.records["Q1"]!.claims[0]!;
    expect(first.pair_attribution).toEqual({ EV_A: null, EV_B: null });
    expect(first.pair_text_source).toEqual({ EV_A: "bundle", EV_B: "run" });
    expect(labels.census_valid).toBe(false);
    expect(labels.pair_text_sources).toEqual({ bundle: 2, run: 2 });
    expect(labels.gate_blocked).toEqual({ Q3: { gate_right: null, dak_has_answer: null, note: "" } });
    expect(labels.abstained_sample.size).toBe(1);
    expect(labels.run.question_set_sha256).toBe("q".repeat(64));
  });

  it("orders the abstention adjudications with the seed plus one and shows every passage", () => {
    const items = abstentionItems(census());
    expect(items).toHaveLength(2);
    const gate = items.find((item) => item.gateBlocked)!;
    expect(gate.questionId).toBe("Q3");
    expect(gate.gateReason).toBe("MISSING_REQUIRED_ROLES");
    expect(gate.passages.map((passage) => passage.text_source)).toEqual(["bundle", "run"]);
  });

  it("flags claims by the five trigger classes and draws seeded controls for Pass C", () => {
    const value = census();
    const labels = emptyLabelFile(value, { runPaths: {}, rubricSha256: null });
    const q1 = labels.records["Q1"]!;
    q1.claims[0]!.joint_attribution = "EXTRAPOLATORY";
    q1.claims[0]!.agreement = "AGREES";
    const q2 = labels.records["Q2"]!;
    q2.claims[0]!.joint_attribution = "ATTRIBUTABLE";
    q2.claims[0]!.agreement = "AGREES";
    q2.claims[1]!.agreement = "PARTIAL";
    expect(claimTriggers(q1.claims[0]!, q1)).toEqual(["joint_non_attributable"]);
    expect(claimTriggers(q2.claims[1]!, q2)).toEqual(["disagreement"]);
    const items = misleadItems(value, labels, 1);
    expect(items.filter((item) => item.status === "flagged").map((item) => item.key + item.index).sort()).toEqual(["Q11", "Q22"]);
    expect(items.filter((item) => item.status === "control")).toHaveLength(1);
    expect(items.map((item) => item.id)).toEqual(["C-001", "C-002", "C-003"]);
    const progress = passProgress(labels);
    expect(progress.agreement).toEqual({ done: 3, total: 6 });
    expect(progress.mislead).toEqual({ done: 0, total: 2 });
  });
});
