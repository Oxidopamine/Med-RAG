import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QUESTION_BANK } from "@/lib/question-bank";

import { QuestionBank } from "./question-bank";

afterEach(cleanup);

describe("QuestionBank", () => {
  it("opens on demand, filters by search and area, and reports the count", async () => {
    const user = userEvent.setup();
    const { container } = render(<QuestionBank disabled={false} onChoose={vi.fn()} />);

    const summary = container.querySelector("summary");
    expect(summary).not.toBeNull();
    await user.click(summary!);

    const list = screen.getByRole("list", { name: "Questions" });
    expect(within(list).getAllByRole("button")).toHaveLength(QUESTION_BANK.length);
    expect(screen.getByText(`${QUESTION_BANK.length} questions`, { selector: "p" })).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search questions" }), "viral load");
    const filtered = within(screen.getByRole("list", { name: "Questions" })).getAllByRole("button");
    expect(filtered.length).toBeGreaterThan(0);
    expect(filtered.length).toBeLessThan(QUESTION_BANK.length);
    expect(
      screen.getByText(`${filtered.length} of ${QUESTION_BANK.length} questions`, {
        selector: "p",
      }),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Clinical area" }), "checks");
    const checks = within(screen.getByRole("list", { name: "Questions" })).getAllByRole("button");
    expect(checks.every((button) => button.textContent?.includes("Behaviour checks"))).toBe(true);

    await user.clear(screen.getByRole("searchbox", { name: "Search questions" }));
    await user.type(screen.getByRole("searchbox", { name: "Search questions" }), "zzzz");
    expect(screen.queryByRole("list", { name: "Questions" })).toBeNull();
    expect(screen.getByText(/No question matches/)).toBeInTheDocument();
  });

  it("hands the chosen question to the composer and closes, without submitting", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    const { container } = render(<QuestionBank disabled={false} onChoose={onChoose} />);

    const summary = container.querySelector("summary")!;
    await user.click(summary);
    const first = QUESTION_BANK[0]!;
    await user.click(screen.getByRole("button", { name: new RegExp(first.question.slice(0, 30)) }));

    expect(onChoose).toHaveBeenCalledWith(first.question);
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it("closes on Escape and returns focus to the summary", async () => {
    const user = userEvent.setup();
    const { container } = render(<QuestionBank disabled={false} onChoose={vi.fn()} />);

    const summary = container.querySelector("summary")!;
    await user.click(summary);
    expect(summary.closest("details")).toHaveAttribute("open");
    await user.keyboard("{Escape}");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    expect(summary).toHaveFocus();
  });

  it("stays readable but inert while a review runs", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    const { container } = render(<QuestionBank disabled onChoose={onChoose} />);

    await user.click(container.querySelector("summary")!);
    const buttons = within(screen.getByRole("list", { name: "Questions" })).getAllByRole("button");
    expect(buttons.every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(onChoose).not.toHaveBeenCalled();
  });
});
