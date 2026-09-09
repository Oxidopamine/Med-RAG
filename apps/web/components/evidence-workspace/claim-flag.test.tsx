import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { clearFlags, flagsSnapshot } from "@/lib/flags";

import { ClaimFlag } from "./claim-flag";

afterEach(() => {
  cleanup();
  clearFlags();
});

describe("ClaimFlag", () => {
  it("raises a flag with a reason and a note, keeps it in this browser, and withdraws it", async () => {
    const user = userEvent.setup();
    render(
      <ClaimFlag claimId="CL_1" claimText="Measure viral load yearly." evidenceIds={["EV_1"]} questionId="Q_1" />,
    );
    await user.click(screen.getByRole("button", { name: "Flag this claim" }));
    await user.click(screen.getByRole("radio", { name: "The claim misreads the passage" }));
    await user.type(screen.getByRole("textbox", { name: "Note" }), "Six-monthly, not yearly.");
    await user.click(screen.getByRole("button", { name: "Save flag" }));

    expect(screen.getByText(/Flagged: The claim misreads the passage — Six-monthly, not yearly\./)).toBeInTheDocument();
    expect(flagsSnapshot()).toHaveLength(1);
    expect(flagsSnapshot()[0]).toMatchObject({
      questionId: "Q_1",
      claimId: "CL_1",
      reason: "misread",
      note: "Six-monthly, not yearly.",
      evidenceIds: ["EV_1"],
    });
    expect(JSON.parse(window.localStorage.getItem("sentinel-rag.flags.v1") ?? "[]")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(flagsSnapshot()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Flag this claim" })).toBeInTheDocument();
  });
});
