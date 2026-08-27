import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import {
  licensedWhoEvidence,
  priorEditionTwinEvidence,
  restrictedWhoEvidence,
  tableCellEvidence,
  unaddressedTableCellEvidence,
} from "@/lib/fixtures/answer-lane";

import { AnchorViewer } from "./anchor-viewer";

afterEach(cleanup);

/** The drawn region, which is positioned from the recorded box and nothing else. */
function region(): HTMLElement | null {
  return document.querySelector("figure span[style]");
}

describe("AnchorViewer, restricted sources", () => {
  it("places the recorded region without the source content behind it", () => {
    render(<AnchorViewer detail={restrictedWhoEvidence()} />);

    const marked = region();
    expect(marked).not.toBeNull();
    expect(marked!.style.left).toBe("12%");
    expect(marked!.style.top).toBe("31%");
    // 0.88 - 0.12 and 0.44 - 0.31, as fractions of the page box.
    expect(marked!.style.width).toBe("76%");
    expect(marked!.style.height).toBe("13%");
    expect(screen.getByText("Anchored region")).toBeInTheDocument();
  });

  it("reports the coordinates and provenance the licence does not withhold", () => {
    render(<AnchorViewer detail={restrictedWhoEvidence()} />);

    expect(screen.getByText("19")).toBeInTheDocument();
    expect(screen.getByText("11")).toBeInTheDocument();
    expect(
      screen.getByText("12.0%, 31.0% to 88.0%, 44.0% of the page"),
    ).toBeInTheDocument();
    expect(screen.getByText("source://SV_WHO_HTN_2021/page/19")).toBeInTheDocument();
  });

  it("says the region is placed and the content is not reproduced", () => {
    render(<AnchorViewer detail={restrictedWhoEvidence()} />);

    expect(
      screen.getByText(/withheld under licence and is not reproduced here/),
    ).toBeInTheDocument();
    expect(document.querySelector("mark")).toBeNull();
  });

  it("does not blame the licence when the release simply carried no text", () => {
    render(<AnchorViewer detail={licensedWhoEvidence({ exact_text: null })} />);

    expect(screen.getByText(/carried no text for that location/)).toBeInTheDocument();
    expect(
      screen.queryByText(/withheld under licence/),
    ).not.toBeInTheDocument();
  });

  it("marks the region as the passage's own once the licence permits it", () => {
    render(<AnchorViewer detail={licensedWhoEvidence()} />);

    expect(screen.getByText("Passage region")).toBeInTheDocument();
    expect(screen.queryByText("Anchored region")).not.toBeInTheDocument();
    // The passage is rendered once, full size, elsewhere. The region locates it.
    expect(document.querySelector("mark")).toBeNull();
  });
});

describe("AnchorViewer, source version identity", () => {
  it("names the edition the coordinates were measured against", () => {
    render(<AnchorViewer detail={restrictedWhoEvidence()} />);

    expect(screen.getByText("SV_WHO_HTN_2021")).toBeInTheDocument();
    expect(screen.getByText(/World Health Organization · 2021/)).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
  });

  it("separates two editions that carry the same page and near-identical text", () => {
    const { unmount } = render(<AnchorViewer detail={restrictedWhoEvidence()} />);
    const current = screen
      .getByRole("img")
      .getAttribute("aria-label");
    unmount();

    render(<AnchorViewer detail={priorEditionTwinEvidence()} />);
    const prior = screen.getByRole("img").getAttribute("aria-label");

    // Same printed page, same PDF page, boxes a hair apart - only the edition tells
    // them apart, so the accessible name of the figure has to carry it.
    expect(current).toContain("Printed page 11 (PDF page 19)");
    expect(prior).toContain("Printed page 11 (PDF page 19)");
    expect(current).toContain("SV_WHO_HTN_2021");
    expect(prior).toContain("SV_WHO_HTN_2013");
    expect(current).not.toEqual(prior);
  });

  it("marks a superseded edition as no longer current", () => {
    render(<AnchorViewer detail={priorEditionTwinEvidence()} />);

    expect(screen.getByText("Superseded")).toBeInTheDocument();
    expect(screen.getByText("May 1, 2013–Aug 23, 2021")).toBeInTheDocument();
  });
});

