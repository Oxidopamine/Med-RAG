import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RunFile } from "@/lib/labelling";

import { LabellingWorkbench } from "./labelling-workbench";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/** Built the way `lib/labelling.test.ts` builds a run file, but with a single answered
 * record and a single claim so the seeded shuffle has nothing to reorder. */
function tinyProduction(): RunFile {
  return {
    run_label: "production-test",
    results: [
      {
        question_id: "Q1",
        question: "How should X be treated?",
        chapter_no: 1,
        chapter: "Intro",
        gate_reason: null,
        retrieval: {
          passages: [{ evidence_id: "EV_A", rendered_text: "Passage A text", kind: "NARRATIVE" }],
        },
        generation: {
          abstained: false,
          claims: [{ text: "The claim under test", evidence_ids: ["EV_A"] }],
        },
      },
    ],
  };
}

describe("LabellingWorkbench", () => {
  it("shows the setup section and hides the passes before a run is loaded", () => {
    render(<LabellingWorkbench />);
    expect(screen.getByRole("heading", { name: "Files" })).toBeInTheDocument();
    expect(screen.getByLabelText("Production run (required)")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Passes" })).not.toBeInTheDocument();
  });

  it("starts the census from an injected production run and conceals which arm answered", async () => {
    const user = userEvent.setup();
    render(<LabellingWorkbench initialFiles={{ production: tinyProduction() }} />);

    await user.click(screen.getByRole("button", { name: "Start the census" }));

    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByText("Item 1 of 1")).toBeInTheDocument();
    expect(within(panel).getByText("The claim under test")).toBeInTheDocument();
    expect(within(panel).queryByText(/production/i)).not.toBeInTheDocument();
    expect(within(panel).queryByText(/closed_book/i)).not.toBeInTheDocument();
    expect(within(panel).queryByText(/naive/i)).not.toBeInTheDocument();
  });

  it("labels a claim, updates the progress figure, and carries the label into the download", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.spyOn(URL, "createObjectURL");
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    render(<LabellingWorkbench initialFiles={{ production: tinyProduction() }} />);
    await user.click(screen.getByRole("button", { name: "Start the census" }));

    const agreementFigure = screen.getByText("Agreement").closest("div")!;
    expect(within(agreementFigure).getByText("0 / 1")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Agrees" }));

    expect(within(agreementFigure).getByText("1 / 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Download label file" }));

    expect(createObjectURL).toHaveBeenCalled();
    const blob = createObjectURL.mock.calls.at(-1)?.[0] as Blob;
    const text = await blob.text();
    const parsed = JSON.parse(text);
    expect(parsed.records.Q1.claims[0].agreement).toBe("AGREES");
    expect(parsed.passes.agreement.started_at).not.toBeNull();

    clickSpy.mockRestore();
    createObjectURL.mockRestore();
  });
});
