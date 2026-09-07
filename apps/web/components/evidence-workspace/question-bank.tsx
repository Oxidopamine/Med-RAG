"use client";

import { BookOpen, ChevronDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import {
  QUESTION_BANK,
  QUESTION_BANK_AREAS,
  filterQuestionBank,
  questionBankAreaLabel,
  type QuestionBankAreaId,
} from "@/lib/question-bank";

import styles from "./workspace.module.css";

interface QuestionBankProps {
  /** True while a review is running: the bank stays readable but cannot change the draft. */
  disabled: boolean;
  onChoose: (question: string) => void;
}

/**
 * The question bank disclosure.
 *
 * Sits beside the source control and follows its behaviour: a native `details` element,
 * closed by Escape or by a click outside it, focus returned to the summary. Choosing a
 * question fills the composer and closes the panel; it never submits, because the reader
 * may want to edit the population or the decision first, and because a bank that fires
 * reviews on click would make a mis-click cost a run.
 */
export function QuestionBank({ disabled, onChoose }: QuestionBankProps) {
  const [query, setQuery] = useState("");
  const [area, setArea] = useState<QuestionBankAreaId | "all">("all");
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const summaryRef = useRef<HTMLElement>(null);
  const headingId = useId();
  const searchId = useId();
  const areaId = useId();
  const countId = useId();

  useEffect(() => {
    // On click, not pointerdown, for the reason given in the source control: closing on
    // pointerdown can move the control under the pointer before its click arrives.
    function handleOutsideClick(event: MouseEvent) {
      const details = detailsRef.current;
      if (details?.open && event.target instanceof Node && !details.contains(event.target)) {
        details.open = false;
      }
    }

    function handleEscape(event: globalThis.KeyboardEvent) {
      const details = detailsRef.current;
      if (event.key !== "Escape" || !details?.open) return;
      event.preventDefault();
      details.open = false;
      summaryRef.current?.focus();
    }

    document.addEventListener("click", handleOutsideClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("click", handleOutsideClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, []);

  const matches = useMemo(
    () => filterQuestionBank(QUESTION_BANK, { query, area }),
    [query, area],
  );

  function choose(question: string) {
    if (disabled) return;
    onChoose(question);
    if (detailsRef.current) detailsRef.current.open = false;
  }

  return (
    <details className={styles["bank-details"]} ref={detailsRef}>
      <summary ref={summaryRef} aria-describedby={countId}>
        <span className={styles["scope-summary-icon"]} aria-hidden="true">
          <BookOpen size={16} />
        </span>
        <span className={styles["scope-summary-copy"]}>
          <strong>Question bank</strong>
          <small>{QUESTION_BANK.length} questions</small>
        </span>
        <ChevronDown className={styles["scope-chevron"]} size={16} aria-hidden="true" />
      </summary>
      <div className={styles["bank-panel"]} role="group" aria-labelledby={headingId}>
        <div className={styles["scope-panel-heading"]}>
          <strong id={headingId}>Question bank</strong>
          <p>
            Questions phrased the way a clinician asks them, by area. Choosing one fills the
            question field so it can be edited before the review runs. Coverage follows the
            active release: a planned area is expected to abstain.
          </p>
        </div>

        <div className={styles["bank-controls"]}>
          <div className={styles["bank-search"]}>
            <Search size={15} aria-hidden="true" />
            <label className={styles.srOnly} htmlFor={searchId}>
              Search questions
            </label>
            <input
              id={searchId}
              type="search"
              value={query}
              placeholder="Search questions"
              autoComplete="off"
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <label className={styles.srOnly} htmlFor={areaId}>
            Clinical area
          </label>
          <select
            id={areaId}
            className={styles["bank-area"]}
            value={area}
            onChange={(event) => setArea(event.target.value as QuestionBankAreaId | "all")}
          >
            <option value="all">All areas</option>
            {QUESTION_BANK_AREAS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.status === "planned" ? `${item.label} (planned release)` : item.label}
              </option>
            ))}
          </select>
        </div>

        <p className={styles["bank-count"]} id={countId} aria-live="polite">
          {matches.length === QUESTION_BANK.length
            ? `${matches.length} questions`
            : `${matches.length} of ${QUESTION_BANK.length} questions`}
        </p>

        {matches.length ? (
          <ul className={styles["bank-list"]} aria-label="Questions">
            {matches.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => choose(entry.question)}
                >
                  <span>{entry.question}</span>
                  <small>
                    {questionBankAreaLabel(entry.area)}
                    {" · "}
                    {entry.purpose}
                  </small>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles["bank-empty"]}>
            No question matches. Clear the search or choose another area.
          </p>
        )}
      </div>
    </details>
  );
}
