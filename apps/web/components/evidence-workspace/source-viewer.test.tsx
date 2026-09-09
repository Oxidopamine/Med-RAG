import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { spreadsheetRowEvidence } from "@/lib/fixtures/answer-lane";
import type { CitationIndex } from "@/lib/evidence-presentation";
import {
  buildCitations,
  locationSummary,
  primaryAnchor,
  renderPolicy,
  shortAttribution,
} from "@/lib/evidence-presentation";
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

/**
 * A citation index over given records, without a whole run to build one from.
 *
 * `buildCitations` needs a `QuestionResult` whose claims cite these records, which is a
 * great deal of fixture to assert that a rail prints `[1]`. The shape is small and public,
 * so it is built directly.
 */
function citationsFor(details: EvidenceDetail[]): CitationIndex {
  const ordered = details.map((detail, index) => ({
    number: index + 1,
    detail,
    shortLabel: shortAttribution(detail),
    locationLabel: locationSummary(detail),
    policy: renderPolicy(detail),
    anchor: primaryAnchor(detail),
  }));
  return {
    byEvidenceId: new Map(ordered.map((citation) => [citation.detail.evidence_id, citation])),
    ordered,
  };
}

function ViewerHarness({
  candidates = CANDIDATES,
  citations = buildCitations(null),
  claimText = null,
  evidence = CITED,
  onPinEvidence,
  onToggleFocus,
  pinnedEvidence = null,
}: {
  candidates?: RankedEvidence[];
  citations?: CitationIndex;
  claimText?: string | null;
  evidence?: EvidenceDetail[];
  onPinEvidence?: (evidenceId: string | null) => void;
  onToggleFocus?: () => void;
  pinnedEvidence?: EvidenceDetail | null;
}) {
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(
    evidence[0]?.evidence_id ?? null,
  );
  return (
    <SourceViewer
      candidates={candidates}
      citations={citations}
      claimText={claimText}
      evidence={evidence}
      onPinEvidence={onPinEvidence}
      onSelectEvidence={setSelectedEvidenceId}
      onToggleFocus={onToggleFocus}
      pinnedEvidence={pinnedEvidence}
      selectedEvidenceId={selectedEvidenceId}
    />
  );
}

/** A passage long enough that finding a word in it is a real navigation problem. */
const PAGE_PASSAGE = detail("evidence-page", "Consolidated guideline");
PAGE_PASSAGE.exact_text =
  "Viral load is the preferred monitoring approach. Repeat the viral load after " +
  "enhanced adherence counselling. A persistently high viral load indicates treatment " +
  "failure.";

describe("SourceViewer", () => {
  it("steps past the last citation into the next ranked passage", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);

    expect(screen.getByText("Record 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("Cited in this answer")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next ranked result" }));

    expect(screen.getByText("Record 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("Passage text for evidence-2.")).toBeInTheDocument();
  });

  it("marks a passage no claim cites so it cannot read as support", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);
    await user.click(screen.getByRole("button", { name: "Next ranked result" }));

    expect(screen.queryByText("Cited in this answer")).not.toBeInTheDocument();
    // "Not cited" is also the rail's own divider label where citation stops, so two
    // instances are expected here; the status badge is the one in document order first.
    expect(screen.getAllByText("Not cited")[0]).toBeInTheDocument();
    expect(screen.getByText("No claim in this answer cites this passage.")).toBeInTheDocument();
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

    expect(screen.getByText("Record 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("Cited in this answer")).toBeInTheDocument();
  });
});

