import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SourcePageResult } from "@/lib/api";
import {
  licensedWhoEvidence,
  pageBlocksEvidence,
  priorEditionTwinEvidence,
  restrictedWhoEvidence,
  tableCellEvidence,
  unaddressedTableCellEvidence,
} from "@/lib/fixtures/answer-lane";

import { AnchorViewer } from "./anchor-viewer";

// The viewer asks for a page image whenever there is a page to ask about. Mocked here so
// the component's own behaviour is what is under test rather than the network, and so the
// default answer is the one the API gives today: no page.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getSourcePageImage: vi.fn(),
}));

const UNAVAILABLE: SourcePageResult = { status: "unavailable" };

/** A loaded page image, for the tests that need the frame to stay drawn deterministically. */
function loadedPageResult(
  overrides: Partial<{ pageWidth: number; pageHeight: number; pageCount: number }> = {},
): SourcePageResult {
  return {
    status: "loaded",
    image: {
      objectUrl: "blob:page-frame",
      pageWidth: 600,
      pageHeight: 800,
      pageCount: 30,
      pageLabel: null,
      dpi: 144,
      ...overrides,
    },
  } as SourcePageResult;
}

beforeEach(async () => {
  const { getSourcePageImage } = await import("@/lib/api");
  vi.mocked(getSourcePageImage).mockReset().mockResolvedValue(UNAVAILABLE);
});

afterEach(cleanup);

/** The drawn regions, which are positioned from the recorded boxes and nothing else. */
function regions(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>("figure span[style*='left']")];
}

function region(): HTMLElement | null {
  return regions()[0] ?? null;
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
      screen.getByText(/withheld under licence/),
    ).toBeInTheDocument();
    expect(document.querySelector("mark")).toBeNull();
  });

  it("does not blame the licence when the release simply carried no text", () => {
    render(<AnchorViewer detail={licensedWhoEvidence({ exact_text: null })} />);

    expect(screen.getByText(/carried no text for this location/)).toBeInTheDocument();
    expect(screen.queryByText(/withheld under licence/)).not.toBeInTheDocument();
  });

  it("marks the region as the passage's own once the licence permits it", () => {
    render(<AnchorViewer detail={licensedWhoEvidence()} />);

    expect(screen.getByText("Passage region")).toBeInTheDocument();
    expect(screen.queryByText("Anchored region")).not.toBeInTheDocument();
    // The passage is rendered once, full size, elsewhere. The region locates it.
    expect(document.querySelector("mark")).toBeNull();
  });
});

/*
 * The edition block (title, version id, lifecycle, in force) moved out of this viewer: it
 * is stated once in the reader above the figure and again in its details disclosure, so
 * the two tests that asserted on it here were testing content this component no longer
 * renders. What survives is that the edition is still named - inside the figure's own
 * accessible name, which the switcher test below covers.
 */
describe("AnchorViewer, source version identity", () => {
  it("separates two editions that carry the same page and near-identical text", () => {
    const { unmount } = render(<AnchorViewer detail={restrictedWhoEvidence()} />);
    const current = screen.getByRole("img").getAttribute("aria-label");
    unmount();

    render(<AnchorViewer detail={priorEditionTwinEvidence()} />);
    const prior = screen.getByRole("img").getAttribute("aria-label");

    // Same printed page, same PDF page, boxes a hair apart - only the edition tells them
    // apart, so the accessible name of the figure has to carry it.
    expect(current).toContain("Printed page 11 (PDF page 19)");
    expect(prior).toContain("Printed page 11 (PDF page 19)");
    expect(current).toContain("SV_WHO_HTN_2021");
    expect(prior).toContain("SV_WHO_HTN_2013");
    expect(current).not.toEqual(prior);
  });
});

