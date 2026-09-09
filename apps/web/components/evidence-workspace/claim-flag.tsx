"use client";

import { useId, useState, useSyncExternalStore } from "react";

import {
  FLAG_REASONS,
  flagsSnapshot,
  raiseFlag,
  reasonLabel,
  serverFlagsSnapshot,
  subscribeFlags,
  withdrawFlag,
  type FlagReason,
} from "@/lib/flags";

import styles from "./workspace.module.css";

/**
 * The reader's own judgement on a claim, recorded where the claim is. A flagged claim
 * shows its reason in one line; the control to change or withdraw it stays beside it.
 */
export function ClaimFlag({
  claimId,
  claimText,
  evidenceIds,
  questionId,
}: {
  claimId: string;
  claimText: string;
  evidenceIds: string[];
  questionId: string;
}) {
  const flags = useSyncExternalStore(subscribeFlags, flagsSnapshot, serverFlagsSnapshot);
  const existing = flags.find((flag) => flag.questionId === questionId && flag.claimId === claimId) ?? null;
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<FlagReason>(existing?.reason ?? "wrong_passage");
  const [note, setNote] = useState(existing?.note ?? "");
  const formId = useId();

  function submit() {
    raiseFlag({ questionId, claimId, claimText, evidenceIds, reason, note: note.trim() });
    setOpen(false);
  }

  return (
    <div className={styles["claim-flag"]} data-print="hide">
      {existing && !open ? (
        <p className={styles["claim-flag-state"]}>
          Flagged: {reasonLabel(existing.reason)}
          {existing.note ? ` — ${existing.note}` : ""}{" "}
          <button onClick={() => setOpen(true)} type="button">
            Change
          </button>{" "}
          <button onClick={() => withdrawFlag(questionId, claimId)} type="button">
            Withdraw
          </button>
        </p>
      ) : null}
      {!existing && !open ? (
        <button
          aria-expanded={false}
          aria-controls={formId}
          className={styles["claim-flag-open"]}
          onClick={() => setOpen(true)}
          type="button"
        >
          Flag this claim
        </button>
      ) : null}
      {open ? (
        <form
          aria-label="Flag this claim"
          className={styles["claim-flag-form"]}
          id={formId}
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <fieldset>
            <legend>What is wrong</legend>
            {FLAG_REASONS.map(([value, label]) => (
              <label key={value}>
                <input
                  checked={reason === value}
                  name={`${formId}-reason`}
                  onChange={() => setReason(value)}
                  type="radio"
                  value={value}
                />
                {label}
              </label>
            ))}
          </fieldset>
          <label>
            Note
            <input
              maxLength={300}
              onChange={(event) => setNote(event.target.value)}
              placeholder="One sentence, optional"
              type="text"
              value={note}
            />
          </label>
          <div>
            <button className={styles["claim-flag-save"]} type="submit">
              Save flag
            </button>
            <button onClick={() => setOpen(false)} type="button">
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
