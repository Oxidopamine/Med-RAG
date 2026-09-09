import { describe, expect, it } from "vitest";

import { buildCitations } from "./evidence-presentation";
import { toBibTeX, toRis } from "./export";
import type { QuestionResult } from "./types";

function result(): QuestionResult {
  return {
    question_id: "Q_abc123def456",
    question: "How often should viral load be monitored?",
    status: "ANSWER_READY",
    corpus_release: null,
    interpreted_context: null,
    claims: [
      {
        claim_id: "CL_1",
        text: "Measure viral load at six and twelve months.",
        evidence_ids: ["EV_1"],
        verification_status: "SUPPORTED",
      },
    ],
    evidence_details: [
      {
        evidence_id: "EV_1",
        exact_text: "Viral load should be measured at six months.",
        evidence_roles: ["PRIMARY_SUPPORT"],
        source_id: "SRC_1",
        source_version_id: "SV_2021",
        source_title: "Consolidated guidelines on HIV",
        source_version_label: "2021 edition",
        publisher_name: "World Health Organization",
        source_url: "https://example.test/guideline",
        source_class: "E1",
        jurisdiction: "WORLD",
        language: "en",
        lifecycle_status: "CURRENT",
        render_allowed: true,
        approval_status: "APPROVED",
        evidence_type: null,
        section_path: [],
        effective_from: "2021-07-16",
        effective_to: null,
        highlights: [],
        locators: [
          {
            kind: "PDF_PAGE",
            source_uri: "https://example.test/guideline.pdf",
            pdf_page: 47,
            printed_page: "231",
            bbox: null,
            exact_highlight_available: true,
          },
        ],
      },
    ],
    retrieval_candidates: [],
    conflicts: [],
    verification_summary: { rendered_claims: 1, supported_claims: 1, withheld_claims: 0 },
    abstention: null,
    created_at: "2026-09-09T10:00:00Z",
    updated_at: "2026-09-09T10:00:10Z",
  } as unknown as QuestionResult;
}

describe("reference exports", () => {
  it("writes one RIS record per cited passage with the page and the review id", () => {
    const value = result();
    const ris = toRis(buildCitations(value), value);
    expect(ris).toContain("TY  - RPRT");
    expect(ris).toContain("TI  - Consolidated guidelines on HIV");
    expect(ris).toContain("PY  - 2021");
    expect(ris).toContain("UR  - https://example.test/guideline");
    expect(ris).toContain("Cited by review Q_abc123def456");
    expect(ris).toContain("ER  - ");
    // The passage text never leaves with the reference.
    expect(ris).not.toContain("Viral load should be measured");
  });

  it("writes BibTeX with a stable key per reference number", () => {
    const value = result();
    const bib = toBibTeX(buildCitations(value), value);
    expect(bib).toMatch(/^@techreport\{[A-Za-z0-9]+-1,/);
    expect(bib).toContain("title = {Consolidated guidelines on HIV}");
    expect(bib).toContain("year = {2021}");
    expect(bib).not.toContain("Viral load should be measured");
  });
});