describe("AnchorViewer, table cells", () => {
  it("marks the addressed cell using the document's own one-based reference", () => {
    render(<AnchorViewer detail={tableCellEvidence()} />);

    // Recorded zero-based as row 3, column 2; the source document calls that C4.
    expect(screen.getByText("Annex2Dosing!C4")).toBeInTheDocument();
    expect(screen.getByText("3, 2 (zero-based)")).toBeInTheDocument();
    expect(screen.getByText("Cell C4 of table Annex2Dosing in 2021")).toBeInTheDocument();
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
    expect(screen.getByText(/carried no table, row, or column for it/)).toBeInTheDocument();
    // It is reported as an unaddressed cell, never as a document-scope anchor.
    expect(screen.queryByText("Located to the document")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not carried").length).toBeGreaterThan(0);
  });

  /**
   * The whole row is one evidence unit, and the extractor anchors every populated cell of
   * it. Before grouping, that produced one interchangeable-looking switcher entry per
   * cell and marked only whichever had been clicked.
   */
  it("marks every anchored cell of one row, as one location", () => {
    render(
      <AnchorViewer
        detail={tableCellEvidence({
          locators: [2, 4, 5].map((columnIndex) => ({
            kind: "TABLE_CELL",
            source_uri: "source://SV_WHO_HTN_2021/annex/2",
            pdf_page: null,
            printed_page: null,
            bbox: null,
            exact_highlight_available: false,
            table_id: "Annex2Dosing",
            row_index: 3,
            column_index: columnIndex,
          })),
        })}
      />,
    );

    // One row, one entry: no switcher at all, because there is only one place.
    expect(screen.queryByRole("group", { name: "Source locations" })).not.toBeInTheDocument();
    expect(screen.getByText("C4, E4, F4")).toBeInTheDocument();
    expect(
      screen.getByText("Cells C4, E4, F4 of table Annex2Dosing in 2021"),
    ).toBeInTheDocument();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain(
      "3 addressed cells are marked on the grid",
    );
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
    // Not "you are not on the cited page": a section anchor names no page to be off.
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain(
      "There is no page and no region, so nothing is marked.",
    );
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
    expect(screen.getByText("72, 90.50 to 523.20, 210.80 in source units")).toBeInTheDocument();
  });

  it("steps between the places one record carries", async () => {
    const user = userEvent.setup();
    render(<AnchorViewer detail={licensedWhoEvidence()} />);

    const switcher = screen.getByRole("group", { name: "Source locations" });
    expect(region()).not.toBeNull();

    await user.click(within(switcher).getByRole("button", { name: "Section" }));

    expect(region()).toBeNull();
    expect(screen.getByText("Located to the document")).toBeInTheDocument();
  });

  /**
   * A PDF evidence unit is a whole page and carries one anchor per text block, all of them
   * labelled with the same page. Thirty identical switcher buttons described the pipeline
   * rather than the document, and marking one block understated the passage to whatever
   * fraction of the page that block happens to be.
   */
  it("draws every block of one page at once, under a single entry", () => {
    render(
      <AnchorViewer
        detail={restrictedWhoEvidence({
          locators: [
            [0.1, 0.1, 0.9, 0.2],
            [0.1, 0.25, 0.9, 0.4],
            [0.1, 0.45, 0.5, 0.55],
          ].map((bbox) => ({
            kind: "PDF",
            source_uri: "source://SV_WHO_HTN_2021/page/19",
            pdf_page: 19,
            printed_page: "11",
            bbox: bbox as [number, number, number, number],
            exact_highlight_available: false,
          })),
        })}
      />,
    );

    expect(screen.queryByRole("group", { name: "Source locations" })).not.toBeInTheDocument();
    expect(regions()).toHaveLength(3);
    // The tag is drawn once and counts the rest, rather than thirty times over.
    expect(screen.getByText(/Anchored region/)).toBeInTheDocument();
    expect(screen.getByText("3 recorded on this page")).toBeInTheDocument();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain(
      "3 marked regions show where the passage sits",
    );
  });

  it("keeps every recorded readout reachable when there are too many to print", async () => {
    const user = userEvent.setup();
    render(
      <AnchorViewer
        detail={restrictedWhoEvidence({
          locators: [
            [0.1, 0.1, 0.9, 0.2],
            [0.1, 0.25, 0.9, 0.4],
          ].map((bbox) => ({
            kind: "PDF",
            source_uri: "source://SV_WHO_HTN_2021/page/19",
            pdf_page: 19,
            printed_page: "11",
            bbox: bbox as [number, number, number, number],
            exact_highlight_available: false,
          })),
        })}
      />,
    );

    await user.click(screen.getByText("All 2 recorded regions"));

    expect(
      screen.getByText("10.0%, 10.0% to 90.0%, 20.0% of the page"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("10.0%, 25.0% to 90.0%, 40.0% of the page"),
    ).toBeInTheDocument();
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

describe("AnchorViewer, page images", () => {
  /** A bbox as the WHO corpus actually records one: PDF points, not fractions. */
  function pointSpaceEvidence(exactHighlightAvailable: boolean) {
    return licensedWhoEvidence({
      source_id: "WHO_HIV_DAK_2_MAIN",
      locators: [
        {
          kind: "PDF_PAGE",
          source_uri: "source://SV_WHO_HTN_2021/page/14",
          pdf_page: 14,
          printed_page: null,
          bbox: [25.4367, 14.4512, 388.8948, 24.0512],
          exact_highlight_available: exactHighlightAvailable,
        },
      ],
    });
  }

  function loadedPage(overrides: Partial<SourcePageResult & { image: object }> = {}) {
    return {
      status: "loaded",
      image: {
        objectUrl: "blob:page-14",
        pageWidth: 680.315,
        pageHeight: 453.543,
        pageCount: 160,
        pageLabel: null,
        dpi: 144,
        ...(overrides as { image?: object }).image,
      },
    } as SourcePageResult;
  }

  it("asks for the page and shows none when the API withholds it", async () => {
    const { getSourcePageImage } = await import("@/lib/api");

    render(<AnchorViewer detail={pointSpaceEvidence(false)} />);

    // The request is made even though this record carries no exact-highlight permission:
    // whether the *page* may be reproduced is the source licence's answer, and the API is
    // the only thing that reads it. A 404 arrives as `unavailable` and the view degrades.
    expect(getSourcePageImage).toHaveBeenCalledWith("WHO_HIV_DAK_2_MAIN", 14, {
      dpi: 144,
    });
    expect(document.querySelector("img")).toBeNull();
    // The pre-existing view: a point-space box has no page to be placed against.
    expect(
      screen.getByText(/Region recorded in source units; no page size to place it against/),
    ).toBeInTheDocument();
  });

  it("does not ask for a page when the anchor names no page", async () => {
    const { getSourcePageImage } = await import("@/lib/api");

    render(<AnchorViewer detail={tableCellEvidence()} />);

    expect(getSourcePageImage).not.toHaveBeenCalled();
  });

  it("places a point-space region against the page box the image carries", async () => {
    const { getSourcePageImage } = await import("@/lib/api");
    vi.mocked(getSourcePageImage).mockResolvedValue(loadedPage());

    render(<AnchorViewer detail={pointSpaceEvidence(true)} />);

    const image = await screen.findByRole("presentation", { hidden: true });
    expect(image).toHaveAttribute("src", "blob:page-14");

    // 25.4367 / 680.315 and 14.4512 / 453.543, as fractions of the real page box.
    const marked = region()!;
    expect(marked.style.left).toBe("3.739%");
    expect(marked.style.top).toBe("3.1863%");
    expect(marked.style.width).toBe("53.425%");
    expect(marked.style.height).toBe("2.1167%");
  });

  it("keeps the frame usable when no page comes back", async () => {
    const { getSourcePageImage } = await import("@/lib/api");
    vi.mocked(getSourcePageImage).mockResolvedValue(UNAVAILABLE);

    render(<AnchorViewer detail={pointSpaceEvidence(true)} />);

    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("PDF page 14")).toBeInTheDocument();
  });

  /**
   * A licence that withholds a page and a connection that dropped are not the same event
   * to the reader deciding whether to try again, which is why the retry is offered for one
   * and not the other - but neither is a page, so neither draws the frame a page would.
   * Both now read as the one plain line the figure has for "no page rendering", and the
   * fault is the one that also carries the retry.
   */
  it("tells a failed request apart from a withheld one, and offers a retry", async () => {
    const user = userEvent.setup();
    const { getSourcePageImage } = await import("@/lib/api");
    vi.mocked(getSourcePageImage).mockResolvedValue({
      status: "error",
      message: "The source page could not be reached.",
    });

    render(<AnchorViewer detail={pointSpaceEvidence(true)} />);

    expect(
      await screen.findByText(/No page rendering for this source/),
    ).toBeInTheDocument();
    // Not the licence note, which would be a claim about the publisher.
    expect(
      screen.queryByText(/withheld under licence/),
    ).not.toBeInTheDocument();

    vi.mocked(getSourcePageImage).mockResolvedValue(loadedPage());
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("presentation", { hidden: true })).toHaveAttribute(
      "src",
      "blob:page-14",
    );
  });

  it("turns the page, and stops marking a page the record never anchored", async () => {
    const user = userEvent.setup();
    const { getSourcePageImage } = await import("@/lib/api");
    vi.mocked(getSourcePageImage).mockResolvedValue(loadedPage());

    render(<AnchorViewer detail={pointSpaceEvidence(true)} />);
    await screen.findByRole("presentation", { hidden: true });
    expect(regions()).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Next page of the source" }));

    expect(getSourcePageImage).toHaveBeenCalledWith("WHO_HIV_DAK_2_MAIN", 15, {
      dpi: 144,
    });
    // The region belongs to page 14. Carrying it onto page 15 would assert a location the
    // record never recorded.
    expect(regions()).toHaveLength(0);
    expect(await screen.findByText("15 of 160")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back to the cited page" }));
    expect(await screen.findByText("14 of 160")).toBeInTheDocument();
    expect(regions()).toHaveLength(1);
  });

  it("prefers the page label the document prints over the ordinal", async () => {
    const { getSourcePageImage } = await import("@/lib/api");
    vi.mocked(getSourcePageImage).mockResolvedValue(
      loadedPage({ image: { pageLabel: "xiv" } } as never),
    );

    render(<AnchorViewer detail={pointSpaceEvidence(true)} />);

    expect(await screen.findByText("Printed page xiv · PDF page 14")).toBeInTheDocument();
  });
});

describe("AnchorViewer, the page pointing back at the passage", () => {
  // These fixtures place their regions with fractions of the page, which need no loaded
  // image to draw - but the figure itself now only draws once the page has (a withheld or
  // failed page draws no frame at all, only a note). Mocked to a loaded page so the frame
  // - and the regions this describe block is about - are there regardless of how the
  // default `unavailable` response happens to interleave with the interaction below.
  beforeEach(async () => {
    const { getSourcePageImage } = await import("@/lib/api");
    vi.mocked(getSourcePageImage).mockReset().mockResolvedValue(loadedPageResult());
  });

  /*
   * The extractor writes one anchor per text block in reading order, so a rectangle on the
   * page and a paragraph of the passage are the same thing seen twice. Until the regions
   * became controls, that correspondence existed in the payload and nowhere on screen.
   */
  it("makes each region a control when the passage has paragraphs to reach", async () => {
    const user = userEvent.setup();
    const onSelectAnchor = vi.fn();

    render(<AnchorViewer detail={pageBlocksEvidence()} onSelectAnchor={onSelectAnchor} />);

    await user.click(
      await screen.findByRole("button", { name: "Paragraph 2 of 3 on this page" }),
    );

    expect(onSelectAnchor).toHaveBeenCalledWith(1);
  });

  it("marks the region whose paragraph the reader is on", async () => {
    render(
      <AnchorViewer activeAnchor={2} detail={pageBlocksEvidence()} onSelectAnchor={vi.fn()} />,
    );

    expect(
      await screen.findByRole("button", { name: "Paragraph 3 of 3 on this page", pressed: true }),
    ).toBeInTheDocument();
    // The tag rides the live region rather than sitting on the first one regardless.
    expect(screen.getByText(/3 of 3/)).toBeInTheDocument();
  });

  /*
   * A `role="img"` hides everything inside it, so a frame holding controls must not claim
   * to be one - and a frame holding none should still describe itself as a picture.
   */
  it("stays one image when there is nothing to point back at", async () => {
    render(<AnchorViewer detail={pageBlocksEvidence()} />);

    expect(await screen.findByRole("img")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Paragraph 1 of 3/ }),
    ).not.toBeInTheDocument();
  });
});