describe("SourceViewer, reading a workbook row", () => {
  /*
   * Most of this corpus is spreadsheet rows, and `content_exact` for one is the
   * extractor's `A146=…` serialisation. Printed verbatim it showed the reader the
   * pipeline instead of the publisher's table.
   */
  it("renders the row as the worksheet has it, not as the extractor wrote it", () => {
    render(<ViewerHarness candidates={[]} evidence={[spreadsheetRowEvidence()]} />);

    expect(screen.queryByText(/A146=/)).not.toBeInTheDocument();

    // Column letters across the top, the row number at the left, values in the cells -
    // the layout the reader will be looking at when they open the workbook to check it.
    const row = screen.getByRole("table");
    expect(within(row).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Row",
      "A",
      "C",
      "E",
      "F",
    ]);
    expect(within(row).getByRole("rowheader")).toHaveTextContent("146");
    expect(within(row).getByRole("cell", { name: /Detectable/ })).toBeInTheDocument();
    expect(
      within(row).getByRole("cell", { name: /Repeat viral load/ }),
    ).toBeInTheDocument();
  });

  it("marks the cells the citation anchors, and says so in words", () => {
    render(<ViewerHarness candidates={[]} evidence={[spreadsheetRowEvidence()]} />);

    // The fixture anchors every populated cell of the row.
    expect(
      screen.getAllByRole("cell", { name: /anchored by this citation/ }),
    ).toHaveLength(4);
  });

  /*
   * The location figure used to draw a three-column grid of empty boxes under a row whose
   * values were already on screen - a picture of a sheet that was not the sheet, adding no
   * location the coordinates beneath it did not already state.
   */
  it("does not draw an empty grid under a row it has already shown", () => {
    render(<ViewerHarness candidates={[]} evidence={[spreadsheetRowEvidence()]} />);

    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(
      screen.getByText(/is shown above in worksheet order/),
    ).toBeInTheDocument();
  });

  it("offers the surrounding rows without fetching them unasked", () => {
    render(<ViewerHarness candidates={[]} evidence={[spreadsheetRowEvidence()]} />);

    // Closed by default: a run cites several passages and the reader inspects one or two,
    // so a window per selection would spend most of its requests on rows nobody opened.
    expect(screen.getByText(/Rows around A146 in HIV.D/)).toBeInTheDocument();
    expect(screen.queryByText(/Reading the surrounding rows/)).not.toBeInTheDocument();
  });
});

describe("SourceViewer, finding a word in a passage", () => {
  it("counts the matches and steps through them", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness candidates={[]} evidence={[PAGE_PASSAGE]} />);

    await user.type(screen.getByRole("searchbox", { name: "Find in passage" }), "viral load");

    expect(screen.getByText("1 of 3")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next match" }));
    expect(screen.getByText("2 of 3")).toBeInTheDocument();

    // Wraps rather than dead-ending, so a reader holding the key keeps moving.
    await user.click(screen.getByRole("button", { name: "Next match" }));
    await user.click(screen.getByRole("button", { name: "Next match" }));
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
  });

  it("says plainly when the passage does not contain it", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness candidates={[]} evidence={[PAGE_PASSAGE]} />);

    await user.type(screen.getByRole("searchbox", { name: "Find in passage" }), "creatinine");

    expect(screen.getByText("No matches")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next match" })).toBeDisabled();
  });

  it("drops the search when the reader moves to another passage", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);

    await user.type(screen.getByRole("searchbox", { name: "Find in passage" }), "passage");
    expect(screen.getByText("1 of 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next ranked result" }));

    // A count carried across would be counting text the reader is no longer looking at.
    expect(screen.queryByText("1 of 1")).not.toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Find in passage" })).toHaveValue("");
  });

  it("marks where the selected claim and the passage meet, without being asked", () => {
    render(
      <ViewerHarness
        candidates={[]}
        claimText="How often should viral load be monitored?"
        evidence={[PAGE_PASSAGE]}
      />,
    );

    const marked = [...document.querySelectorAll("b")].map((node) => node.textContent);
    expect(marked).toContain("Viral");
    expect(marked).toContain("monitoring");
    // Stopwords would light up most of the page and point at nothing.
    expect(marked).not.toContain("the");
  });
});

describe("SourceViewer, taking a quote away", () => {
  it("copies the passage with the edition, the location and the notice", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);

    await user.click(screen.getByRole("button", { name: /Copy quote/ }));

    const copied = await navigator.clipboard.readText();
    expect(copied).toContain("Passage text for evidence-1.");
    expect(copied).toContain("2026 (source-version-1)");
    expect(copied).toContain("https://guidelines.example/atrial-fibrillation");
    expect(copied).toContain("Research use only");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("offers nothing to copy when the licence withholds the passage", () => {
    const restricted = detail("evidence-restricted", "Restricted guideline");
    restricted.exact_text = null;
    restricted.render_allowed = false;

    render(<ViewerHarness candidates={[]} evidence={[restricted]} />);

    expect(screen.queryByRole("button", { name: /Copy quote/ })).not.toBeInTheDocument();
  });
});

