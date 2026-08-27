import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { answerReadyResult } from "@/lib/fixtures/answer-lane";
import type { QuestionResult } from "@/lib/types";

import { ProvenanceStrip } from "./provenance-strip";

afterEach(cleanup);

/**
 * The provenance strip is the only surface that states where an answer's authority comes
 * from, so the distinction it must never lose is between a release that passed the signed
 * activation gate and one being served for research without it. Both carry a release ID
 * and both produce answers; only one has been accepted for clinical use.
 */

function renderStrip(result: QuestionResult) {
  return render(
    <ProvenanceStrip result={result} onCopyLink={vi.fn()} onExportAudit={vi.fn()} />,
  );
}

function researchResult(): QuestionResult {
  const result = answerReadyResult();
  return {
    ...result,
    corpus_release: {
      ...result.corpus_release!,
      activated_at: null,
      serving_mode: "RESEARCH_UNACTIVATED",
    },
  };
}

describe("ProvenanceStrip", () => {
  it("calls an activated release approved, and dates its activation", () => {
    renderStrip(answerReadyResult());

    expect(screen.getByText("Approved corpus release")).toBeInTheDocument();
    // The summary line specifically, not the details term of the same name.
    expect(screen.getByText(/· activated \w+ \d+, \d{4}/i)).toBeInTheDocument();
  });

  it("does not call a research release approved", () => {
    renderStrip(researchResult());

    expect(screen.queryByText("Approved corpus release")).not.toBeInTheDocument();
    expect(
      screen.getByText("Research serving — not clinically accepted"),
    ).toBeInTheDocument();
  });

  it("says a research release was served without activation", () => {
    renderStrip(researchResult());

    expect(
      screen.getByText(/validated release, served without activation/i),
    ).toBeInTheDocument();
  });

  it("reports no activation instant rather than inventing one", () => {
    // The failure this guards is a formatted date derived from a null, which would read
    // as a real activation to anyone glancing at the strip.
    renderStrip(researchResult());

    expect(
      screen.getByText("Not activated — no signed activation decision"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument();
  });

  it("still identifies the release and its manifest under research serving", () => {
    // Withholding the identifiers would be the opposite failure: an answer nobody can
    // trace. Research serving changes the claim being made about the release, not
    // whether the reader can find it.
    const result = researchResult();
    renderStrip(result);

    expect(
      screen.getAllByText(result.corpus_release!.corpus_release_id).length,
    ).toBeGreaterThan(0);
  });

  it("keeps naming the absence of any release distinct from research serving", () => {
    const result = answerReadyResult();
    renderStrip({ ...result, corpus_release: null });

    expect(screen.getByText("No approved corpus release")).toBeInTheDocument();
    expect(
      screen.queryByText("Research serving — not clinically accepted"),
    ).not.toBeInTheDocument();
  });
});
