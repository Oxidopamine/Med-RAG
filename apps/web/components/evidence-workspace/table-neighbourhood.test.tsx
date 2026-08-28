import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { anchorGroups } from "@/lib/document-inspection";
import { spreadsheetRowEvidence } from "@/lib/fixtures/answer-lane";
import type { TableRowNeighbourhood } from "@/lib/types";

import { TableNeighbourhood } from "./table-neighbourhood";

vi.mock("@/lib/api", () => ({ getTableRowNeighbourhood: vi.fn() }));

const CITED = spreadsheetRowEvidence();
const GROUP = anchorGroups(CITED)[0]!;

function neighbour(rowIndex: number, value: string) {
  return {
    row_index: rowIndex,
    row_number: rowIndex + 1,
    is_anchor_row: rowIndex === 145,
    evidence: spreadsheetRowEvidence({
      evidence_id: `EV_ROW_${rowIndex}`,
      exact_text: `A${rowIndex + 1}=HIV.D.DE${rowIndex}\nE${rowIndex + 1}=${value}`,
      locators: [0, 4].map((columnIndex) => ({
        kind: "TABLE_CELL",
        source_uri: "source://SV_WHO_HIV_DAK_2/annex/B",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
        table_id: "HIV.D",
        row_index: rowIndex,
        column_index: columnIndex,
      })),
    }),
  };
}

function window(...rows: ReturnType<typeof neighbour>[]): TableRowNeighbourhood {
  return {
    source_id: "WHO_HIV_DAK_2_ANNEX_B",
    table_id: "HIV.D",
    anchor_row_index: 145,
    radius: 3,
    rows,
  };
}

beforeEach(async () => {
  const { getTableRowNeighbourhood } = await import("@/lib/api");
  vi.mocked(getTableRowNeighbourhood).mockReset();
});

afterEach(cleanup);

async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText(/Rows around A146 in HIV.D/));
}

describe("TableNeighbourhood", () => {
  it("asks for nothing until the reader opens it", async () => {
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    render(<TableNeighbourhood detail={CITED} group={GROUP} />);

    // A run cites several passages and a reader inspects one or two. Requesting a window
    // per selection would spend most of its requests on rows nobody opened.
    expect(getTableRowNeighbourhood).not.toHaveBeenCalled();
  });

  it("reads the rows either side of the cited one", async () => {
    const user = userEvent.setup();
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    vi.mocked(getTableRowNeighbourhood).mockResolvedValue(
      window(
        neighbour(144, "Undetectable"),
        neighbour(145, "Detectable (>= 1000 copies/mL)"),
        neighbour(146, "Result pending"),
      ),
    );

    render(<TableNeighbourhood detail={CITED} group={GROUP} />);
    await open(user);

    expect(getTableRowNeighbourhood).toHaveBeenCalledWith(
      "WHO_HIV_DAK_2_ANNEX_B",
      "HIV.D",
      145,
      3,
    );
    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(3);
    // The row above opens the condition and the row below carries the exception; both are
    // shown as the document has them rather than as `E145=` text.
    expect(within(rows[0]!).getByText("Undetectable")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Result pending")).toBeInTheDocument();
    expect(screen.queryByText(/E145=/)).not.toBeInTheDocument();
  });

  it("keeps the cited row obvious among its neighbours", async () => {
    const user = userEvent.setup();
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    vi.mocked(getTableRowNeighbourhood).mockResolvedValue(
      window(neighbour(144, "Undetectable"), neighbour(145, "Detectable")),
    );

    render(<TableNeighbourhood detail={CITED} group={GROUP} />);
    await open(user);

    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[1]!).getByText(", the cited row")).toBeInTheDocument();
    expect(within(rows[0]!).queryByText(", the cited row")).not.toBeInTheDocument();
  });

  /*
   * A window over a table invites the assumption that it is the table. It is the rows of
   * this release that happen to sit near the cited one, and saying so is the difference
   * between a reader concluding "row 147 does not exist" and "this release has no row
   * 147".
   */
  it("says what the window is, so it cannot be read as the publisher's table", async () => {
    const user = userEvent.setup();
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    vi.mocked(getTableRowNeighbourhood).mockResolvedValue(
      window(neighbour(144, "Undetectable"), neighbour(146, "Result pending")),
    );

    render(<TableNeighbourhood detail={CITED} group={GROUP} />);
    await open(user);

    expect(
      await screen.findByText(/Rows 145–147 of HIV.D, as this release carries them/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Rows the release does not carry are absent/)).toBeInTheDocument();
  });

  it("says plainly when the release carries no neighbours", async () => {
    const user = userEvent.setup();
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    vi.mocked(getTableRowNeighbourhood).mockResolvedValue(window());

    render(<TableNeighbourhood detail={CITED} group={GROUP} />);
    await open(user);

    expect(
      await screen.findByText("This release carries no other rows of HIV.D near this one."),
    ).toBeInTheDocument();
  });

  it("reports a failed lookup as a fault and offers a retry", async () => {
    const user = userEvent.setup();
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    vi.mocked(getTableRowNeighbourhood).mockRejectedValueOnce(
      new Error("Request failed with status 503"),
    );

    render(<TableNeighbourhood detail={CITED} group={GROUP} />);
    await open(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Request failed with status 503",
    );

    vi.mocked(getTableRowNeighbourhood).mockResolvedValue(
      window(neighbour(145, "Detectable")),
    );
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findAllByRole("listitem")).toHaveLength(1);
  });

  it("shows a neighbour's address when its licence withholds the text", async () => {
    const user = userEvent.setup();
    const { getTableRowNeighbourhood } = await import("@/lib/api");
    const restricted = neighbour(144, "Undetectable");
    restricted.evidence = spreadsheetRowEvidence({
      evidence_id: "EV_ROW_RESTRICTED",
      exact_text: null,
      render_allowed: false,
    });
    vi.mocked(getTableRowNeighbourhood).mockResolvedValue(restricted && window(restricted));

    render(<TableNeighbourhood detail={CITED} group={GROUP} />);
    await open(user);

    // The window performs no licence act the answer did not: a source that may not be
    // excerpted yields rows with addresses and no text.
    expect(
      await screen.findByText("Licence does not permit showing this passage"),
    ).toBeInTheDocument();
    expect(screen.getByText("145")).toBeInTheDocument();
  });
});
