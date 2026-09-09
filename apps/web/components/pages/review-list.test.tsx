import { describe, expect, it } from "vitest";

import type { QuestionSummary } from "@/lib/contracts";

import { mergeRows, outcomeLabel } from "./review-list";

function summary(overrides: Partial<QuestionSummary>): QuestionSummary {
  return {
    question_id: "Q_1",
    question: "How often should viral load be monitored?",
    status: "ANSWER_READY",
    corpus_release_id: "guidelines-2026-08",
    serving_mode: "ACTIVATED",
    supported_claims: 2,
    withheld_claims: 0,
    abstention_reason_code: null,
    created_at: "2026-09-09T10:00:00Z",
    updated_at: "2026-09-09T10:00:10Z",
    ...overrides,
  };
}

describe("the review list", () => {
  it("puts server rows first, newest at the top, and keeps browser-only rows marked", () => {
    const rows = mergeRows(
      [
        summary({ question_id: "Q_old", created_at: "2026-09-08T10:00:00Z" }),
        summary({ question_id: "Q_new", created_at: "2026-09-09T10:00:00Z" }),
      ],
      [
        { questionId: "Q_new", question: "duplicate of a server row", openedAt: "2026-09-09T11:00:00Z" },
        { questionId: "Q_local", question: "Only in this browser", openedAt: "2026-09-09T12:00:00Z" },
      ],
    );
    expect(rows.map((row) => row.questionId)).toEqual(["Q_local", "Q_new", "Q_old"]);
    expect(rows[0]!.onServer).toBe(false);
    expect(rows[0]!.status).toBe("UNKNOWN");
    // The server's copy of a question wins over the browser's memory of it.
    expect(rows[1]!.question).toBe("How often should viral load be monitored?");
    expect(rows[1]!.onServer).toBe(true);
  });

  it("names outcomes in the reader's words", () => {
    expect(outcomeLabel("ANSWER_READY")).toBe("Answered");
    expect(outcomeLabel("ABSTAINED")).toBe("No answer");
    expect(outcomeLabel("FAILED")).toBe("Failed");
    expect(outcomeLabel("RETRIEVING")).toBe("Running");
    expect(outcomeLabel("UNKNOWN")).toBe("Not held");
  });
});
