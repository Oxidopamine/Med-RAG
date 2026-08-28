import { describe, expect, it } from "vitest";

import {
  anchorGroups,
  citationText,
  findMatchCount,
  parseSpreadsheetRow,
  passageBlocks,
  passageSearchText,
  passageSegments,
  questionTerms,
} from "@/lib/document-inspection";
import {
  licensedWhoEvidence,
  pageBlocksEvidence,
  restrictedWhoEvidence,
  spreadsheetRowEvidence,
  tableCellEvidence,
} from "@/lib/fixtures/answer-lane";

describe("parseSpreadsheetRow", () => {
  it("recovers the publisher's row from the extractor's serialisation", () => {
    const row = parseSpreadsheetRow(spreadsheetRowEvidence());

    expect(row).not.toBeNull();
    expect(row!.tableId).toBe("HIV.D");
    // Zero-based in the corpus, one-based in the document. Both are kept so the
    // off-by-one between them can never become invisible.
    expect(row!.rowIndex).toBe(145);
    expect(row!.rowNumber).toBe(146);
    expect(row!.cells.map((cell) => cell.columnLetter)).toEqual(["A", "C", "E", "F"]);
    expect(row!.cells[2]!.value).toBe("Detectable (>= 1000 copies/mL)");
  });

  it("marks the cells this record's own anchors address", () => {
    const row = parseSpreadsheetRow(
      spreadsheetRowEvidence({
        locators: [
          {
            kind: "TABLE_CELL",
            source_uri: "source://SV_WHO_HIV_DAK_2/annex/B",
            pdf_page: null,
            printed_page: null,
            bbox: null,
            exact_highlight_available: false,
            table_id: "HIV.D",
            row_index: 145,
            column_index: 4,
          },
        ],
      }),
    );

    expect(row!.cells.map((cell) => cell.anchored)).toEqual([false, false, true, false]);
  });

  it("keeps a cell value that runs onto its own lines", () => {
    const row = parseSpreadsheetRow(
      spreadsheetRowEvidence({
        exact_text: "A146=First\nsecond line\nC146=Other",
        locators: [0, 2].map((columnIndex) => ({
          kind: "TABLE_CELL",
          source_uri: "source://SV_WHO_HIV_DAK_2/annex/B",
          pdf_page: null,
          printed_page: null,
          bbox: null,
          exact_highlight_available: false,
          table_id: "HIV.D",
          row_index: 145,
          column_index: columnIndex,
        })),
      }),
    );

    expect(row!.cells).toHaveLength(2);
    expect(row!.cells[0]!.value).toBe("First\nsecond line");
  });

  /*
   * A wrong parse would print invented cell addresses beside real values, in a table a
   * clinician is reading to check a dose. Every one of these returns null and falls back
   * to showing the text as it stands.
   */
  it("claims nothing when the record is not a workbook row", () => {
    expect(parseSpreadsheetRow(licensedWhoEvidence())).toBeNull();
  });

  it("claims nothing when a page record's text happens to look like cells", () => {
    // A PDF page whose first line reads `A1=…` is not a row, and the anchors say so.
    expect(
      parseSpreadsheetRow(licensedWhoEvidence({ exact_text: "A1=Table 1\nB1=Dose" })),
    ).toBeNull();
  });

  it("claims nothing when the text and the anchors name different rows", () => {
    expect(
      parseSpreadsheetRow(spreadsheetRowEvidence({ exact_text: "A7=Something else" })),
    ).toBeNull();
  });

  it("claims nothing when the text opens with a line that addresses no cell", () => {
    expect(
      parseSpreadsheetRow(spreadsheetRowEvidence({ exact_text: "loose text\nA146=Value" })),
    ).toBeNull();
  });

  it("claims nothing when the licence withheld the text", () => {
    expect(parseSpreadsheetRow(spreadsheetRowEvidence({ exact_text: null, render_allowed: false })))
      .toBeNull();
  });
});

