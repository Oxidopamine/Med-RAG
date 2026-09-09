import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CorpusCatalogue } from "@/lib/contracts";

const api = vi.hoisted(() => ({
  getCorpusCatalogue: vi.fn<() => Promise<CorpusCatalogue>>(),
}));

vi.mock("@/lib/api", () => ({
  getCorpusCatalogue: api.getCorpusCatalogue,
}));

import { CorpusCatalogueView } from "./corpus-catalogue";

afterEach(() => {
  cleanup();
  api.getCorpusCatalogue.mockReset();
});

const catalogue: CorpusCatalogue = {
  release: {
    corpus_release_id: "guidelines-2026-08",
    serving_mode: "ACTIVATED",
    state: "ACTIVE",
    contract_version: "1",
    manifest_sha256: "a".repeat(64),
    cutoff_at: "2026-08-01T00:00:00Z",
    evidence_count: 1180,
    index_status: "VALIDATED",
    validated_at: "2026-08-20T00:00:00Z",
    activated_at: "2026-08-24T11:00:00Z",
    activated_by: "control-plane",
  },
  sources: [
    {
      source_id: "SRC_1",
      title: "Consolidated guidelines on HIV prevention, testing, treatment and service delivery",
      publisher_id: "PUB_WHO",
      publisher_name: "World Health Organization",
      source_class: "E1",
      jurisdiction: "WORLD",
      canonical_url: "https://example.test/guideline",
      license_excerpt_allowed: true,
      license_render_allowed: false,
      versions: [
        {
          source_version_id: "SV_2021",
          version_label: "2021 edition",
          status: "EFFECTIVE",
          effective_from: "2021-07-16",
          effective_to: null,
          approved_for_retrieval: true,
          evidence_count: 900,
          page_count: 594,
        },
      ],
    },
  ],
  trust_roots: [
    {
      trust_root_id: "WHO_SMART_HIV",
      publisher_id: "PUB_WHO",
      publisher_name: "World Health Organization",
      title: "WHO SMART guidelines, HIV",
      scope: "The HIV digital adaptation kit and consolidated guidelines.",
      jurisdictions: ["WORLD"],
      enabled: true,
      last_reconciled_at: "2026-08-19T00:00:00Z",
    },
  ],
};

describe("CorpusCatalogueView", () => {
  it("names the served release, lists its documents with licence terms, and the registered scopes", async () => {
    api.getCorpusCatalogue.mockResolvedValue(catalogue);
    render(<CorpusCatalogueView />);
    await waitFor(() =>
      expect(screen.getByText("Approved release guidelines-2026-08")).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: /Consolidated guidelines on HIV prevention/ })).toHaveAttribute(
      "href",
      "/sources/SRC_1",
    );
    expect(screen.getByText("Quote only")).toBeInTheDocument();
    expect(screen.getByText("2021 edition")).toBeInTheDocument();
    expect(screen.getByText("WHO SMART guidelines, HIV")).toBeInTheDocument();
    // The catalogue names documents; it never carries a passage.
    expect(screen.queryByText(/Viral load should be measured/)).not.toBeInTheDocument();
  });

  it("says plainly when nothing is served", async () => {
    api.getCorpusCatalogue.mockResolvedValue({ release: null, sources: [], trust_roots: [] });
    render(<CorpusCatalogueView />);
    await waitFor(() => expect(screen.getByText(/No release is being served/)).toBeInTheDocument());
  });

  it("reports an unreachable service without pretending the catalogue is empty", async () => {
    api.getCorpusCatalogue.mockRejectedValue(new Error("refused"));
    render(<CorpusCatalogueView />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/could not be reached/), {
      timeout: 10_000,
    });
  }, 15_000);
});
