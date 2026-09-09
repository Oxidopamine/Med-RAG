import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildCitations } from "@/lib/evidence-presentation";
import { answerReadyResult } from "@/lib/fixtures/answer-lane";
import type { QuestionResult } from "@/lib/types";

import { ConflictPanel } from "./conflict-panel";

afterEach(cleanup);

function renderPanel(
  result: QuestionResult = answerReadyResult(),
  onCompareEvidence?: (evidenceId: string, againstEvidenceId: string) => void,
) {
  const onSelectEvidence = vi.fn();
  render(
    <ConflictPanel
      citations={buildCitations(result)}
      onCompareEvidence={onCompareEvidence}
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

    expect(screen.getByRole("heading", { level: 2, name: "Conflicts" })).toBeInTheDocument();
    expect(screen.getByText("1 for review")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Superseded information" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("One passage states guidance that a later passage revises."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Check the effective dates and version labels before relying on either passage.",
      ),
    ).toBeInTheDocument();
  });

  it("reports both clauses rather than resolving them, as two sides of one comparison", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [
          {
            conflict_type: "CONFLICTING_RECOMMENDATIONS",
            summary: "One edition recommends treating at a threshold the other does not.",
            evidence_ids: ["EV_WHO_HTN_001", "EV_WHO_HTN_003"],
          },
        ],
      }),
    );

    expect(
      screen.getByRole("heading", { level: 3, name: "Conflicting recommendations" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("One edition recommends treating at a threshold the other does not."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Both recommendations are shown as published."),
    ).toBeInTheDocument();

    // Both passages resolve, so they render as two buttons in a side-by-side comparison
    // rather than as a merged or narrated outcome.
    const sides = screen.getAllByRole("button", {
      name: /Passage text withheld by licence\./,
    });
    expect(sides).toHaveLength(2);
  });

  it("shows both passages side by side and lets the reader open either", async () => {
    const user = userEvent.setup();
    const { onSelectEvidence } = renderPanel(
      answerReadyResult({
        licensed: true,
        conflicts: [
          {
            conflict_type: "OUTDATED_INFORMATION",
            summary:
              "The 2013 edition states a higher treatment threshold than the 2021 edition for the same population.",
            evidence_ids: ["EV_WHO_HTN_001", "EV_WHO_HTN_003"],
          },
        ],
      }),
    );

    const currentSide = screen.getByRole("button", { name: /In force/ });
    expect(currentSide).toHaveTextContent(
      "Pharmacological treatment is recommended for adults with confirmed hypertension and systolic blood pressure of 140 mmHg or greater.",
    );

    const supersededSide = screen.getByRole("button", { name: /Superseded/ });
    expect(supersededSide).toBeInTheDocument();
    expect(supersededSide).toHaveTextContent("Passage text withheld by licence.");

    await user.click(currentSide);
    expect(onSelectEvidence).toHaveBeenCalledWith("EV_WHO_HTN_001");

    await user.click(supersededSide);
    expect(onSelectEvidence).toHaveBeenCalledWith("EV_WHO_HTN_003");
  });

  it("lets the reader open both compared passages together in the source pane", async () => {
    const user = userEvent.setup();
    const onCompareEvidence = vi.fn();
    renderPanel(answerReadyResult(), onCompareEvidence);

    await user.click(screen.getByRole("button", { name: "Compare in the source pane" }));

    expect(onCompareEvidence).toHaveBeenCalledWith("EV_WHO_HTN_001", "EV_WHO_HTN_003");
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
            evidence_ids: ["EV_WHO_HTN_001", "EV_WHO_HTN_002"],
          },
        ],
      }),
    );

    // One line, but it still has to say which of the two absences this is: a check that
    // ran and cleared, not a check that never reported.
    expect(screen.getByRole("heading", { level: 2, name: "Conflicts" })).toBeInTheDocument();
    expect(screen.getByText("Checked, none open across 1 check")).toBeInTheDocument();
    expect(screen.queryByText(/No conflicts found/)).not.toBeInTheDocument();
  });

  it("says nothing was returned when the answer reported no conflict at all", () => {
    renderPanel(answerReadyResult({ conflicts: [] }));

    expect(screen.getByText("No conflicts found among these claims.")).toBeInTheDocument();
    expect(screen.queryByText(/Checked, none open/)).not.toBeInTheDocument();
  });

  it("does not present an unrecognised type as though it had been classified", () => {
    renderPanel(
      answerReadyResult({
        conflicts: [{ conflict_type: "DOSE_DISAGREEMENT", summary: "Doses differ." }],
      }),
    );

    expect(
      screen.getByRole("heading", { level: 3, name: "Unclassified disagreement" }),
    ).toBeInTheDocument();
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
            evidence_ids: ["EV_WHO_HTN_001", "EV_WHO_HTN_014"],
          },
        ],
      }),
    );

    expect(
      screen.getByText("Also names EV_WHO_HTN_014, which no rendered claim cites."),
    ).toBeInTheDocument();
  });
});
