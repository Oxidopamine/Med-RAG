import { contextRows } from "@/lib/presentation";
import type { ClinicalContext } from "@/lib/types";

import styles from "./workspace.module.css";

/**
 * What the review read from the question, as chips under it: the interpretation sits
 * where the question is, and the way to correct it is beside it.
 */
export function ContextChips({
  allowEditing,
  context,
  onEdit,
}: {
  allowEditing: boolean;
  context: ClinicalContext | null;
  onEdit: () => void;
}) {
  const rows = contextRows(context);
  return (
    <div className={styles["context-chips"]} role="group" aria-label="Interpreted context">
      <span>Read as</span>
      {rows.length ? (
        rows.map((row, index) => (
          <span className={styles["context-chip"]} key={`${row.label}-${row.value}-${index}`}>
            {row.label.toLowerCase()} {row.value}
            {row.inferred ? <small>inferred</small> : null}
          </span>
        ))
      ) : (
        <span className={styles["context-chip"]}>no clinical facts</span>
      )}
      {context && allowEditing ? (
        <button onClick={onEdit} type="button">
          Edit context
        </button>
      ) : null}
    </div>
  );
}
