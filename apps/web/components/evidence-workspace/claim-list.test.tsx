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

    const claims = screen.getAllByRole("listitem");
    expect(screen.getByText(/Pharmacological treatment is recommended/)).toBeInTheDocument();
    expect(screen.getByText("Claim 1 of 2 · Supported")).toBeInTheDocument();

    const firstClaimCitations = within(claims[0]!).getByRole("list", {
      name: "Evidence cited by claim 1",
    });
    expect(within(firstClaimCitations).getAllByRole("button")).toHaveLength(2);

    // The gate decided this claim could be rendered by reading the roles its evidence
    // carries; the claim shows them, so "supported" is a statement rather than a badge.
    const roles = within(claims[0]!).getByRole("list", {
      name: "Evidence roles behind claim 1",
    });
    expect(within(roles).getByText(/Current primary guideline/)).toBeInTheDocument();
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

    expect(
      screen.getByText("Cited evidence for this claim is unavailable in this result."),
    ).toBeInTheDocument();
  });
});

describe("ClaimList, going to the evidence", () => {
  /*
   * A citation chip is already a click on the thing you want to read, so the inspector
   * updating under it is the whole action. "Inspect evidence" is a request to go
   * somewhere, and on a stacked layout that somewhere was several screens below the
   * button - which left the reader looking at the button.
   */
  it("asks to be taken to the inspector, not merely to select", async () => {
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

    await user.click(screen.getAllByRole("button", { name: /Inspect evidence/ })[1]!);

    expect(onInspectClaim).toHaveBeenCalledWith("CL_002");
    expect(onSelectClaim).not.toHaveBeenCalled();
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

    await user.click(screen.getAllByRole("button", { name: /Inspect evidence/ })[0]!);

    expect(onSelectClaim).toHaveBeenCalledWith("CL_001");
  });
});
