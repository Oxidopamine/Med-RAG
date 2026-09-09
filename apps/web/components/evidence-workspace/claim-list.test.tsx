import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildCitations } from "@/lib/evidence-presentation";
import { answerReadyResult } from "@/lib/fixtures/answer-lane";

import { ClaimList } from "./claim-list";

afterEach(cleanup);

function renderClaims(result = answerReadyResult()) {
  const onSelectClaim = vi.fn();
  const onSelectEvidence = vi.fn();
  render(
    <ClaimList
      citations={buildCitations(result)}
      claims={result.claims}
      onSelectClaim={onSelectClaim}
      onSelectEvidence={onSelectEvidence}
      result={result}
      selectedClaimId={result.claims[0]!.claim_id}
      selectedEvidenceId={null}
    />,
  );
  return { onSelectClaim, onSelectEvidence };
}

describe("ClaimList", () => {
  it("shows every claim with the evidence it rests on", () => {
    renderClaims();

    const claims = within(screen.getByRole("list", { name: "Recommendations" })).getAllByRole(
      "listitem",
    );
    expect(claims).toHaveLength(2);
    expect(screen.getByText(/Pharmacological treatment is recommended/)).toBeInTheDocument();

    // Citations are superscript numbers on the claim itself, one per cited passage.
    expect(within(claims[0]!).getAllByRole("button", { name: /^Reference \d+:/ })).toHaveLength(2);

    // The gate decided this claim could be rendered by reading the roles its evidence
    // carries; the line under the claim names them, so "supported" is a statement
    // rather than a badge.
    expect(claims[0]!).toHaveTextContent(/Supported · current primary guideline/);
  });

  it("keeps one reference number per source across the whole answer", () => {
    renderClaims();

    expect(
      screen.getByRole("button", { name: /^Reference 1: World Health Organization 2021/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Reference 3: World Health Organization 2021/ }),
    ).toBeInTheDocument();
  });

  it("says a licence withholds the passage before the reader opens it", () => {
    renderClaims();

    expect(
      screen.getAllByRole("button", {
        name: /Licence does not permit showing the passage text$/,
      }).length,
    ).toBeGreaterThan(0);
  });

  it("does not mark a licensed source as withheld", () => {
    renderClaims(answerReadyResult({ licensed: true }));

    expect(
      screen.getByRole("button", { name: "Reference 1: World Health Organization 2021, Printed page 11 (PDF page 19), exact region" }),
    ).toBeInTheDocument();
  });

  it("selects the claim and the source together when a reference is chosen", async () => {
    const user = userEvent.setup();
    const { onSelectClaim, onSelectEvidence } = renderClaims();

    await user.click(screen.getByRole("button", { name: /^Reference 3:/ }));

    expect(onSelectClaim).toHaveBeenCalledWith("CL_002");
    expect(onSelectEvidence).toHaveBeenCalledWith("EV_WHO_HTN_002");
  });

  it("states the absence when a claim has no attributable citation", () => {
    const result = answerReadyResult();
    result.evidence_details = result.evidence_details.filter(
      (detail) => detail.evidence_id !== "EV_WHO_HTN_002",
    );
    renderClaims(result);

    expect(screen.getByText("The evidence for this claim could not be shown.")).toBeInTheDocument();
  });
});

describe("ClaimList, going to the evidence", () => {
  /*
   * A citation number is a click on the thing the reader wants to read: it selects the
   * claim and its passage, and asks to be taken to the source pane, which on a stacked
   * layout is the pane the tabs switch to.
   */
  it("selects the claim and its passage, and asks to be taken to the source pane", async () => {
    const user = userEvent.setup();
    const onInspectClaim = vi.fn();
    const onSelectClaim = vi.fn();
    const result = answerReadyResult();

    render(
      <ClaimList
        citations={buildCitations(result)}
        claims={result.claims}
        onInspectClaim={onInspectClaim}
        onSelectClaim={onSelectClaim}
        onSelectEvidence={vi.fn()}
        result={result}
        selectedClaimId={null}
        selectedEvidenceId={null}
      />,
    );

    const second = screen.getAllByRole("listitem")[1]!;
    await user.click(within(second).getAllByRole("button", { name: /^Reference \d+:/ })[0]!);

    expect(onInspectClaim).toHaveBeenCalledWith("CL_002");
    expect(onSelectClaim).toHaveBeenCalledWith("CL_002");
  });

  it("still selects where nothing offered to take the reader anywhere", async () => {
    const user = userEvent.setup();
    const onSelectClaim = vi.fn();
    const result = answerReadyResult();

    render(
      <ClaimList
        citations={buildCitations(result)}
        claims={result.claims}
        onSelectClaim={onSelectClaim}
        onSelectEvidence={vi.fn()}
        result={result}
        selectedClaimId={null}
        selectedEvidenceId={null}
      />,
    );

    await user.click(screen.getAllByRole("button", { name: /^Reference \d+:/ })[0]!);

    expect(onSelectClaim).toHaveBeenCalledWith("CL_001");
  });
});