describe("anchorGroups", () => {
  it("collapses every block of one page into a single place", () => {
    const groups = anchorGroups(
      restrictedWhoEvidence({
        locators: [
          [0.1, 0.1, 0.9, 0.2],
          [0.1, 0.3, 0.9, 0.4],
          [0.1, 0.5, 0.9, 0.6],
        ].map((bbox) => ({
          kind: "PDF",
          source_uri: "source://SV_WHO_HTN_2021/page/19",
          pdf_page: 19,
          printed_page: "11",
          bbox: bbox as [number, number, number, number],
          exact_highlight_available: false,
        })),
      }),
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]!.form).toBe("PAGE");
    expect(groups[0]!.label).toBe("Printed page 11 (PDF page 19)");
    // All three regions travel with the group, so the figure can draw the whole passage.
    expect(groups[0]!.regions).toHaveLength(3);
  });

  it("keeps two different pages apart", () => {
    const groups = anchorGroups(
      restrictedWhoEvidence({
        locators: [19, 20].map((page) => ({
          kind: "PDF",
          source_uri: `source://SV_WHO_HTN_2021/page/${page}`,
          pdf_page: page,
          printed_page: null,
          bbox: [0.1, 0.1, 0.9, 0.2] as [number, number, number, number],
          exact_highlight_available: false,
        })),
      }),
    );

    expect(groups.map((group) => group.label)).toEqual(["PDF page 19", "PDF page 20"]);
  });

  it("collapses the cells of one row and names the row rather than a cell", () => {
    const groups = anchorGroups(spreadsheetRowEvidence());

    expect(groups).toHaveLength(1);
    expect(groups[0]!.form).toBe("TABLE_ROW");
    expect(groups[0]!.label).toBe("Table HIV.D, row 146");
    expect(groups[0]!.cells.map((cell) => cell.columnLetter)).toEqual(["A", "C", "E", "F"]);
  });

  it("still names a single cell as a cell", () => {
    expect(anchorGroups(tableCellEvidence())[0]!.label).toBe(
      "Table Annex2Dosing, cell C4",
    );
  });

  it("keeps a section anchor apart from the page it is not", () => {
    const groups = anchorGroups(licensedWhoEvidence());

    expect(groups.map((group) => group.form)).toEqual(["PAGE", "DOCUMENT"]);
    // Most precise first, which is what makes the first group the one to open on.
    expect(groups[0]!.label).toBe("Printed page 11 (PDF page 19)");
  });
});

describe("passageBlocks", () => {
  it("splits a page into the blocks the extractor joined", () => {
    const blocks = passageBlocks(pageBlocksEvidence());

    expect(blocks).toHaveLength(3);
    expect(blocks![1]!.text).toContain("repeated office measurements");
    // Anchor 1 is block 1: the extractor writes them in lockstep, in reading order.
    expect(blocks!.map((block) => block.anchorIndex)).toEqual([0, 1, 2]);
  });

  /*
   * Pairing is by position, so it is claimed only when the counts match exactly. A
   * mispaired block would draw a rectangle around text that is not the text in it, which
   * is a provenance view pointing confidently at the wrong place.
   */
  it("pairs nothing when the blocks and the anchors disagree in number", () => {
    const blocks = passageBlocks(
      pageBlocksEvidence({
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
      }),
    );

    expect(blocks).toHaveLength(3);
    expect(blocks!.every((block) => block.anchorIndex === null)).toBe(true);
  });

  it("has nothing to split for a workbook row or a withheld passage", () => {
    expect(passageBlocks(spreadsheetRowEvidence())).toBeNull();
    expect(passageBlocks(restrictedWhoEvidence())).toBeNull();
  });

  it("leaves a single-block passage unpaired, since one block pairs with anything", () => {
    // `licensedWhoEvidence` is one sentence against one page anchor. Counts match, but a
    // count of one is not evidence that the two are in step.
    expect(passageBlocks(licensedWhoEvidence())).toMatchObject([{ anchorIndex: null }]);
  });
});

describe("anchorGroups, regions carry their anchor", () => {
  it("tags each region with the anchor it came from", () => {
    const group = anchorGroups(pageBlocksEvidence())[0]!;

    expect(group.regions.map((entry) => entry.anchorIndex)).toEqual([0, 1, 2]);
  });
});

describe("questionTerms", () => {
  it("takes the words worth pointing at and drops the ones that are not", () => {
    const terms = questionTerms(
      "What should the viral load monitoring interval be for an adult on ART?",
    );

    expect(terms).toContain("viral");
    expect(terms).toContain("art");
    expect(terms).not.toContain("what");
    expect(terms).not.toContain("the");
    // Two-letter tokens are noise at this density.
    expect(terms).not.toContain("be");
  });

  it("reduces a term to the stem its inflections share", () => {
    // The question asks about monitoring and the guideline says monitored. Keeping the
    // asked-for form would be a feature that fails silently on the ordinary case.
    expect(questionTerms("viral load monitoring")).toContain("monitor");
    expect(questionTerms("Viral load was monitored")).toContain("monitor");
    // Not a stemmer: a stem this short would match words it has nothing to do with, so
    // the word is kept whole instead.
    expect(questionTerms("the patient was dosed")).toContain("dosed");
  });

  it("reads the claim as well as the question, without repeating a term", () => {
    const terms = questionTerms("viral load monitoring", "Viral load should be monitored");

    expect(terms.filter((term) => term === "viral")).toHaveLength(1);
    expect(terms.filter((term) => term === "monitor")).toHaveLength(1);
  });

  it("ignores sources that are absent", () => {
    expect(questionTerms(null, undefined, "")).toEqual([]);
  });
});

