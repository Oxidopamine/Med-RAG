import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { licensedWhoEvidence, restrictedWhoEvidence } from "@/lib/fixtures/answer-lane";

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
    expect(screen.getByText(/World Health Organization/)).toBeInTheDocument();
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

  it("applies the same licence decision in the document view", () => {
    render(<PassageBody detail={restrictedWhoEvidence()} variant="document" />);

    expect(
      screen.getByText("Licence does not permit showing this passage"),
    ).toBeInTheDocument();
    expect(document.querySelector("mark")).toBeNull();
  });
});
