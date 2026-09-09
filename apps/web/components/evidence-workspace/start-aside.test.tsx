import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getCorpusCatalogue: vi.fn() }));
vi.mock("@/lib/api", () => ({ getCorpusCatalogue: api.getCorpusCatalogue }));

import { StartAside, type CorpusStatus } from "./start-aside";

afterEach(() => {
  cleanup();
  api.getCorpusCatalogue.mockReset();
  window.localStorage.clear();
});

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

describe("StartAside", () => {
  it("says it is checking while the readiness check runs", () => {
    render(<StartAside corpusStatus={status({ isLoading: true })} />);
    expect(screen.getByText("Checking corpus")).toBeInTheDocument();
    expect(api.getCorpusCatalogue).not.toHaveBeenCalled();
  });

  it("treats an unreachable service as unreachable, not as a withheld corpus", async () => {
    const user = userEvent.setup();
    const onRecheck = vi.fn();
    render(<StartAside corpusStatus={status({ error: "refused" })} onRecheck={onRecheck} />);
    expect(screen.getByText("Evidence service not reached")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  it("names an inactive corpus when the registry answered", () => {
    render(<StartAside corpusStatus={status({ registryAvailable: true })} />);
    expect(screen.getByText("No active corpus")).toBeInTheDocument();
  });

  it("shows the served release's facts and links to the corpus and the reviews", async () => {
    api.getCorpusCatalogue.mockResolvedValue({
      release: {
        corpus_release_id: "guidelines-2026-08",
        serving_mode: "ACTIVATED",
        state: "ACTIVE",
        contract_version: "1",
        manifest_sha256: "a".repeat(64),
        cutoff_at: "2026-08-01T00:00:00Z",
        evidence_count: 1180,
        index_status: "VALIDATED",
        validated_at: null,
        activated_at: "2026-08-24T11:00:00Z",
        activated_by: null,
      },
      sources: [
        {
          source_id: "SRC_1",
          title: "Consolidated guidelines",
          publisher_id: "PUB_WHO",
          publisher_name: "World Health Organization",
          source_class: "E1",
          jurisdiction: "WORLD",
          canonical_url: "https://example.test",
          license_excerpt_allowed: true,
          license_render_allowed: false,
          versions: [
            {
              source_version_id: "SV_1",
              version_label: "2021",
              status: "EFFECTIVE",
              effective_from: null,
              effective_to: null,
              approved_for_retrieval: true,
              evidence_count: 1180,
              page_count: null,
            },
          ],
        },
      ],
      trust_roots: [],
    });
    render(
      <StartAside
        corpusStatus={status({
          approvedCorpusAvailable: true,
          registryAvailable: true,
          releaseId: "guidelines-2026-08",
          servingMode: "ACTIVATED",
        })}
      />,
    );
    expect(screen.getByText("Approved corpus available")).toBeInTheDocument();
    expect(await screen.findByText("1180")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Every document in the release" })).toHaveAttribute("href", "/corpus");
    expect(screen.getByRole("link", { name: "All reviews" })).toHaveAttribute("href", "/reviews");
    expect(screen.getByText("None yet in this browser.")).toBeInTheDocument();
  });
});
