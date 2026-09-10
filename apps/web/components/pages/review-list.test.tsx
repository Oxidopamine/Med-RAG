import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { QuestionSummary } from "@/lib/contracts";
import { forgetRuns, recordRun } from "@/lib/run-history";
import type { CorpusReadiness } from "@/lib/types";

const api = vi.hoisted(() => ({
  listQuestions: vi.fn<() => Promise<QuestionSummary[]>>(),
  getCorpusReadiness: vi.fn<() => Promise<CorpusReadiness>>(),
}));

vi.mock("@/lib/api", () => ({
  listQuestions: api.listQuestions,
  getCorpusReadiness: api.getCorpusReadiness,
}));

import { mergeRows, outcomeLabel, ReviewList, ReviewListExportAction, ReviewsProvider } from "./review-list";

const SERVED_RELEASE: CorpusReadiness = {
  status: "ready",
  corpus_registry_available: true,
  approved_corpus_available: true,
  corpus_release_id: "guidelines-2026-08",
  serving_mode: "ACTIVATED",
  clinical_use_allowed: true,
};

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

afterEach(() => {
  cleanup();
  api.listQuestions.mockReset();
  api.getCorpusReadiness.mockReset();
  forgetRuns();
});

describe("the review list's pure logic", () => {
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

function renderList() {
  return render(
    <ReviewsProvider>
      <ReviewListExportAction />
      <ReviewList />
    </ReviewsProvider>,
  );
}

describe("the review list as rendered", () => {
  it("links the question, keeps the outcome and claim counts on one line each, and flags a review from another release", async () => {
    api.listQuestions.mockResolvedValue([
      summary({
        question_id: "Q_1",
        question: "How often should viral load be monitored?",
        status: "ANSWER_READY",
        corpus_release_id: "guidelines-2026-08",
        supported_claims: 2,
        withheld_claims: 1,
      }),
      summary({
        question_id: "Q_2",
        question: "What prophylaxis suits a needlestick injury?",
        status: "ABSTAINED",
        corpus_release_id: "guidelines-2026-07",
        supported_claims: 0,
        withheld_claims: 0,
        abstention_reason_code: "NO_EVIDENCE_FOUND",
      }),
    ]);
    api.getCorpusReadiness.mockResolvedValue(SERVED_RELEASE);

    renderList();

    const link = await screen.findByRole("link", { name: "How often should viral load be monitored?" });
    expect(link).toHaveAttribute("href", `/r/${encodeURIComponent("Q_1")}`);

    const row1 = link.closest("tr")!;
    expect(within(row1).getByText("Answered")).toBeInTheDocument();
    expect(within(row1).getByText("2 supported")).toBeInTheDocument();
    expect(within(row1).getByText("1 withheld")).toBeInTheDocument();

    // A review answered on a release other than the one now served says so; the reason
    // code is read in a sentence, not printed as the raw constant the API sent.
    const row2 = screen.getByRole("link", { name: "What prophylaxis suits a needlestick injury?" }).closest("tr")!;
    expect(within(row2).getByText("No answer")).toBeInTheDocument();
    expect(within(row2).getByText("No evidence found")).toBeInTheDocument();
    expect(within(row2).getByText("Ran on release guidelines-2026-07")).toBeInTheDocument();
    expect(within(row1).queryByText(/Ran on release/)).not.toBeInTheDocument();
  });

  it("marks a review only opened in this browser, without a claim count fetched from the server", async () => {
    api.listQuestions.mockResolvedValue([]);
    api.getCorpusReadiness.mockResolvedValue(SERVED_RELEASE);
    recordRun("Q_local", "Only in this browser");

    renderList();

    const link = await screen.findByRole("link", { name: "Only in this browser" });
    const row = link.closest("tr")!;
    expect(within(row).getByText("Opened in this browser")).toBeInTheDocument();
    expect(within(row).getByText("Not fetched")).toBeInTheDocument();
  });

  it("says plainly when there are no reviews at all", async () => {
    api.listQuestions.mockResolvedValue([]);
    api.getCorpusReadiness.mockResolvedValue(SERVED_RELEASE);

    renderList();

    expect(await screen.findByText("No reviews yet.")).toBeInTheDocument();
  });

  it("offers to clear the filters when a search matches nothing, and recovers the list", async () => {
    const user = userEvent.setup();
    api.listQuestions.mockResolvedValue([summary({ question_id: "Q_1" })]);
    api.getCorpusReadiness.mockResolvedValue(SERVED_RELEASE);

    renderList();
    await screen.findByRole("link", { name: /viral load/ });

    await user.type(screen.getByLabelText("Search reviews"), "nothing matches this");
    expect(await screen.findByText("No reviews match these filters.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("link", { name: /viral load/ })).toBeInTheDocument();
  });

  it("exports the currently visible rows as CSV from the title band's action", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.spyOn(URL, "createObjectURL");
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    api.listQuestions.mockResolvedValue([
      summary({ question_id: "Q_1", question: "How often should viral load be monitored?" }),
    ]);
    api.getCorpusReadiness.mockResolvedValue(SERVED_RELEASE);

    renderList();
    await screen.findByRole("link", { name: /viral load/ });

    const button = screen.getByRole("button", { name: "Export CSV" });
    expect(button).toBeEnabled();
    await user.click(button);

    expect(clickSpy).toHaveBeenCalled();
    const blob = createObjectURL.mock.calls.at(-1)?.[0] as Blob;
    const text = await blob.text();
    expect(text.split("\n")[0]).toBe(
      "question_id,question,outcome,created_at,release,supported,withheld,reason",
    );
    expect(text).toContain('"Q_1"');
    expect(text).toContain('"Answered"');
    expect(text).toContain('"guidelines-2026-08"');

    createObjectURL.mockRestore();
    clickSpy.mockRestore();
  });
});
