"use client";

import { FileLock2, Rows3, Unplug } from "lucide-react";
import { useCallback, useState } from "react";

import { getTableRowNeighbourhood } from "@/lib/api";
import { parseSpreadsheetRow } from "@/lib/document-inspection";
import type { AnchorGroup } from "@/lib/document-inspection";
import { renderPolicy } from "@/lib/evidence-presentation";
import type { EvidenceDetail, TableRowNeighbour } from "@/lib/types";

import styles from "./workspace.module.css";

/** How far either side to look. Three is a neighbourhood; ten is a spreadsheet. */
const NEIGHBOUR_RADIUS = 3;

type NeighbourhoodState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; rows: TableRowNeighbour[] }
  | { status: "error"; message: string };

/**
 * The rows this one sits between.
 *
 * A decision table is not decidable one row at a time. The row above opens the condition,
 * the row below carries the exception, and a reader checking a citation against the
 * published table is really checking whether the neighbours change what the cited row
 * means. Every other surface in this workspace stops at the record, which is exactly where
 * that question starts.
 *
 * Fetched when the reader opens it and not before. A run cites several passages and the
 * reader inspects one or two, so requesting a window for every selection would spend most
 * of its requests on rows nobody looked at.
 */
export function TableNeighbourhood({
  detail,
  group,
}: {
  detail: EvidenceDetail;
  group: AnchorGroup;
}) {
  const [state, setState] = useState<NeighbourhoodState>({ status: "idle" });
  const cell = group.cells[0] ?? null;

  const load = useCallback(() => {
    if (cell === null) return;
    setState({ status: "loading" });
    void getTableRowNeighbourhood(
      detail.source_id,
      cell.tableId,
      cell.rowIndex,
      NEIGHBOUR_RADIUS,
    )
      .then((neighbourhood) => setState({ status: "loaded", rows: neighbourhood.rows }))
      .catch((error: unknown) =>
        setState({
          status: "error",
          message:
            error instanceof Error
              ? error.message
              : "The surrounding rows could not be retrieved.",
        }),
      );
  }, [cell, detail.source_id]);

  if (cell === null) return null;

  return (
    <details
      className={styles["neighbourhood"]}
      onToggle={(event) => {
        if (event.currentTarget.open && state.status === "idle") load();
      }}
    >
      <summary>
        <Rows3 size={15} aria-hidden="true" />
        Rows around {cell.columnLetter}
        {cell.rowNumber} in {cell.tableId}
      </summary>

      {state.status === "loading" ? (
        <p className={styles["neighbourhood-note"]}>Reading the surrounding rows…</p>
      ) : null}

      {state.status === "error" ? (
        <p className={styles["neighbourhood-note"]} role="alert">
          <Unplug size={15} aria-hidden="true" />
          {state.message}
          <button type="button" onClick={load}>
            Try again
          </button>
        </p>
      ) : null}

      {state.status === "loaded" ? (
        state.rows.length ? (
          <>
            <ol className={styles["neighbourhood-rows"]}>
              {state.rows.map((neighbour) => (
                <NeighbourRow key={neighbour.evidence.evidence_id} neighbour={neighbour} />
              ))}
            </ol>
            <p className={styles["neighbourhood-note"]}>
              {/* Said plainly, because a window over a table invites the assumption that
                  it is the table. It is the rows of this release that happen to sit near
                  the cited one; a row the release does not carry leaves a gap here and
                  says nothing about whether the publisher's table has one. */}
              Rows {state.rows[0]!.row_number}–{state.rows.at(-1)!.row_number} of{" "}
              {cell.tableId}, as this release carries them. Rows the release does not carry
              are absent from this window.
            </p>
          </>
        ) : (
          <p className={styles["neighbourhood-note"]}>
            This release carries no other rows of {cell.tableId} near this one.
          </p>
        )
      ) : null}
    </details>
  );
}

function NeighbourRow({ neighbour }: { neighbour: TableRowNeighbour }) {
  const { evidence, is_anchor_row: isAnchorRow, row_number: rowNumber } = neighbour;
  const row = parseSpreadsheetRow(evidence);
  const policy = renderPolicy(evidence);

  return (
    <li className={isAnchorRow ? styles["neighbour-anchored"] : undefined}>
      <span className={styles["neighbour-rank"]}>
        {rowNumber}
        {isAnchorRow ? <span className={styles.srOnly}>, the cited row</span> : null}
      </span>
      <div className={styles["neighbour-body"]}>
        {row !== null ? (
          <dl>
            {row.cells.map((item) => (
              <div key={item.columnIndex}>
                <dt>
                  {item.columnLetter}
                  {row.rowNumber}
                </dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
        ) : policy.canQuote && evidence.exact_text !== null ? (
          <p>{evidence.exact_text}</p>
        ) : (
          <p className={styles["neighbour-restricted"]}>
            <FileLock2 size={14} aria-hidden="true" />
            {policy.headline}
          </p>
        )}
        <small>{evidence.evidence_id}</small>
      </div>
    </li>
  );
}
