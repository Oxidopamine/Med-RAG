import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GettingStarted, type CorpusStatus } from "./getting-started";

afterEach(cleanup);

function status(overrides: Partial<CorpusStatus>): CorpusStatus {
  return {
    approvedCorpusAvailable: false,
    error: null,
    isLoading: false,
    registryAvailable: false,
    releaseId: null,
    servingMode: null,
    ...overrides,
  };
}

describe("GettingStarted coverage", () => {
  it("says it is checking while the readiness check runs", () => {
    render(<GettingStarted corpusStatus={status({ isLoading: true })} />);
    expect(screen.getByText("Checking corpus")).toBeInTheDocument();
    expect(screen.queryByText("Evidence service not reached")).not.toBeInTheDocument();
  });

  it("treats an unreachable service as unreachable, not as a withheld corpus", async () => {
    const user = userEvent.setup();
    const onRecheck = vi.fn();
    render(
      <GettingStarted corpusStatus={status({ error: "refused" })} onRecheck={onRecheck} />,
    );
    expect(screen.getByText("Evidence service not reached")).toBeInTheDocument();
    expect(screen.queryByText(/fail closed/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  it("names an inactive corpus when the registry answered", () => {
    render(<GettingStarted corpusStatus={status({ registryAvailable: true })} />);
    expect(screen.getByText("No active corpus")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
  });
});
