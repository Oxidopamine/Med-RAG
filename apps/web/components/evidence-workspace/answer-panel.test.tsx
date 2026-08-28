import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { answerReadyResult } from "@/lib/fixtures/answer-lane";

import { AnswerPanel } from "./answer-panel";

afterEach(cleanup);

function renderPanel(overrides: Parameters<typeof answerReadyResult>[0] = {}) {
  const onSelectEvidence = vi.fn();
  render(
    <AnswerPanel
      onCopyAnswer={vi.fn()}
      onRetry={vi.fn()}
      onSelectClaim={vi.fn()}
      onSelectEvidence={onSelectEvidence}
      result={answerReadyResult(overrides)}
      selectedClaimId={null}
      selectedEvidenceId={null}
    />,
  );
  return { onSelectEvidence };
}

/**
 * Two facts that change how the answer above should be read used to take three clicks
 * each to find - whether any cited edition has been superseded, and whether any cited
 * passage is withheld under licence. Both were discoverable only by opening every source
 * in turn.
 */
describe("AnswerPanel, what the answer rests on", () => {
  it("counts documents and editions, not citations", () => {
    renderPanel();

    // Asserted on the group rather than a text node: the count and the edition clause are
    // separate children, which is exactly the case `getByText` cannot see across.
    const summary = screen.getByRole("group", { name: "Cited source summary" });
    // Three cited passages, but one guideline - quoted at two editions, which is the case
    // this strip exists to surface. Counting the passages would have called it 3 sources.
    expect(summary).toHaveTextContent("1 source, 2 editions");
  });

  it("says nothing about editions when each source contributes one", () => {
    render(
      <AnswerPanel
        onCopyAnswer={vi.fn()}
        onRetry={vi.fn()}
        onSelectClaim={vi.fn()}
        onSelectEvidence={vi.fn()}
        result={{
          ...answerReadyResult(),
          claims: [
            {
              claim_id: "CL_001",
              text: "One claim, one passage.",
              evidence_ids: ["EV_WHO_HTN_001"],
              verification_status: "SUPPORTED",
            },
          ],
        }}
        selectedClaimId={null}
        selectedEvidenceId={null}
      />,
    );

    const summary = screen.getByRole("group", { name: "Cited source summary" });
    expect(summary).toHaveTextContent("1 source");
    expect(summary).not.toHaveTextContent("edition,");
    expect(summary).not.toHaveTextContent("1 edition");
  });

  it("says how many cited editions are no longer in force", () => {
    renderPanel();

    expect(screen.getByRole("button", { name: /1 superseded edition/ })).toBeInTheDocument();
  });

  it("says how many cited passages the licence withholds", () => {
    renderPanel({ licensed: true });

    // The primary record is licensed; the other two cited records are not.
    expect(screen.getByRole("button", { name: /2 withheld by licence/ })).toBeInTheDocument();
  });

  /*
   * The counts are a way in rather than a statistic: each one opens the first record it
   * counted, which is the record the reader would otherwise have gone hunting for.
   */
  it("opens the first record a count is about", async () => {
    const user = userEvent.setup();
    const { onSelectEvidence } = renderPanel();

    await user.click(screen.getByRole("button", { name: /1 superseded edition/ }));

    expect(onSelectEvidence).toHaveBeenCalledWith("EV_WHO_HTN_003");
  });

  it("reports nothing where there is nothing to report", () => {
    render(
      <AnswerPanel
        onCopyAnswer={vi.fn()}
        onRetry={vi.fn()}
        onSelectClaim={vi.fn()}
        onSelectEvidence={vi.fn()}
        result={{ ...answerReadyResult(), claims: [] }}
        selectedClaimId={null}
        selectedEvidenceId={null}
      />,
    );

    expect(
      screen.queryByRole("group", { name: "Cited source summary" }),
    ).not.toBeInTheDocument();
  });
});
