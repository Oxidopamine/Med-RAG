import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SourceFilters } from "@/lib/types";

import { EXAMPLE_QUESTIONS, QuestionComposer } from "./question-composer";

const defaultFilters: SourceFilters = {
  jurisdictions: ["US", "EU", "UK"],
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
  return (
    <QuestionComposer
      isRunning={isRunning}
      onChange={setQuestion}
      onSourceFiltersChange={setFilters}
      onStopWaiting={onStopWaiting}
      onSubmit={onSubmit}
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
    expect(
      screen.getByLabelText(
        "A focused question includes population, condition, and decision",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("No patient identifiers")).toBeInTheDocument();

    const question = screen.getByRole("textbox", { name: "Guideline question" });
    expect(question).toHaveValue("");
    expect(question).toHaveAttribute("aria-keyshortcuts", "Control+Enter Meta+Enter");
    expect(screen.getByRole("button", { name: "Review evidence" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: EXAMPLE_QUESTIONS[0] }));
    expect(question).toHaveValue(EXAMPLE_QUESTIONS[0]);
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

  it("keeps valid coverage, tokenizes organizations, and submits drafts without blur", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<ComposerHarness onSubmit={onSubmit} />);

    await user.click(screen.getByText("Sources", { exact: true }));
    const us = screen.getByRole("checkbox", { name: "US, United States" });
    const eu = screen.getByRole("checkbox", { name: "EU, European Union" });
    const uk = screen.getByRole("checkbox", { name: "UK, United Kingdom" });
    await user.click(us);
    await user.click(eu);
    await user.click(uk);

    expect(uk).toBeChecked();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Keep at least one jurisdiction selected.",
    );

    const organizations = screen.getByRole("textbox", { name: "Organizations" });
    await user.type(organizations, "ACC, AHA, acc");
    await user.type(
      screen.getByRole("textbox", { name: "Guideline question" }),
      "A focused guideline question",
    );
    await user.click(screen.getByRole("button", { name: "Review evidence" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith("A focused guideline question", {
        jurisdictions: ["UK"],
        organizations: ["ACC", "AHA"],
      }),
    );
  });

  it("supports complete scope disclosure behavior", async () => {
    const user = userEvent.setup();
    render(
      <ComposerHarness
        initialFilters={{ jurisdictions: ["US"], organizations: ["ACC"] }}
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
    expect(screen.getByRole("checkbox", { name: "US, United States" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "EU, European Union" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "UK, United Kingdom" })).toBeChecked();
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
    expect(screen.getByRole("button", { name: "Starting..." })).toBeDisabled();
    expect(question).toHaveAttribute("readonly");

    request.resolve(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review evidence" })).toBeEnabled(),
    );

    const onStopWaiting = vi.fn();
    rerender(
      <ComposerHarness
        isRunning
        onStopWaiting={onStopWaiting}
        onSubmit={vi.fn(async () => true)}
      />,
    );
    expect(screen.getByRole("button", { name: "Reviewing..." })).toBeDisabled();
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
