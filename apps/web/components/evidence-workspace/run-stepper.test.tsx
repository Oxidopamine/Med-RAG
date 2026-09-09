import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RunStepper } from "./run-stepper";

afterEach(cleanup);

describe("RunStepper", () => {
  it("names the current stage as the heading and shows the checks strip", () => {
    render(
      <RunStepper
        lifecycle="running"
        onCopyRunId={() => {}}
        progressEvents={[{ status: "RETRIEVING", occurredAt: "2026-08-28T10:00:01.000Z" }]}
        questionId="q-1"
        status="RETRIEVING"
      />,
    );

    expect(screen.getByRole("heading", { name: "Retrieving evidence" })).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
  });

  it("offers to copy the run ID only once one exists", () => {
    const { rerender } = render(
      <RunStepper
        lifecycle="running"
        onCopyRunId={() => {}}
        progressEvents={[]}
        questionId={null}
        status={null}
      />,
    );

    expect(screen.queryByRole("button", { name: "Copy run ID" })).not.toBeInTheDocument();

    rerender(
      <RunStepper
        lifecycle="running"
        onCopyRunId={() => {}}
        progressEvents={[]}
        questionId="q-1"
        status={null}
      />,
    );

    expect(screen.getByRole("button", { name: "Copy run ID" })).toBeInTheDocument();
  });
});