describe("AnchorViewer, table cells", () => {
  it("marks the addressed cell using the document's own one-based reference", () => {
    render(<AnchorViewer detail={tableCellEvidence()} />);

    // Recorded zero-based as row 3, column 2; the source document calls that C4.
    expect(screen.getByText("Annex2Dosing!C4")).toBeInTheDocument();
    expect(screen.getByText("3, 2 (zero-based)")).toBeInTheDocument();
    expect(
      screen.getByText("Cell C4 of table Annex2Dosing in 2021"),
    ).toBeInTheDocument();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain(
      "Table Annex2Dosing, cell C4",
    );
  });

  it("draws a grid window around the cell rather than starting at the first column", () => {
    render(<AnchorViewer detail={tableCellEvidence()} />);
    const grid = screen.getByRole("img");

    expect(within(grid).getByText("B")).toBeInTheDocument();
    expect(within(grid).getByText("C")).toBeInTheDocument();
    expect(within(grid).getByText("D")).toBeInTheDocument();
    expect(within(grid).queryByText("A")).not.toBeInTheDocument();
    expect(within(grid).getByText("4")).toBeInTheDocument();
  });

  it("reports a cell anchor the release did not address as unaddressed", () => {
    render(<AnchorViewer detail={unaddressedTableCellEvidence()} />);

    expect(screen.getAllByText("Cell address not carried").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/carried no table, row, or column for it/),
    ).toBeInTheDocument();
    // It is reported as an unaddressed cell, never as a document-scope anchor.
    expect(screen.queryByText("Located to the document")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not carried").length).toBeGreaterThan(0);
  });
});

describe("AnchorViewer, coarse and multiple anchors", () => {
  it("states that a document-scope anchor has no place on a page to mark", () => {
    render(
      <AnchorViewer
        detail={restrictedWhoEvidence({
          locators: [
            {
              kind: "SECTION",
              source_uri: "source://SV_WHO_HTN_2021/section/treatment-initiation",
              pdf_page: null,
              printed_page: null,
              bbox: null,
              exact_highlight_available: false,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("Located to the document")).toBeInTheDocument();
    expect(screen.getByText(/no page and no region/)).toBeInTheDocument();
  });

  it("will not place a region recorded in the source document's own units", () => {
    render(
      <AnchorViewer
        detail={restrictedWhoEvidence({
          locators: [
            {
              kind: "PDF",
              source_uri: "source://SV_WHO_HTN_2021/page/19",
              pdf_page: 19,
              printed_page: null,
              // PyMuPDF block coordinates, in points. Nothing in the payload says how
              // large the page is, so there is no honest way to place them.
              bbox: [72, 90.5, 523.2, 210.8],
              exact_highlight_available: false,
            },
          ],
        })}
      />,
    );

    expect(region()).toBeNull();
    expect(
      screen.getByText("Region recorded in source units; no page size to place it against"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("72, 90.50 to 523.20, 210.80 in source units"),
    ).toBeInTheDocument();
  });

  it("steps between the locators one record carries", async () => {
    const user = userEvent.setup();
    render(<AnchorViewer detail={licensedWhoEvidence()} />);

    const switcher = screen.getByRole("group", { name: "Source locations" });
    expect(region()).not.toBeNull();

    await user.click(within(switcher).getByRole("button", { name: "Section" }));

    expect(region()).toBeNull();
    expect(screen.getByText("Located to the document")).toBeInTheDocument();
  });

  it("says so plainly when no location was supplied", () => {
    render(<AnchorViewer detail={restrictedWhoEvidence({ locators: [] })} />);

    expect(
      screen.getByText(
        "No source location was supplied for this passage, so there is nothing to place.",
      ),
    ).toBeInTheDocument();
  });
});
