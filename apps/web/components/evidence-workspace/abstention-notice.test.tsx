import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { abstainedResult } from "@/lib/fixtures/answer-lane";
import type { AbstentionDetail, QuestionStatus } from "@/lib/types";

import { AbstentionNotice } from "./abstention-notice";

afterEach(cleanup);

function renderNotice(
  abstention: Partial<AbstentionDetail> & Pick<AbstentionDetail, "reason_code">,
  status: Extract<QuestionStatus, "ABSTAINED" | "FAILED"> = "ABSTAINED",
) {
  const onRetry = vi.fn();
  render(<AbstentionNotice onRetry={onRetry} result={abstainedResult(abstention, status)} />);
  return { onRetry };
}

describe("AbstentionNotice", () => {
  it("names the generation-lane reason and what to do about it", () => {
    renderNotice({
      reason_code: "MODEL_DECLARED_INSUFFICIENT",
      message: "The retrieved guideline passages do not answer this question.",
    });

    // The headline stands alone. The service's own sentence here says the same thing in
    // almost the same words, and rendering both put one under the other in two different
    // faces; it stays reachable under Details.
    expect(
      screen.getByText("The retrieved passages do not answer this question"),
    ).toBeInTheDocument();
    const repeated = screen.getByText(
      "The retrieved guideline passages do not answer this question.",
    );
    expect(repeated.closest("details")).not.toBeNull();
    expect(
      screen.getByText(
        /Narrow the question to a decision the guideline addresses, or widen the source scope\./,
      ),
    ).toBeInTheDocument();
  });

  it("distinguishes a grounding failure from an absence of evidence", () => {
    renderNotice({
      reason_code: "NO_CLAIM_SURVIVED_GROUNDING",
      closest_evidence_ids: ["EV_WHO_HTN_003", "EV_WHO_HTN_014"],
    });

    expect(screen.getByText("No proposed claim was fully supported")).toBeInTheDocument();
    expect(screen.getByText("What came closest")).toBeInTheDocument();
    expect(screen.getByText("EV_WHO_HTN_003")).toBeInTheDocument();
  });

  it("renders no retry action where a retry alone will not change the outcome", () => {
    renderNotice({ reason_code: "NO_ACTIVE_RELEASE" });

    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Activate an approved corpus release, then run the question again\./),
    ).toBeInTheDocument();
  });

  it("offers a retry where one could plausibly succeed", async () => {
    const user = userEvent.setup();
    const { onRetry } = renderNotice({ reason_code: "NO_EVIDENCE_RETRIEVED" });

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("lists the evidence roles the review did not verify", () => {
    renderNotice({
      reason_code: "NO_APPROVED_CORPUS",
      missing_evidence_roles: ["PRIMARY_SUPPORT", "EXCEPTION_OR_CONTRAINDICATION"],
    });

    expect(screen.getByText("Could not verify")).toBeInTheDocument();
    expect(
      screen.getByText("Primary support, Exception or contraindication"),
    ).toBeInTheDocument();
  });

  it("keeps an unrecognised reason code visible for support", async () => {
    const user = userEvent.setup();
    renderNotice({
      reason_code: "SOME_FUTURE_CODE",
      message: "A future gate withheld the answer.",
    });

    await user.click(screen.getByText("Details"));

    expect(screen.getByText("SOME_FUTURE_CODE")).toBeInTheDocument();
    expect(screen.getByText(/not recognised by this interface/)).toBeInTheDocument();
    expect(screen.getByText("A future gate withheld the answer.")).toBeInTheDocument();
  });

  it("presents a failed run as a failure, not as missing evidence", () => {
    renderNotice(
      { reason_code: "PIPELINE_FAILURE", message: "The evidence pipeline failed closed." },
      "FAILED",
    );

    expect(screen.getByText("The review could not be completed")).toBeInTheDocument();
    expect(screen.getByText("The evidence pipeline failed closed.")).toBeInTheDocument();
  });
});