describe("SourceViewer, searching every retrieved record", () => {
  /*
   * The reader's question is nearly always "which of these mentions X", and answering it
   * by opening each passage in turn is the manual version of a search the client already
   * has the text for.
   */
  it("counts the search across the rail, not only in the open passage", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);

    await user.type(screen.getByRole("searchbox", { name: "Find in passage" }), "evidence-2");

    // The open record does not contain it; the uncited one below does.
    expect(screen.getByRole("button", { name: /1 of 2 records/ })).toBeInTheDocument();
    const rows = screen.getAllByRole("button", { name: /^Record / });
    expect(rows[0]).toHaveTextContent("0");
    expect(rows[1]).toHaveTextContent("1");
  });

  it("narrows the rail to the records that matched, keeping the open one", async () => {
    const user = userEvent.setup();
    render(<ViewerHarness />);

    await user.type(screen.getByRole("searchbox", { name: "Find in passage" }), "evidence-2");
    await user.click(screen.getByRole("button", { name: /1 of 2 records/ }));

    // Two rows: the one that matched, and the one the reader is reading - which is never
    // filtered out from under them.
    expect(screen.getAllByRole("button", { name: /^Record / })).toHaveLength(2);
  });
});

describe("SourceViewer, the rail as the only ranking", () => {
  it("prints the reference number the claims above cite", () => {
    render(<ViewerHarness candidates={[]} citations={citationsFor(CITED)} />);

    const row = screen.getByRole("button", { name: /^Record 1/ });
    expect(row).toHaveTextContent("[1]");
    expect(row).toHaveAccessibleName(/reference 1/);
  });

  /*
   * An edition no longer in force is the one fact worth knowing before opening a passage,
   * and it used to be discoverable only after opening it.
   */
  it("marks an edition that is no longer in force", () => {
    const superseded = detail("evidence-old", "Superseded guideline");
    superseded.lifecycle_status = "SUPERSEDED";

    render(<ViewerHarness candidates={[]} evidence={[superseded]} />);

    expect(screen.getByRole("button", { name: /^Record 1/ })).toHaveAccessibleName(
      /superseded edition/,
    );
  });
});

describe("SourceViewer, comparing two passages", () => {
  it("pins the open record when asked to compare", async () => {
    const user = userEvent.setup();
    const onPinEvidence = vi.fn();
    render(<ViewerHarness onPinEvidence={onPinEvidence} />);

    await user.click(screen.getByRole("button", { name: /Compare/ }));

    expect(onPinEvidence).toHaveBeenCalledWith("evidence-1");
  });

  it("shows both passages once a second record is pinned", () => {
    const other = detail("evidence-2", "Uncited guideline");
    render(
      <ViewerHarness candidates={[]} onPinEvidence={vi.fn()} pinnedEvidence={other} />,
    );

    expect(screen.getByText("Passage text for evidence-1.")).toBeInTheDocument();
    expect(screen.getByText("Passage text for evidence-2.")).toBeInTheDocument();
    // The comparison is about the two clauses, so neither side carries the page figure,
    // the neighbourhood or the colophon.
    expect(screen.queryByText("Location in source")).not.toBeInTheDocument();
  });

  it("stops comparing without changing which record is open", async () => {
    const user = userEvent.setup();
    const onPinEvidence = vi.fn();
    const other = detail("evidence-2", "Uncited guideline");
    render(
      <ViewerHarness candidates={[]} onPinEvidence={onPinEvidence} pinnedEvidence={other} />,
    );

    // The pinned sheet's own control, distinct from the toolbar's "Stop comparing".
    await user.click(screen.getByRole("button", { name: "Unpin" }));
    expect(onPinEvidence).toHaveBeenCalledWith(null);

    await user.click(screen.getByRole("button", { name: "Stop comparing" }));
    expect(onPinEvidence).toHaveBeenCalledTimes(2);
  });
});

describe("SourceViewer, focus mode", () => {
  it("asks for the full width", async () => {
    const user = userEvent.setup();
    const onToggleFocus = vi.fn();
    render(<ViewerHarness onToggleFocus={onToggleFocus} />);

    await user.click(
      screen.getByRole("button", { name: "Give the inspector the full width" }),
    );

    expect(onToggleFocus).toHaveBeenCalled();
  });
});