describe("passageSegments", () => {
  it("returns the text untouched when there is nothing to mark", () => {
    expect(passageSegments("Repeat the viral load test.")).toEqual([
      { text: "Repeat the viral load test.", kind: "plain", matchIndex: null },
    ]);
  });

  it("marks a term through its ordinary inflections", () => {
    const segments = passageSegments("Monitoring, monitored, and monitors", {
      terms: ["monitor"],
    });

    expect(segments.filter((segment) => segment.kind === "term").map((s) => s.text)).toEqual([
      "Monitoring",
      "monitored",
      "monitors",
    ]);
  });

  it("marks question terms on whole words only", () => {
    const segments = passageSegments("Viral load and antiviral therapy", {
      terms: ["viral"],
    });

    expect(segments.filter((segment) => segment.kind === "term")).toEqual([
      { text: "Viral", kind: "term", matchIndex: null },
    ]);
    // `antiviral` contains the term and is not a match for it.
    expect(segments.map((segment) => segment.text).join("")).toBe(
      "Viral load and antiviral therapy",
    );
  });

  it("numbers the reader's own matches in reading order", () => {
    const segments = passageSegments("load, then load, then load", { find: "load" });

    expect(segments.filter((segment) => segment.kind === "find").map((s) => s.matchIndex)).toEqual(
      [0, 1, 2],
    );
    expect(findMatchCount(segments)).toBe(3);
  });

  /*
   * The reader looking for something specific has said what they care about, and that
   * outranks what the system inferred they might.
   */
  it("gives the reader's search precedence over an inferred term", () => {
    const segments = passageSegments("viral load", { terms: ["viral"], find: "viral" });

    expect(segments[0]).toEqual({ text: "viral", kind: "find", matchIndex: 0 });
    expect(segments.some((segment) => segment.kind === "term")).toBe(false);
  });

  it("never drops or duplicates a character of the passage", () => {
    const text = "Viral load monitoring: repeat viral load after counselling.";
    const segments = passageSegments(text, {
      terms: ["viral", "load", "counselling"],
      find: "repeat",
    });

    expect(segments.map((segment) => segment.text).join("")).toBe(text);
  });

  it("ignores a search too short to mean anything", () => {
    expect(findMatchCount(passageSegments("a a a a", { find: "a" }))).toBe(0);
  });
});

describe("passageSearchText", () => {
  /*
   * A workbook row is shown as values without their addresses, so counting matches over
   * the serialisation would report hits in text the reader cannot see - and `146` typed
   * into the search would appear to match every cell of the row.
   */
  it("searches the cell values a workbook row actually shows", () => {
    const text = passageSearchText(spreadsheetRowEvidence());

    expect(text).not.toContain("A146=");
    expect(text).toContain("Detectable (>= 1000 copies/mL)");
    expect(findMatchCount(passageSegments(text, { find: "146" }))).toBe(0);
  });

  it("searches the passage itself for everything else", () => {
    expect(passageSearchText(licensedWhoEvidence())).toBe(
      licensedWhoEvidence().exact_text,
    );
  });

  it("has nothing to search when the licence withholds the text", () => {
    expect(passageSearchText(restrictedWhoEvidence())).toBe("");
  });
});

describe("citationText", () => {
  it("carries the edition, the location and the research-use notice with the quote", () => {
    const quote = citationText(licensedWhoEvidence(), "Printed page 11 (PDF page 19)");

    expect(quote).toContain("Pharmacological treatment is recommended");
    expect(quote).toContain("World Health Organization.");
    // The label collides across editions; the identifier is what settles which one.
    expect(quote).toContain("2021 (SV_WHO_HTN_2021)");
    expect(quote).toContain("Printed page 11 (PDF page 19)");
    expect(quote).toContain("https://iris.who.int/handle/10665/344424");
    expect(quote).toContain("Research use only");
  });

  it("offers nothing to copy when the licence withholds the passage", () => {
    expect(citationText(restrictedWhoEvidence(), "Printed page 11")).toBeNull();
  });
});
