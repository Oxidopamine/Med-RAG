import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ClinicalContext } from "@/lib/types";

import { ContextDialog } from "./context-dialog";

const context: ClinicalContext = {
  age: 74,
  sex: null,
  conditions: ["ATRIAL_FIBRILLATION"],
  known_absent_conditions: [],
  measurements: [
    {
      concept: "EGFR",
      value: 28,
      unit: "mL/min/1.73m2",
      provenance: "USER_TEXT_EXPLICIT",
    },
  ],
  special_populations: ["RENAL_IMPAIRMENT"],
  known_absent_special_populations: [],
  care_setting: null,
  jurisdiction: null,
  question_type: "treatment_guideline",
  topic: "anticoagulation",
  inferred_fields: [],
};

interface DialogHarnessProps {
  initialContext?: ClinicalContext;
  onSubmit: (value: ClinicalContext) => Promise<boolean>;
  showOriginal?: boolean;
}

function DialogHarness({
  initialContext = context,
  onSubmit,
  showOriginal = false,
}: DialogHarnessProps) {
  const [open, setOpen] = useState(true);
  const [draft, setDraft] = useState(() => structuredClone(initialContext));
  return (
    <ContextDialog
      context={draft}
      onChange={setDraft}
      onOpenChange={setOpen}
      onSubmit={onSubmit}
      open={open}
      originalContext={showOriginal ? initialContext : undefined}
    />
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolver) => {
    resolve = resolver;
  });
  return { promise, resolve };
}

describe("ContextDialog", () => {
  it("provides dialog semantics and closes with Escape", async () => {
    const user = userEvent.setup();
    render(<DialogHarness onSubmit={vi.fn(async () => true)} />);

    expect(
      screen.getByRole("dialog", { name: "Edit interpreted context" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/correct only facts you can confirm/i)).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("adds and removes normalized terminology chips with native suggestions", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<DialogHarness onSubmit={onSubmit} />);

    const absentInput = screen.getByRole("combobox", {
      name: "Add known absent conditions",
    });
    expect(absentInput).toHaveAttribute("list");

    await user.type(absentInput, "diabetes mellitus{Enter}");
    expect(
      screen.getByRole("button", {
        name: "Remove Diabetes mellitus from known absent conditions",
      }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: "Remove Diabetes mellitus from known absent conditions",
      }),
    );
    await user.type(absentInput, "pregnancy, pregnancy{Enter}");
    expect(screen.getByText(/pregnancy is already added/i)).toBeInTheDocument();

    await user.clear(absentInput);
    await user.type(absentInput, "diabetes mellitus{Enter}");
    await user.click(screen.getByRole("button", { name: "Run again" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ known_absent_conditions: ["PREGNANCY", "DIABETES_MELLITUS"] }),
      ),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows contradiction errors inline and prevents invalid submission", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<DialogHarness onSubmit={onSubmit} />);

    await user.type(
      screen.getByRole("combobox", { name: "Add known absent conditions" }),
      "atrial fibrillation{Enter}",
    );

    expect(
      screen.getByText("ATRIAL_FIBRILLATION cannot be both present and absent"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run again" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent(/highlighted field/i);
    expect(
      screen.getByRole("combobox", { name: "Add known absent conditions" }),
    ).toHaveFocus();
  });

  it("preserves unknown select values and summarizes before-and-after changes", async () => {
    const user = userEvent.setup();
    const initialContext = { ...context, sex: "SELF_DESCRIBED" };
    render(
      <DialogHarness
        initialContext={initialContext}
        onSubmit={vi.fn(async () => true)}
        showOriginal
      />,
    );

    const sex = screen.getByRole("combobox", { name: "Sex" });
    expect(sex).toHaveValue("SELF_DESCRIBED");
    expect(
      within(sex).getByRole("option", { name: "Self described (current value)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("No changes yet.")).toBeInTheDocument();

    const age = screen.getByRole("spinbutton", { name: "Age" });
    await user.clear(age);
    await user.type(age, "75");
    await user.tab();
    expect(sex).toHaveFocus();

    const summary = screen.getByRole("heading", { name: "Changes to review" }).parentElement;
    expect(summary).not.toBeNull();
    expect(within(summary!).getByText("Age")).toBeInTheDocument();
    expect(within(summary!).getByText("74")).toBeInTheDocument();
    expect(within(summary!).getByText("75")).toBeInTheDocument();
  });

  it("edits measurement concepts with native suggestions", async () => {
    const user = userEvent.setup();
    render(<DialogHarness onSubmit={vi.fn(async () => true)} />);

    const concept = screen.getByRole("combobox", { name: "Concept" });
    const unit = screen.getByRole("combobox", { name: "Unit" });
    expect(concept).toHaveAttribute("list");
    expect(unit).toHaveAttribute("list");

    await user.clear(concept);
    await user.type(concept, "creatinine clearance");
    await user.tab();
    expect(screen.getByRole("spinbutton", { name: "Value" })).toHaveFocus();

    await waitFor(() => expect(concept).toHaveValue("creatinine clearance"));
  });

  it("validates a cleared measurement value inline", async () => {
    const user = userEvent.setup();
    render(<DialogHarness onSubmit={vi.fn(async () => true)} />);

    await user.clear(screen.getByRole("spinbutton", { name: "Value" }));
    await user.tab();
    expect(screen.getByRole("combobox", { name: "Unit" })).toHaveFocus();

    expect(screen.getByText("Enter a valid numeric value.")).toBeInTheDocument();
  });

  it("adds and removes measurement rows", async () => {
    const user = userEvent.setup();
    render(<DialogHarness onSubmit={vi.fn(async () => true)} />);

    await user.click(screen.getByRole("button", { name: "Add measurement" }));
    expect(screen.getAllByRole("group", { name: /Measurement \d/ })).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Remove 2 measurement" }));
    expect(screen.getAllByRole("group", { name: /Measurement \d/ })).toHaveLength(1);
  });

  it("normalizes an edited measurement before submission", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<DialogHarness onSubmit={onSubmit} />);

    const concept = screen.getByRole("combobox", { name: "Concept" });
    const value = screen.getByRole("spinbutton", { name: "Value" });
    await user.clear(concept);
    await user.type(concept, "creatinine clearance");
    await user.clear(value);
    await user.type(value, "31");

    await user.click(screen.getByRole("button", { name: "Run again" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          measurements: [
            expect.objectContaining({ concept: "CREATININE_CLEARANCE", value: 31 }),
          ],
        }),
      ),
    );
  });

  it("keeps edits open after a rejected rerun and disables only submit while pending", async () => {
    const user = userEvent.setup();
    const request = deferred<boolean>();
    const onSubmit = vi.fn(() => request.promise);
    render(<DialogHarness onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "Run again" }));

    const pendingButton = screen.getByRole("button", { name: "Running..." });
    expect(pendingButton).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.getByRole("spinbutton", { name: "Age" })).toBeEnabled();

    request.resolve(false);

    expect(
      await screen.findByText(/your edits are still here, so you can try again/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });
});
