import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildCitations } from "@/lib/evidence-presentation";
import { answerReadyResult } from "@/lib/fixtures/answer-lane";
import type { QuestionResult } from "@/lib/types";

import { ConflictPanel } from "./conflict-panel";

afterEach(cleanup);

function renderPanel(result: QuestionResult = answerReadyResult()) {
  const onSelectEvidence = vi.fn();
  render(
    <ConflictPanel
      citations={buildCitations(result)}
      onSelectEvidence={onSelectEvidence}
      result={result}
      selectedEvidenceId={null}
    />,
  );
  return { onSelectEvidence };
}

describe("ConflictPanel", () => {
  it("names the kind of disagreement and what it means", () => {
    renderPanel();

    expect(screen.getByText("1 for review")).toBeInTheDocument();
    expect(screen.getByText("Superseded information")).toBeInTheDocument();
    expect(
      screen.getByText("One passage states guidance that a later passage revises."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Check the effective dates and version labels before relying on either passage.",
      ),
    ).toBeInTheDocument();
  });

  it("reports both clauses rather than resolving them", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [
          {
            conflict_type: "CONFLICTING_RECOMMENDATIONS",
            summary: "One edition recommends treating at a threshold the other does not.",
            evidence_ids: "EV_WHO_HTN_001, EV_WHO_HTN_003",
          },
        ],
      }),
    );

    expect(screen.getByText("Conflicting recommendations")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Both recommendations are shown as published. Choosing between them is a clinical judgement, not a system output.",
      ),
    ).toBeInTheDocument();
  });

  it("links every passage the conflict names, by its answer-wide reference number", async () => {
    const user = userEvent.setup();
    const { onSelectEvidence } = renderPanel();

    const passages = screen.getByRole("list", { name: "Passages in conflict" });
    expect(passages).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Reference 2:/ }));

    expect(onSelectEvidence).toHaveBeenCalledWith("EV_WHO_HTN_003");
  });

  it("distinguishes a checked-and-clear result from nothing being returned", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [
          {
            conflict_type: "NO_CONFLICT",
            summary: "The cited passages agree.",
            evidence_ids: "EV_WHO_HTN_001, EV_WHO_HTN_002",
          },
        ],
      }),
    );

    expect(screen.getByText("Checked, none open")).toBeInTheDocument();
    expect(screen.getByText(/reported no open disagreement across 1 check/)).toBeInTheDocument();
    expect(screen.queryByText("No conflict")).not.toBeInTheDocument();
  });

  it("says nothing was returned when the answer reported no conflict at all", () => {
    renderPanel(answerReadyResult({ conflicts: [] }));

    expect(screen.getByText("None returned")).toBeInTheDocument();
    expect(
      screen.getByText("No conflict was returned for the rendered claims."),
    ).toBeInTheDocument();
  });

  it("does not present an unrecognised type as though it had been classified", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [{ conflict_type: "DOSE_DISAGREEMENT", summary: "Doses differ." }],
      }),
    );

    expect(screen.getByText("Unclassified disagreement")).toBeInTheDocument();
    expect(screen.getByText("DOSE_DISAGREEMENT")).toBeInTheDocument();
    expect(screen.getByText("Doses differ.")).toBeInTheDocument();
  });

  it("keeps a record that predates the typed contract legible", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [{ organization: "Regional formulary", rationale: "Local policy is stricter." }],
      }),
    );

    expect(screen.getByText("Organization")).toBeInTheDocument();
    expect(screen.getByText("Regional formulary")).toBeInTheDocument();
    expect(screen.getByText("Rationale")).toBeInTheDocument();
  });

  it("names conflicting evidence no rendered claim cited", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [
          {
            conflict_type: "CONTRADICTORY_SOURCE",
            evidence_ids: "EV_WHO_HTN_001, EV_WHO_HTN_014",
          },
        ],
      }),
    );

    expect(
      screen.getByText("Also names EV_WHO_HTN_014, which no rendered claim cites."),
    ).toBeInTheDocument();
  });
});
