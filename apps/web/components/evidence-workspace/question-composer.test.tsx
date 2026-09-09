import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SourceFilters } from "@/lib/types";

import { EXAMPLES, QuestionComposer } from "./question-composer";

/** The example questions carry regex metacharacters, so they are matched literally. */
function escapeRegExp(value: string): string {
  return value.replaceAll(/[.*+?^${}()|[\]\\]/g, String.raw`\$&`);
}

const defaultFilters: SourceFilters = {
  jurisdictions: ["WORLD"],
  organizations: [],
};

afterEach(cleanup);

function ComposerHarness({
  initialFilters = defaultFilters,
  isRunning = false,
  onStopWaiting = vi.fn(),
  onSubmit,
}: {
  initialFilters?: SourceFilters;
  isRunning?: boolean;
  onStopWaiting?: () => void;
  onSubmit: (question: string, filters: SourceFilters) => Promise<boolean>;
}) {
  const [question, setQuestion] = useState("");
  const [filters, setFilters] = useState(initialFilters);
  /*
   * The workspace marks the run live synchronously inside `onSubmit`, before its first
   * await, so the composer is locked in the same commit as the click that started it. The
   * harness has to do the same: a parent that leaves `isRunning` false across an in-flight
   * submission does not exist, and testing against one would only prove the composer keeps
   * a second copy of the run state - which is the bug that let a hung request lock the
   * controls with nothing able to release them.
   */
  const [started, setStarted] = useState(false);
  async function submit(value: string, submittedFilters: SourceFilters) {
    setStarted(true);
    try {
      return await onSubmit(value, submittedFilters);
    } finally {
      setStarted(false);
    }
  }
  return (
    <QuestionComposer
      isRunning={isRunning || started}
      isStarting={started}
      onChange={setQuestion}
      onSourceFiltersChange={setFilters}
      onStopWaiting={onStopWaiting}
      onSubmit={submit}
      question={question}
      showExamples
      sourceFilters={filters}
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

describe("QuestionComposer", () => {
  it("communicates the evidence task and turns examples into editable starting points", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<ComposerHarness onSubmit={onSubmit} />);

    expect(
      screen.getByRole("heading", { name: "What guideline decision are you reviewing?" }),
    ).toBeInTheDocument();
    // The Population/Condition/Decision chips are visual cues and are hidden from
    // assistive technology on purpose: the same guidance reaches it as the field's own
    // description, in prose, which is asserted there rather than here.
    expect(screen.getByText("Population", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Condition", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Decision", { exact: true })).toBeInTheDocument();

    const question = screen.getByRole("textbox", { name: "Guideline question" });
    expect(question).toHaveValue("");
    expect(question).toHaveAttribute("aria-keyshortcuts", "Control+Enter Meta+Enter");
    expect(question).toHaveAccessibleDescription(
      /Include the population, condition, and clinical decision/i,
    );

    const submit = screen.getByRole("button", { name: "Review evidence" });
    expect(submit).toHaveAttribute("aria-disabled", "true");
    // Marked unavailable, never natively disabled: a disabled control loses focus, and
    // this one is made unavailable at the moment it is holding it.
    expect(submit).not.toBeDisabled();

    // The accessible name leads with the visible topic, so speech input can activate the
    // button by what it reads (WCAG 2.5.3), and still carries the question it inserts.
    const example = screen.getByRole("button", {
      name: new RegExp(`^${escapeRegExp(EXAMPLES[0]!.label)}: `),
    });
    expect(example).toHaveAccessibleName(new RegExp(escapeRegExp(EXAMPLES[0]!.question)));
    await user.click(example);
    expect(question).toHaveValue(EXAMPLES[0]!.question);
    expect(question).toHaveFocus();
    expect(onSubmit).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Clear question" }));
    expect(question).toHaveFocus();
    expect(question).toHaveValue("");
  });

  it("keeps Enter multiline and supports both platform submission shortcuts", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<ComposerHarness onSubmit={onSubmit} />);

    const question = screen.getByRole("textbox", { name: "Guideline question" });
    await user.type(question, "Population and condition{Enter}clinical decision");
    expect(question).toHaveValue("Population and condition\nclinical decision");
    expect(onSubmit).not.toHaveBeenCalled();

    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());

    await user.clear(question);
    await user.type(question, "A second focused guideline question");
    await user.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
  });

  it("shows calm validation only after submission and focuses the question", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<ComposerHarness onSubmit={onSubmit} />);

    const question = screen.getByRole("textbox", { name: "Guideline question" });
    await user.type(question, "no");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Review evidence" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Add a little more detail, such as the population and clinical decision.",
    );
    expect(question).toHaveFocus();
    expect(question).toHaveAttribute("aria-invalid", "true");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("states the release scope, tokenizes organizations, and submits drafts without blur", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<ComposerHarness onSubmit={onSubmit} />);

    await user.click(screen.getByText("Sources", { exact: true }));

    // No jurisdiction control: every record in the active release is scoped WORLD and the
    // serving path widens any selection to include it, so a country filter could only
    // claim to narrow. Coverage is stated instead, and travels unchanged to the request.
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    // The panel names every guideline source this product intends to cover and marks the
    // ones that are not served yet, so the corpus boundary is visible rather than implied
    // by an empty answer. None of them is selectable.
    const bodies = screen.getByRole("list", { name: "Guideline sources" });
    expect(within(bodies).getByText("WHO HIV guidelines")).toBeInTheDocument();
    expect(within(bodies).getByText("Active")).toBeInTheDocument();
    expect(within(bodies).getAllByText("Coming soon")).toHaveLength(3);
    expect(within(bodies).queryByRole("button")).not.toBeInTheDocument();
    expect(within(bodies).queryByRole("checkbox")).not.toBeInTheDocument();

    const organizations = screen.getByRole("textbox", { name: "Organizations" });
    await user.type(organizations, "ACC, AHA, acc");
    await user.type(
      screen.getByRole("textbox", { name: "Guideline question" }),
      "A focused guideline question",
    );
    await user.click(screen.getByRole("button", { name: "Review evidence" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith("A focused guideline question", {
        jurisdictions: ["WORLD"],
        organizations: ["ACC", "AHA"],
      }),
    );
  });

  it("supports complete scope disclosure behavior", async () => {
    const user = userEvent.setup();
    render(
      <ComposerHarness
        initialFilters={{ jurisdictions: ["WORLD"], organizations: ["ACC"] }}
        onSubmit={vi.fn(async () => true)}
      />,
    );

    const summary = screen.getByText("Sources", { exact: true }).closest("summary")!;
    const details = summary.closest("details")!;
    await user.click(summary);
    expect(details).toHaveAttribute("open");
    expect(screen.getByRole("button", { name: "Remove ACC" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(details).not.toHaveAttribute("open");
    expect(summary).toHaveFocus();

    await user.click(summary);
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.queryByRole("button", { name: "Remove ACC" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(details).not.toHaveAttribute("open");
    expect(summary).toHaveFocus();
  });

  it("locks duplicate submissions immediately and binds the composer to a running review", async () => {
    const user = userEvent.setup();
    const request = deferred<boolean>();
    const onSubmit = vi.fn(() => request.promise);
    const { rerender } = render(<ComposerHarness onSubmit={onSubmit} />);

    const question = screen.getByRole("textbox", { name: "Guideline question" });
    await user.type(question, "A focused guideline question");
    await user.dblClick(screen.getByRole("button", { name: "Review evidence" }));

    expect(onSubmit).toHaveBeenCalledOnce();
    const starting = screen.getByRole("button", { name: "Starting..." });
    expect(starting).toHaveAttribute("aria-disabled", "true");
    expect(question).toHaveAttribute("readonly");
    // The point of not disabling it: the click that started the run left focus here, and
    // a run can take minutes. Disabling would hand focus back to <body> for all of it.
    expect(starting).toHaveFocus();

    request.resolve(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review evidence" })).toHaveAttribute(
        "aria-disabled",
        "false",
      ),
    );

    const onStopWaiting = vi.fn();
    rerender(
      <ComposerHarness
        isRunning
        onStopWaiting={onStopWaiting}
        onSubmit={vi.fn(async () => true)}
      />,
    );
    expect(screen.getByRole("button", { name: "Reviewing..." })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("textbox", { name: "Guideline question" })).toHaveAttribute(
      "readonly",
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Question and sources locked to this review",
    );

    await user.click(screen.getByRole("button", { name: "Stop waiting" }));
    expect(onStopWaiting).toHaveBeenCalledOnce();
  });
});
