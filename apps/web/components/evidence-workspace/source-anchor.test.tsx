import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  licensedWhoEvidence,
  pageBlocksEvidence,
  restrictedWhoEvidence,
  spreadsheetRowEvidence,
} from "@/lib/fixtures/answer-lane";

import { PassageBody, SourceAnchorList } from "./source-anchor";

afterEach(cleanup);

describe("SourceAnchorList", () => {
  it("states how precisely each locator pins the passage", () => {
    render(<SourceAnchorList detail={licensedWhoEvidence()} />);

    const anchors = screen.getByRole("list", { name: "Source locations" });
    expect(anchors).toBeInTheDocument();
    expect(screen.getByText("Exact region")).toBeInTheDocument();
    expect(screen.getByText("Printed page 11 (PDF page 19), exact region")).toBeInTheDocument();
    expect(screen.getByText("Region verified")).toBeInTheDocument();
    // The coarse locator is kept and labelled, not hidden behind the precise one.
    expect(screen.getByText("Document")).toBeInTheDocument();
    expect(screen.getByText("No region recorded")).toBeInTheDocument();
  });

  it("reports a recorded region the licence withholds as withheld, not as absent", () => {
    render(<SourceAnchorList detail={restrictedWhoEvidence()} />);

    expect(screen.getByText("Page")).toBeInTheDocument();
    expect(screen.getByText("Printed page 11 (PDF page 19)")).toBeInTheDocument();
    expect(screen.getByText("Region withheld by licence")).toBeInTheDocument();
  });

  it("says so plainly when no location was supplied", () => {
    render(<SourceAnchorList detail={restrictedWhoEvidence({ locators: [] })} />);

    expect(
      screen.getByText("No source location was supplied for this passage."),
    ).toBeInTheDocument();
  });
});

describe("PassageBody", () => {
  it("quotes the passage only where the licence permits it", () => {
    render(<PassageBody detail={licensedWhoEvidence()} />);

    expect(screen.getByText(/Pharmacological treatment is recommended/)).toBeInTheDocument();
    expect(screen.getByText("Verified source passage")).toBeInTheDocument();
  });

  it("explains a licence restriction without reconstructing the passage", () => {
    render(<PassageBody detail={restrictedWhoEvidence()} />);

    expect(
      screen.getByText("Licence does not permit showing this passage"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Provenance, location, and version were verified. Open the publisher source to read the passage itself.",
      ),
    ).toBeInTheDocument();
  });

  it("does not blame a licence for text the release simply did not carry", () => {
    render(<PassageBody detail={licensedWhoEvidence({ exact_text: null })} />);

    expect(screen.getByText("No passage text was supplied")).toBeInTheDocument();
    expect(
      screen.queryByText("Licence does not permit showing this passage"),
    ).not.toBeInTheDocument();
  });

  it("reproduces nothing when the licence withholds the text", () => {
    render(<PassageBody detail={restrictedWhoEvidence()} />);

    expect(document.querySelector("mark")).toBeNull();
  });
});

describe("PassageBody, a page as paragraphs", () => {
  /*
   * A PDF evidence unit is a whole page, joined from the extractor's text blocks by a
   * blank line. HTML collapses those, so the page used to arrive as one unbroken slab -
   * the structure was in the payload the whole way and was dropped on the last step.
   */
  it("splits a page back into the blocks it was assembled from", () => {
    render(<PassageBody detail={pageBlocksEvidence()} />);

    const blocks = screen.getAllByRole("listitem");
    expect(blocks).toHaveLength(3);
    expect(blocks[0]).toHaveTextContent(/Recommendation 3\./);
    expect(blocks[1]).toHaveTextContent(/repeated office measurements/);
    expect(screen.getByText(/3 blocks on this page/)).toBeInTheDocument();
  });

  it("offers each paragraph a way to point at its own region", async () => {
    const user = userEvent.setup();
    const onSelectBlock = vi.fn();
    render(<PassageBody detail={pageBlocksEvidence()} onSelectBlock={onSelectBlock} />);

    await user.click(screen.getByRole("button", { name: "Locate block 2 on the page" }));

    expect(onSelectBlock).toHaveBeenCalledWith(1);
  });

  it("marks the paragraph the figure is currently pointing at", () => {
    render(<PassageBody activeAnchor={2} detail={pageBlocksEvidence()} onSelectBlock={vi.fn()} />);

    const pressed = screen.getByRole("button", { name: "Locate block 3 on the page", pressed: true });
    expect(pressed).toBeInTheDocument();
  });

  /*
   * Pairing is by position and is claimed only when the counts match. A mispaired block
   * would draw a rectangle around text that is not the text in it - a provenance view
   * pointing confidently at the wrong place.
   */
  it("splits the page but locates nothing when the blocks outnumber the anchors", () => {
    render(
      <PassageBody
        detail={pageBlocksEvidence({
          locators: [
            {
              kind: "PDF",
              source_uri: "source://SV_WHO_HTN_2021/page/19",
              pdf_page: 19,
              printed_page: "11",
              bbox: [0.1, 0.12, 0.9, 0.24],
              exact_highlight_available: true,
            },
          ],
        })}
        onSelectBlock={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Locate block/ })).not.toBeInTheDocument();
  });

  it("leaves a workbook row to the row renderer", () => {
    render(<PassageBody detail={spreadsheetRowEvidence()} />);

    // Its structure is cells, not paragraphs, and it has its own table.
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Locate block/ })).not.toBeInTheDocument();
  });
});
