"use client";

import {
  ArrowRight,
  Check,
  ChevronDown,
  Clock,
  CircleDashed,
  Globe,
  Search,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import {
  describeIdentifiers,
  detectIdentifiers,
  stripIdentifiers,
} from "@/lib/identifiers";
import type { SourceFilters } from "@/lib/types";

import styles from "./workspace.module.css";

/*
 * Examples the active corpus can actually answer.
 *
 * The MVP release is WHO SMART HIV. Offering questions about anticoagulation or
 * colorectal screening taught readers to ask things this corpus is guaranteed to abstain
 * on, and an abstention a person was invited into reads as a broken product rather than
 * an out-of-scope one.
 */
export const EXAMPLES = [
  {
    label: "Viral load monitoring",
    question:
      "How often should viral load be monitored for an adult established on antiretroviral therapy?",
  },
  {
    label: "Treatment failure",
    question:
      "What do current guidelines recommend when an adult on first-line ART has a confirmed high viral load?",
  },
  {
    label: "PrEP eligibility",
    question:
      "Which adults do current guidelines recommend be offered pre-exposure prophylaxis, and what testing is required first?",
  },
  {
    label: "Testing services",
    question:
      "What retesting do current guidelines recommend after a reactive HIV rapid diagnostic test?",
  },
];

export const EXAMPLE_QUESTIONS = EXAMPLES.map((example) => example.question);

/*
 * The guideline bodies this product intends to cover, and where each one stands.
 *
 * Only WHO is validated and served today. The rest are listed rather than hidden because
 * the first question a clinician asks of an evidence tool is what it has read - and an
 * empty answer from a corpus whose boundary was never stated reads as a defect. Listing
 * them is a roadmap, not a capability: nothing here can be selected until its release is
 * approved, and the interface says so on each row.
 */
const SOURCE_BODIES = [
  {
    id: "WHO",
    name: "World Health Organization",
    detail: "Consolidated HIV guidelines, testing services, SMART adaptation kit",
    available: true,
  },
  {
    id: "CDC",
    name: "US Centers for Disease Control",
    detail: "HIV treatment, prevention and PrEP clinical guidance",
    available: false,
  },
  {
    id: "DHHS",
    name: "US DHHS / NIH",
    detail: "Antiretroviral guidelines for adults, adolescents and pregnancy",
    available: false,
  },
  {
    id: "EACS",
    name: "European AIDS Clinical Society",
    detail: "European HIV treatment and comorbidity guidelines",
    available: false,
  },
  {
    id: "BHIVA",
    name: "British HIV Association",
    detail: "UK treatment, monitoring and PrEP guidelines",
    available: false,
  },
];

interface QuestionComposerProps {
  /**
   * True while the run has been accepted by this browser but not yet by the server.
   *
   * Derived from the run rather than tracked here. This component used to hold its own
   * `isSubmitting` flag around the submit call, which meant a request that never settled
   * left the composer locked with no way out: cancelling ends the *run*, and nothing could
   * end a promise. Every busy state now reads from the one place that knows, so stopping a
   * run releases the controls whatever the in-flight request does afterwards.
   */
  isStarting?: boolean;
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
  isStarting: isStartingRun = false,
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
  const [organizationInput, setOrganizationInput] = useState("");
  const [identifiersDismissed, setIdentifiersDismissed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scopeDetailsRef = useRef<HTMLDetailsElement>(null);
  const scopeSummaryRef = useRef<HTMLElement>(null);
  const submittingRef = useRef(false);
  const focusAfterQuestionChangeRef = useRef(false);
  const questionId = useId();
  const questionHelpId = useId();
  const questionErrorId = useId();
  const scopeHelpId = useId();
  const organizationId = useId();
  const organizationHelpId = useId();
  /*
   * Busy is the run's state, and only the run's state.
   *
   * This used to be `isRunning || isSubmitting`, where `isSubmitting` was local state held
   * across the submit promise. That made the composer impossible to release: a request
   * that never settled left the controls disabled and the textarea read-only with no way
   * out but a reload, because "Stop waiting" ends a *run* and nothing can end a promise.
   * The workspace marks the run live synchronously inside `onSubmit`, before its first
   * await, so reading the run here locks just as promptly and unlocks the moment the run
   * stops - however it stops.
   */
  const isBusy = isRunning;

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

    const submittedFilters = filtersWithOrganizationDraft();
    submittingRef.current = true;
    try {
      const accepted = await onSubmit(trimmedQuestion, submittedFilters);
      if (accepted && scopeDetailsRef.current) scopeDetailsRef.current.open = false;
    } finally {
      submittingRef.current = false;
    }
  }

  /**
   * Stop watching the run, and stop treating a submission as in flight.
   *
   * `handleSubmit`'s `finally` clears the guard on every path where the request settles.
   * This is the path where it might not: the reader has decided the run is over, and a
   * guard still held by a promise nobody can cancel would let them type a new question and
   * then silently swallow the submit.
   */
  function handleStopWaiting() {
    submittingRef.current = false;
    onStopWaiting();
  }

  function handleQuestionKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
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
    onSourceFiltersChange({ ...sourceFilters, organizations: [] });
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

  /*
   * The identifier guard.
   *
   * Runs on the current draft, in the browser, and nothing it finds leaves the page. It
   * never blocks submission: this is a research prototype whose reader may legitimately be
   * testing with synthetic text, and a guard that cannot be overruled becomes a guard
   * people route around.
   */
  const identifiers = useMemo(() => detectIdentifiers(question), [question]);
  const identifierWarningId = useId();
  const showIdentifierWarning = identifiers.length > 0 && !identifiersDismissed;

  const describedBy = [
    questionHelpId,
    questionError ? questionErrorId : null,
    showIdentifierWarning ? identifierWarningId : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section
      className={styles["composer-shell"]}
      aria-labelledby="composer-heading"
      aria-busy={isBusy}
    >
      <form className={styles["question-form"]} id="ask" onSubmit={handleSubmit} noValidate>
        <div className={styles["composer-intro"]}>
          <h1 id="composer-heading">What guideline decision are you reviewing?</h1>
          {/* Visual cues only. The same guidance reaches assistive technology as prose in
              the field description below, so announcing three bare nouns here would
              repeat it worse. `aria-label` on a plain div is dropped anyway.

              Set as a legend under the heading rather than as three filled chips beside
              it. Filled, they sat at the weight of the source control below and read as
              something to press; as small caps directly above the field they read as what
              they are - the shape of a question worth asking. */}
          <p className={styles["question-anatomy"]} aria-hidden="true">
            <span>Population</span>
            <span>Condition</span>
            <span>Decision</span>
          </p>
        </div>
        <p className={styles.srOnly} id={questionHelpId}>
          Ask one focused guideline question. Include the population, condition, and clinical
          decision. Do not include names or patient identifiers.
        </p>

        {/* The field takes the full width and the submit sits in the control row below it.
            Side by side, the button was pinned to a box that grows as the question does,
            and a long question squeezed the one input this whole screen exists to serve. */}
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
              setIdentifiersDismissed(false);
              onChange(event.target.value);
            }}
            onKeyDown={handleQuestionKeyDown}
            maxLength={4000}
            rows={1}
            placeholder="e.g., For an adult on first-line ART with a viral load of 1200 copies/mL, what do guidelines recommend?"
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

        {questionError ? (
          <p className={styles["field-error"]} id={questionErrorId} role="alert">
            {questionError}
          </p>
        ) : null}

        {showIdentifierWarning ? (
          <div className={styles["identifier-warning"]} id={identifierWarningId} role="status">
            <ShieldAlert size={16} aria-hidden="true" />
            <div>
              <strong>
                This looks like it contains {describeIdentifiers(identifiers)}
              </strong>
              <p>
                Remove{" "}
                {identifiers.map((item, index) => (
                  <span key={`${item.start}-${item.text}`}>
                    {index > 0 ? ", " : ""}
                    <mark>{item.text}</mark>
                  </span>
                ))}{" "}
                before running. This is a research prototype and nothing you enter should
                identify a person.
              </p>
              <div className={styles["identifier-actions"]}>
                <button
                  type="button"
                  onClick={() => {
                    focusAfterQuestionChangeRef.current = true;
                    onChange(stripIdentifiers(question, identifiers));
                  }}
                >
                  Remove {identifiers.length === 1 ? "it" : "them"}
                </button>
                <button
                  className={styles["identifier-dismiss"]}
                  type="button"
                  onClick={() => setIdentifiersDismissed(true)}
                >
                  Keep, it is not real
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {/* One control row: what the review will read, what it must not be given, and the
            action that starts it. These were split across two rows at three different
            weights - an outlined pill, a green tick, and the black button - which is what
            made the panel read as a pile of unrelated controls. Ink now means one thing
            here: press this. Everything informational is grey. */}
        <div className={styles["composer-actions"]}>
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
                <p>
                  What this release has read, and what is still to come. Availability
                  follows an approved corpus release, not a preference.
                </p>
              </div>
              {/* No jurisdiction control. Every record in the active release is scoped
                  WORLD, and the serving path widens any selection to include it, so the
                  filter could narrow nothing - it only claimed to. What the corpus does
                  and does not carry is stated instead. */}
              <ul className={styles["source-bodies"]} aria-label="Guideline bodies">
                {SOURCE_BODIES.map((body) => (
                  <li
                    className={body.available ? undefined : styles["source-planned"]}
                    key={body.id}
                  >
                    <span className={styles["source-mark"]} aria-hidden="true">
                      {body.available ? <Check size={13} /> : <Clock size={13} />}
                    </span>
                    <span className={styles["source-copy"]}>
                      <strong>{body.name}</strong>
                      <small>{body.detail}</small>
                    </span>
                    <span
                      className={
                        body.available ? styles["source-live"] : styles["source-soon"]
                      }
                    >
                      {body.available ? "Active" : "Coming soon"}
                    </span>
                  </li>
                ))}
              </ul>
              <p className={styles["scope-scope-note"]} id={scopeHelpId}>
                <Globe size={13} aria-hidden="true" />
                <span>
                  WHO records are globally scoped, so retrieval is not narrowed by country.
                </span>
              </p>

              <div className={styles["organization-filter"]}>
                <label htmlFor={organizationId}>
                  Organizations <span aria-hidden="true">Optional</span>
                </label>
                <p id={organizationHelpId}>
                  Narrow to a publisher, then press Enter or comma.
                </p>
                <div className={styles["organization-entry"]}>
                  <input
                    id={organizationId}
                    type="text"
                    value={organizationInput}
                    placeholder="e.g., WHO"
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
                {isStartingRun ? "Starting review" : "Question and sources locked to this review"}
              </span>
            ) : (
              /* Grey, not green. Green in this workspace means a gate passed, and nothing
                 has been checked here - this is an instruction to the reader, not a
                 result. A tick that claims a verification it did not do is worse than no
                 tick at all. */
              <span className={styles["privacy-cue"]}>
                <ShieldCheck size={15} aria-hidden="true" />
                No patient identifiers
              </span>
            )}

            {/* The Ctrl/Cmd+Enter shortcut still works and is still announced through
                `aria-keyshortcuts` on the textarea; it is no longer drawn here. A key cap
                sitting under the composer at all times spent the reader's attention on
                something they learn once, next to a boundary notice they must not learn to
                ignore. Where the shortcut is written down is the menu. */}
            {isRunning ? (
              <button
                className={styles["stop-waiting-link"]}
                type="button"
                onClick={handleStopWaiting}
              >
                <Square size={13} fill="currentColor" aria-hidden="true" />
                Stop waiting
              </button>
            ) : null}
          </div>

          {/* `aria-disabled` rather than `disabled`, for the same reason the textarea
              above it uses `readOnly`: a disabled control loses focus, and this one is
              disabled at the exact moment it is holding it - the click that starts the
              run. The browser would drop focus to <body> for the length of the review.
              `handleSubmit` already rejects an empty or in-flight submission, so the
              button stays focusable and does nothing. */}
          <button
            className={styles["ask-button"]}
            type="submit"
            aria-disabled={!question.trim() || isBusy}
          >
            {isBusy ? (
              <CircleDashed className={styles.spin} size={18} aria-hidden="true" />
            ) : (
              <Search size={18} aria-hidden="true" />
            )}
            {isStartingRun ? "Starting..." : isRunning ? "Reviewing..." : "Review evidence"}
          </button>
        </div>
      </form>

      {showExamples ? (
        <div className={styles["example-questions"]} role="group" aria-labelledby="example-heading">
          <span id="example-heading">Try a focused example</span>
          <div>
            {EXAMPLES.map(({ label, question: example }) => (
              <button
                // The visible topic leads the accessible name, then the question it
                // fills in. WCAG 2.5.3 requires the visible label to be part of the
                // accessible name, so speech input can activate the button by what it
                // reads; the full question stays available to a screen reader.
                aria-label={`${label}: ${example}`}
                disabled={isBusy}
                key={label}
                title={example}
                type="button"
                onClick={() => chooseExample(example)}
              >
                <span>{label}</span>
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
  if (!filters.organizations.length) return "WHO, global";
  const count = filters.organizations.length;
  return `${count} publisher${count === 1 ? "" : "s"}`;
}
