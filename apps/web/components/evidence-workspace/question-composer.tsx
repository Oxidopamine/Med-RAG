"use client";

import {
  ArrowRight,
  Check,
  ChevronDown,
  CircleDashed,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type { SourceFilters } from "@/lib/types";

import styles from "./workspace.module.css";

export const EXAMPLE_QUESTIONS = [
  "For an older adult with atrial fibrillation and renal impairment, what do current guidelines recommend about anticoagulation?",
  "What do current guidelines recommend for first-line hypertension treatment in adults with diabetes?",
  "When do current guidelines recommend colorectal cancer screening for an average-risk adult?",
];

const EXAMPLE_LABELS = [
  "AF + renal impairment",
  "Hypertension + diabetes",
  "Colorectal screening",
];

const JURISDICTIONS = [
  { code: "US", label: "United States" },
  { code: "EU", label: "European Union" },
  { code: "UK", label: "United Kingdom" },
];

interface QuestionComposerProps {
  isRunning: boolean;
  onChange: (question: string) => void;
  onSourceFiltersChange: (filters: SourceFilters) => void;
  onStopWaiting: () => void;
  onSubmit: (question: string, sourceFilters: SourceFilters) => Promise<boolean>;
  question: string;
  showExamples?: boolean;
  sourceFilters: SourceFilters;
}

export function QuestionComposer({
  isRunning,
  onChange,
  onSourceFiltersChange,
  onStopWaiting,
  onSubmit,
  question,
  showExamples = false,
  sourceFilters,
}: QuestionComposerProps) {
  const [questionError, setQuestionError] = useState("");
  const [scopeError, setScopeError] = useState("");
  const [organizationInput, setOrganizationInput] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scopeDetailsRef = useRef<HTMLDetailsElement>(null);
  const scopeSummaryRef = useRef<HTMLElement>(null);
  const firstJurisdictionRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false);
  const focusAfterQuestionChangeRef = useRef(false);
  const questionId = useId();
  const questionHelpId = useId();
  const questionErrorId = useId();
  const scopeHelpId = useId();
  const scopeErrorId = useId();
  const organizationId = useId();
  const organizationHelpId = useId();
  const isBusy = isRunning || isSubmitting;

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    if (!question) {
      textarea.style.height = "";
      return;
    }
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 60), 144)}px`;
    if (focusAfterQuestionChangeRef.current) {
      textarea.focus();
      textarea.setSelectionRange(question.length, question.length);
      focusAfterQuestionChangeRef.current = false;
    }
  }, [question]);

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      const details = scopeDetailsRef.current;
      if (details?.open && event.target instanceof Node && !details.contains(event.target)) {
        details.open = false;
      }
    }

    function handleEscape(event: globalThis.KeyboardEvent) {
      const details = scopeDetailsRef.current;
      if (event.key !== "Escape" || !details?.open) return;
      event.preventDefault();
      details.open = false;
      scopeSummaryRef.current?.focus();
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isBusy || submittingRef.current) return;

    const trimmedQuestion = question.trim();
    if (trimmedQuestion.length < 3) {
      setQuestionError(
        "Add a little more detail, such as the population and clinical decision.",
      );
      textareaRef.current?.focus();
      return;
    }
    setQuestionError("");

    if (sourceFilters.jurisdictions.length === 0) {
      setScopeError("Select at least one source jurisdiction before starting the review.");
      if (scopeDetailsRef.current) scopeDetailsRef.current.open = true;
      firstJurisdictionRef.current?.focus();
      return;
    }

    const submittedFilters = filtersWithOrganizationDraft();
    submittingRef.current = true;
    setIsSubmitting(true);
    setScopeError("");
    try {
      const accepted = await onSubmit(trimmedQuestion, submittedFilters);
      if (accepted && scopeDetailsRef.current) scopeDetailsRef.current.open = false;
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  function handleQuestionKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function toggleJurisdiction(jurisdiction: string) {
    if (isBusy) return;
    const isSelected = sourceFilters.jurisdictions.includes(jurisdiction);
    if (isSelected && sourceFilters.jurisdictions.length === 1) {
      setScopeError("Keep at least one jurisdiction selected.");
      return;
    }
    const jurisdictions = isSelected
      ? sourceFilters.jurisdictions.filter((item) => item !== jurisdiction)
      : [...sourceFilters.jurisdictions, jurisdiction];
    setScopeError("");
    onSourceFiltersChange({ ...sourceFilters, jurisdictions });
  }

  function filtersWithOrganizationDraft(): SourceFilters {
    const organizations = mergeOrganizations(
      sourceFilters.organizations,
      organizationInput,
    );
    if (
      organizationInput.trim() ||
      organizations.length !== sourceFilters.organizations.length
    ) {
      onSourceFiltersChange({ ...sourceFilters, organizations });
      setOrganizationInput("");
    }
    return { ...sourceFilters, organizations };
  }

  function commitOrganizationDraft() {
    if (isBusy || !organizationInput.trim()) return;
    filtersWithOrganizationDraft();
  }

  function handleOrganizationKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if ((event.key === "Enter" || event.key === ",") && organizationInput.trim()) {
      event.preventDefault();
      commitOrganizationDraft();
    }
  }

  function removeOrganization(organization: string) {
    if (isBusy) return;
    onSourceFiltersChange({
      ...sourceFilters,
      organizations: sourceFilters.organizations.filter((item) => item !== organization),
    });
  }

  function resetScope() {
    if (isBusy) return;
    setOrganizationInput("");
    setScopeError("");
    onSourceFiltersChange({
      jurisdictions: JURISDICTIONS.map((jurisdiction) => jurisdiction.code),
      organizations: [],
    });
  }

  function finishScopeEditing() {
    if (!isBusy) commitOrganizationDraft();
    if (scopeDetailsRef.current) scopeDetailsRef.current.open = false;
    scopeSummaryRef.current?.focus();
  }

  function chooseExample(example: string) {
    if (isBusy) return;
    setQuestionError("");
    focusAfterQuestionChangeRef.current = true;
    onChange(example);
  }

  const describedBy = questionError
    ? `${questionHelpId} ${questionErrorId}`
    : questionHelpId;
  const scopeDescribedBy = scopeError
    ? `${scopeHelpId} ${scopeErrorId}`
    : scopeHelpId;

  return (
    <section
      className={styles["composer-shell"]}
      aria-labelledby="composer-heading"
      aria-busy={isBusy}
    >
      <form className={styles["question-form"]} id="ask" onSubmit={handleSubmit} noValidate>
        <div className={styles["composer-heading-row"]}>
          <h1 id="composer-heading">What guideline decision are you reviewing?</h1>
          <div
            className={styles["question-anatomy"]}
            aria-label="A focused question includes population, condition, and decision"
          >
            <span>Population</span>
            <span>Condition</span>
            <span>Decision</span>
          </div>
        </div>
        <p className={styles.srOnly} id={questionHelpId}>
          Ask one focused guideline question. Include the population, condition, and clinical
          decision. Do not include names or patient identifiers.
        </p>

        <div className={styles["question-bar"]}>
          <div
            className={`${styles["question-field"]} ${questionError ? styles["question-field-error"] : ""}`}
          >
            <Search className={styles["question-leading-icon"]} size={21} aria-hidden="true" />
            <label className={styles.srOnly} htmlFor={questionId}>
              Guideline question
            </label>
            <textarea
              ref={textareaRef}
              id={questionId}
              value={question}
              onChange={(event) => {
                setQuestionError("");
                onChange(event.target.value);
              }}
              onKeyDown={handleQuestionKeyDown}
              maxLength={4000}
              rows={1}
              placeholder="e.g., In adults with atrial fibrillation and eGFR 28, what do guidelines recommend for anticoagulation?"
              aria-describedby={describedBy}
              aria-invalid={Boolean(questionError)}
              aria-keyshortcuts="Control+Enter Meta+Enter"
              readOnly={isBusy}
            />
            {question && !isBusy ? (
              <button
                className={styles["clear-question"]}
                type="button"
                onClick={() => {
                  setQuestionError("");
                  onChange("");
                  textareaRef.current?.focus();
                }}
                aria-label="Clear question"
              >
                <X size={18} aria-hidden="true" />
              </button>
            ) : null}
          </div>

          <button
            className={styles["ask-button"]}
            type="submit"
            disabled={!question.trim() || isBusy}
          >
            {isBusy ? (
              <CircleDashed className={styles.spin} size={18} aria-hidden="true" />
            ) : (
              <Search size={18} aria-hidden="true" />
            )}
            {isRunning ? "Reviewing..." : isSubmitting ? "Starting..." : "Review evidence"}
          </button>
        </div>

        {questionError ? (
          <p className={styles["field-error"]} id={questionErrorId} role="alert">
            {questionError}
          </p>
        ) : null}

        <div className={styles["composer-options"]}>
          <details className={styles["scope-details"]} ref={scopeDetailsRef}>
            <summary ref={scopeSummaryRef}>
              <span className={styles["scope-summary-icon"]} aria-hidden="true">
                <SlidersHorizontal size={16} />
              </span>
              <span className={styles["scope-summary-copy"]}>
                <strong>Sources</strong>
                <small>{sourceSummary(sourceFilters)}</small>
              </span>
              <ChevronDown className={styles["scope-chevron"]} size={16} aria-hidden="true" />
            </summary>
            <div className={styles["scope-panel"]}>
              <div className={styles["scope-panel-heading"]}>
                <strong>Source coverage</strong>
                <p id={scopeHelpId}>
                  These filters narrow retrieval; source approval rules still apply.
                </p>
              </div>
              <fieldset
                aria-describedby={scopeDescribedBy}
                aria-invalid={Boolean(scopeError)}
              >
                <legend>Jurisdictions</legend>
                <div className={styles["scope-checkboxes"]}>
                  {JURISDICTIONS.map((jurisdiction) => {
                    const selected = sourceFilters.jurisdictions.includes(jurisdiction.code);
                    return (
                      <label
                        className={selected ? styles["scope-option-selected"] : undefined}
                        key={jurisdiction.code}
                      >
                        <input
                          ref={
                            jurisdiction.code === JURISDICTIONS[0]!.code
                              ? firstJurisdictionRef
                              : undefined
                          }
                          type="checkbox"
                          aria-label={`${jurisdiction.code}, ${jurisdiction.label}`}
                          checked={selected}
                          disabled={isBusy}
                          onChange={() => toggleJurisdiction(jurisdiction.code)}
                        />
                        <span>
                          <strong>{jurisdiction.code}</strong>
                          <small>{jurisdiction.label}</small>
                        </span>
                        {selected ? <Check size={15} aria-hidden="true" /> : null}
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <div className={styles["organization-filter"]}>
                <label htmlFor={organizationId}>
                  Organizations <span aria-hidden="true">Optional</span>
                </label>
                <p id={organizationHelpId}>
                  Add a publisher or society, then press Enter or comma.
                </p>
                <div className={styles["organization-entry"]}>
                  <input
                    id={organizationId}
                    type="text"
                    value={organizationInput}
                    placeholder="e.g., ACC/AHA"
                    maxLength={500}
                    disabled={isBusy}
                    aria-describedby={organizationHelpId}
                    onChange={(event) => setOrganizationInput(event.target.value)}
                    onKeyDown={handleOrganizationKeyDown}
                    onBlur={commitOrganizationDraft}
                  />
                  <button
                    type="button"
                    disabled={isBusy || !organizationInput.trim()}
                    onClick={commitOrganizationDraft}
                  >
                    Add
                  </button>
                </div>
                {sourceFilters.organizations.length ? (
                  <ul className={styles["organization-tokens"]} aria-label="Selected organizations">
                    {sourceFilters.organizations.map((organization) => (
                      <li key={organization}>
                        <span>{organization}</span>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => removeOrganization(organization)}
                          aria-label={`Remove ${organization}`}
                        >
                          <X size={14} aria-hidden="true" />
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>

              {scopeError ? (
                <p className={styles["field-error"]} id={scopeErrorId} role="alert">
                  {scopeError}
                </p>
              ) : null}

              <div className={styles["scope-actions"]}>
                <button type="button" disabled={isBusy} onClick={resetScope}>
                  Reset
                </button>
                <button type="button" onClick={finishScopeEditing}>
                  Done
                </button>
              </div>
            </div>
          </details>

          <div className={styles["composer-utility"]}>
            {isBusy ? (
              <span className={styles["composer-run-note"]} role="status">
                <CircleDashed className={styles.spin} size={15} aria-hidden="true" />
                {isRunning ? "Question and sources locked to this review" : "Starting review"}
              </span>
            ) : (
              <span className={styles["privacy-cue"]}>
                <ShieldCheck size={15} aria-hidden="true" />
                No patient identifiers
              </span>
            )}

            {isRunning ? (
              <button
                className={styles["stop-waiting-link"]}
                type="button"
                onClick={onStopWaiting}
              >
                <Square size={13} fill="currentColor" aria-hidden="true" />
                Stop waiting
              </button>
            ) : (
              <span className={styles["keyboard-hint"]} aria-hidden="true">
                <kbd>Ctrl</kbd>
                <span>/</span>
                <kbd>⌘</kbd>
                <span>+</span>
                <kbd>Enter</kbd>
              </span>
            )}
          </div>
        </div>
      </form>

      {showExamples ? (
        <div className={styles["example-questions"]} aria-labelledby="example-heading">
          <span id="example-heading">Try a focused example</span>
          <div>
            {EXAMPLE_QUESTIONS.map((example, index) => (
              <button
                aria-label={example}
                disabled={isBusy}
                key={example}
                title={example}
                type="button"
                onClick={() => chooseExample(example)}
              >
                <span>{EXAMPLE_LABELS[index]}</span>
                <ArrowRight size={15} aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function mergeOrganizations(existing: string[], draft: string): string[] {
  const organizations = [...existing];
  const seen = new Set(existing.map((organization) => organization.toLocaleLowerCase()));
  for (const organization of draft.split(/[,;\n]/)) {
    const trimmed = organization.trim();
    const normalized = trimmed.toLocaleLowerCase();
    if (!trimmed || seen.has(normalized)) continue;
    seen.add(normalized);
    organizations.push(trimmed);
  }
  return organizations;
}

function sourceSummary(filters: SourceFilters): string {
  const jurisdictions = filters.jurisdictions.length
    ? filters.jurisdictions.join(" · ")
    : "Choose coverage";
  const organizations = filters.organizations.length
    ? ` + ${filters.organizations.length} org${filters.organizations.length === 1 ? "" : "s"}`
    : "";
  return `${jurisdictions}${organizations}`;
}
