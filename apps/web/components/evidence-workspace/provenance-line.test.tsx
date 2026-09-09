import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { QuestionResult } from "@/lib/types";

import { ProvenanceLine } from "./provenance-line";

afterEach(cleanup);

function result(overrides: Partial<QuestionResult> = {}): QuestionResult {
  return {
    question_id: "Q_0123456789abcdef0123456789",
    question: "How often should viral load be monitored?",
    status: "ANSWER_READY",
    corpus_release: {
      corpus_release_id: "guidelines-2026-08",
      manifest_sha256: "a".repeat(64),
      qdrant_collection: "guidelines",
      activated_at: "2026-08-24T11:00:00Z",
      serving_mode: "ACTIVATED",
    },
    interpreted_context: null,
    claims: [],
    evidence_details: [],
    retrieval_candidates: [],
    conflicts: [],
    verification_summary: { rendered_claims: 0, supported_claims: 0, withheld_claims: 0 },
    abstention: null,
    created_at: "2026-08-25T13:00:00Z",
    updated_at: "2026-08-25T13:00:10Z",
    ...overrides,
  } as QuestionResult;
}

describe("ProvenanceLine", () => {
  it("names an approved release with its activation date on one line", () => {
    render(<ProvenanceLine exports={[]} onCopyLink={vi.fn()} result={result()} />);
    const line = screen.getByRole("region", { name: "Review provenance" });
    expect(line).toHaveTextContent(/Approved release guidelines-2026-08, activated \w+ \d+, \d{4}/);
    expect(screen.getByText("a".repeat(64))).not.toBeVisible();
  });

  it("keeps identifiers and the manifest behind Details", async () => {
    const user = userEvent.setup();
    render(<ProvenanceLine exports={[]} onCopyLink={vi.fn()} result={result()} />);
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("a".repeat(64))).toBeVisible();
    expect(screen.getByText("Q_0123456789abcdef0123456789")).toBeVisible();
  });

  it("does not call a research release approved, and reports no activation instant", async () => {
    const user = userEvent.setup();
    render(
      <ProvenanceLine
        exports={[]}
        onCopyLink={vi.fn()}
        result={result({
          corpus_release: {
            corpus_release_id: "guidelines-2026-09-rc",
            manifest_sha256: "b".repeat(64),
            qdrant_collection: "guidelines",
            activated_at: null,
            serving_mode: "RESEARCH_UNACTIVATED",
          },
        })}
      />,
    );
    expect(screen.getByRole("region", { name: "Review provenance" })).toHaveTextContent(
      "Research release guidelines-2026-09-rc, validated but not activated",
    );
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Not activated: no signed activation decision")).toBeVisible();
  });

  it("names the absence of any release", () => {
    render(
      <ProvenanceLine exports={[]} onCopyLink={vi.fn()} result={result({ corpus_release: null })} />,
    );
    expect(screen.getByText("No approved release")).toBeInTheDocument();
  });

  it("offers the link and every export from the line", async () => {
    const user = userEvent.setup();
    const onCopyLink = vi.fn();
    const audit = vi.fn();
    const ris = vi.fn();
    render(
      <ProvenanceLine
        exports={[
          { label: "References (RIS)", run: ris },
          { label: "Audit record (JSON)", run: audit },
        ]}
        onCopyLink={onCopyLink}
        result={result()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Copy link" }));
    await user.click(screen.getByText("Export"));
    await user.click(screen.getByRole("button", { name: "References (RIS)" }));
    expect(onCopyLink).toHaveBeenCalledTimes(1);
    expect(ris).toHaveBeenCalledTimes(1);
    expect(audit).not.toHaveBeenCalled();
  });
});
