import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { RankedEvidence } from "@/lib/presentation";
import type { EvidenceDetail } from "@/lib/types";

import { SourceViewer } from "./source-verification-column";

afterEach(cleanup);

function detail(evidenceId: string, title: string): EvidenceDetail {
  return {
    evidence_id: evidenceId,
    exact_text: `Passage text for ${evidenceId}.`,
    evidence_type: "RECOMMENDATION",
    evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
    section_path: ["Anticoagulation"],
    source_id: "source-1",
    source_version_id: "source-version-1",
    source_title: title,
    source_version_label: "2026",
    publisher_name: "Example Cardiology Society",
    source_url: "https://guidelines.example/atrial-fibrillation",
    source_class: "PROFESSIONAL_GUIDELINE",
    jurisdiction: "US",
    language: "en",
    lifecycle_status: "CURRENT",
    effective_from: "2026-01-01",
    effective_to: null,
    approval_status: "APPROVED",
    render_allowed: true,
    locators: [
      {
        kind: "PDF_PAGE",
        source_uri: "source://source-version-1/page/47",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
      },
    ],
  };
}

const CITED = [detail("evidence-1", "Cited guideline")];
const CANDIDATES: RankedEvidence[] = [
  { detail: detail("evidence-2", "Uncited guideline"), retrievalRank: 3 },
];

function ViewerHarness({ candidates = CANDIDATES }: { candidates?: RankedEvidence[] }) {
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>("evidence-1");
  return (
    <SourceViewer
      candidates={candidates}
      evidence={CITED}
      onSelectEvidence={setSelectedEvidenceId}
      selectedEvidenceId={selectedEvidenceId}
    />
  );
}

describe("SourceViewer", () => {
  it("steps past the last citation into the next ranked passage", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);

    expect(screen.getByText("Evidence 1 of 1")).toBeInTheDocument();
    expect(screen.getByText("Canonical evidence")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next ranked result" }));

    expect(screen.getByText("Ranked passage 1 of 1, uncited")).toBeInTheDocument();
    expect(screen.getByText("Passage text for evidence-2.")).toBeInTheDocument();
  });

  it("marks a passage no claim cites so it cannot read as support", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);
    await user.click(screen.getByRole("button", { name: "Next ranked result" }));

    expect(screen.queryByText("Canonical evidence")).not.toBeInTheDocument();
    expect(screen.getByText("Retrieved, not cited")).toBeInTheDocument();
    expect(screen.getByText(/Retrieved at rank 3\. No claim cites it\./)).toBeInTheDocument();
  });

  it("stops at the last citation when retrieval offered nothing further", () => {
    render(<ViewerHarness candidates={[]} />);

    expect(screen.getByRole("button", { name: "Next ranked result" })).toBeDisabled();
  });

  it("returns from a ranked passage to the cited evidence", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);
    await user.click(screen.getByRole("button", { name: "Next ranked result" }));
    await user.click(screen.getByRole("button", { name: "Previous ranked result" }));

    expect(screen.getByText("Evidence 1 of 1")).toBeInTheDocument();
    expect(screen.getByText("Canonical evidence")).toBeInTheDocument();
  });
});
