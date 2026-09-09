"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Plus, RotateCw, Trash2, X } from "lucide-react";
import {
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import type { ZodIssue } from "zod";

import { clinicalContextSchema } from "@/lib/contracts";
import type { ClinicalContext } from "@/lib/types";

import styles from "./context-dialog.module.css";

// The vocabulary follows the releases the instrument serves: the HIV guidelines first,
// then the hypertension and chronic-care releases that are planned. A list written for a
// release the corpus does not carry misleads about what the review reads.
const CONDITION_SUGGESTIONS = [
  "HIV_INFECTION",
  "ADVANCED_HIV_DISEASE",
  "TUBERCULOSIS",
  "HEPATITIS_B",
  "HEPATITIS_C",
  "CRYPTOCOCCAL_MENINGITIS",
  "HYPERTENSION",
  "DIABETES_MELLITUS",
  "CHRONIC_KIDNEY_DISEASE",
  "HEART_FAILURE",
];

const POPULATION_SUGGESTIONS = [
  "PREGNANCY",
  "BREASTFEEDING",
  "PEDIATRIC",
  "ADOLESCENT",
  "OLDER_ADULT",
  "KEY_POPULATION",
  "RENAL_IMPAIRMENT",
  "HEPATIC_IMPAIRMENT",
];

const MEASUREMENT_SUGGESTIONS = [
  "HIV_VIRAL_LOAD",
  "CD4_COUNT",
  "WEIGHT",
  "BMI",
  "SYSTOLIC_BLOOD_PRESSURE",
  "CREATININE_CLEARANCE",
  "EGFR",
  "HEMOGLOBIN",
];

const UNIT_SUGGESTIONS = [
  "copies/mL",
  "cells/mm3",
  "kg",
  "kg/m2",
  "mmHg",
  "mL/min",
  "mL/min/1.73m2",
  "mg/dL",
  "mmol/L",
  "g/dL",
  "%",
];

const SEX_OPTIONS = [
  { label: "Female", value: "FEMALE" },
  { label: "Male", value: "MALE" },
  { label: "Intersex", value: "INTERSEX" },
  { label: "Another documented value", value: "OTHER" },
];

const CARE_SETTING_OPTIONS = [
  { label: "Primary care", value: "PRIMARY_CARE" },
  { label: "Outpatient", value: "OUTPATIENT" },
  { label: "Inpatient", value: "INPATIENT" },
  { label: "Emergency department", value: "EMERGENCY_DEPARTMENT" },
  { label: "Intensive care", value: "INTENSIVE_CARE" },
  { label: "Specialty care", value: "SPECIALTY_CARE" },
];

const JURISDICTION_OPTIONS = [
  { label: "United States", value: "UNITED_STATES" },
  { label: "United Kingdom", value: "UNITED_KINGDOM" },
  { label: "European Union", value: "EUROPEAN_UNION" },
  { label: "Canada", value: "CANADA" },
  { label: "Australia", value: "AUSTRALIA" },
  { label: "International", value: "INTERNATIONAL" },
];

const DISPLAY_NAMES: Record<string, string> = {
  BMI: "BMI",
  EGFR: "eGFR",
  INR: "INR",
  HIV_INFECTION: "HIV infection",
  ADVANCED_HIV_DISEASE: "Advanced HIV disease",
  HIV_VIRAL_LOAD: "HIV viral load",
  CD4_COUNT: "CD4 count",
  HEPATITIS_B: "Hepatitis B",
  HEPATITIS_C: "Hepatitis C",
  CRYPTOCOCCAL_MENINGITIS: "Cryptococcal meningitis",
  KEY_POPULATION: "Key population",
};

/** Acronyms a title-casing pass would otherwise turn into words. */
const ACRONYMS: Record<string, string> = {
  hiv: "HIV",
  tb: "TB",
  art: "ART",
  prep: "PrEP",
  pep: "PEP",
  cd4: "CD4",
  who: "WHO",
  egfr: "eGFR",
  bmi: "BMI",
  inr: "INR",
};

function normalizeConcept(value: string): string {
  return value
    .trim()
    .toUpperCase()
    .replaceAll(/[^A-Z0-9]+/g, "_")
    .replaceAll(/^_+|_+$/g, "");
}

function humanizeConcept(value: string): string {
  if (DISPLAY_NAMES[value]) return DISPLAY_NAMES[value];
  return value
    .replaceAll("_", " ")
    .toLowerCase()
    .split(" ")
    .map((word, index) =>
      ACRONYMS[word] ?? (index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word),
    )
    .join(" ");
}

function joinIds(...ids: Array<string | undefined>): string | undefined {
  const presentIds = ids.filter(Boolean);
  return presentIds.length ? presentIds.join(" ") : undefined;
}

function issueFor(issues: ZodIssue[], path: Array<string | number>): ZodIssue | undefined {
  return issues.find(
    (issue) =>
      issue.path.length === path.length &&
      issue.path.every((part, index) => part === path[index]),
  );
}

function meaningfulIssue(issue: ZodIssue | undefined): string | undefined {
  if (!issue) return undefined;
  const [field, , measurementField] = issue.path;

  if (field === "age") return "Enter a whole age from 0 to 130, or leave it blank.";
  if (field === "measurements" && measurementField === "concept") {
    return "Enter a name for this measurement.";
  }
  if (field === "measurements" && measurementField === "value") {
    return "Enter a valid numeric value.";
  }
  if (field === "measurements" && measurementField === "unit") {
    return "Enter the unit used for this measurement.";
  }
  return issue.message;
}

interface TerminologyFieldProps {
  error?: string;
  label: string;
  onChange: (concepts: string[]) => void;
  suggestions: string[];
  values: string[];
}

function TerminologyField({
  error,
  label,
  onChange,
  suggestions,
  values,
}: TerminologyFieldProps) {
  const id = useId();
  const [draft, setDraft] = useState("");
  const [entryMessage, setEntryMessage] = useState<string | null>(null);
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const listId = `${id}-suggestions`;

  function addDraft() {
    const concept = normalizeConcept(draft);
    if (!concept) {
      setEntryMessage("Enter a term before adding it.");
      return;
    }
    if (values.includes(concept)) {
      setEntryMessage(`${humanizeConcept(concept)} is already added.`);
      return;
    }
    onChange([...values, concept]);
    setDraft("");
    setEntryMessage(null);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addDraft();
    }
  }

  const displayedError = entryMessage ?? error;

  return (
    <fieldset className={styles.terminologyField} aria-describedby={hintId}>
      <legend>{label}</legend>
      <div className={styles.termEntry}>
        <input
          autoComplete="off"
          aria-describedby={joinIds(hintId, displayedError ? errorId : undefined)}
          aria-invalid={Boolean(displayedError)}
          aria-label={`Add ${label.toLowerCase()}`}
          list={listId}
          onChange={(event) => {
            setDraft(event.target.value);
            setEntryMessage(null);
          }}
          onKeyDown={handleKeyDown}
          placeholder="Type or choose a term"
          type="text"
          value={draft}
        />
        <datalist id={listId}>
          {suggestions
            .filter((suggestion) => !values.includes(suggestion))
            .map((suggestion) => (
              <option key={suggestion} value={humanizeConcept(suggestion)} />
            ))}
        </datalist>
        <button
          aria-label={`Add ${label.toLowerCase()}`}
          className={styles.addButton}
          onClick={addDraft}
          type="button"
        >
          <Plus size={16} aria-hidden="true" />
          Add
        </button>
      </div>
      <p className={styles.fieldHint} id={hintId}>
        Press Enter or comma to add a term.
      </p>
      {values.length ? (
        <ul className={styles.chipList} aria-label={`${label} added`}>
          {values.map((concept) => (
            <li className={styles.chip} key={concept}>
              <span>{humanizeConcept(concept)}</span>
              <button
                aria-label={`Remove ${humanizeConcept(concept)} from ${label.toLowerCase()}`}
                onClick={() => onChange(values.filter((value) => value !== concept))}
                type="button"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.emptyValue}>None recorded</p>
      )}
      {displayedError ? (
        <p className={styles.fieldError} id={errorId}>
          {displayedError}
        </p>
      ) : null}
    </fieldset>
  );
}

interface SelectFieldProps {
  label: string;
  onChange: (value: string | null) => void;
  options: Array<{ label: string; value: string }>;
  value: string | null;
}

function SelectField({ label, onChange, options, value }: SelectFieldProps) {
  const knownValue = value === null || options.some((option) => option.value === value);

  return (
    <label className={styles.field}>
      <span>{label}</span>
      <select onChange={(event) => onChange(event.target.value || null)} value={value ?? ""}>
        <option value="">Not specified</option>
        {!knownValue && value ? (
          <option value={value}>{humanizeConcept(value)} (current value)</option>
        ) : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

interface ChangeItem {
  after: string;
  before: string;
  label: string;
}

function displayNullable(value: string | number | null): string {
  if (value === null || value === "") return "Not specified";
  return typeof value === "number" ? String(value) : humanizeConcept(value);
}

function displayConcepts(values: string[]): string {
  return values.length ? values.map(humanizeConcept).join(", ") : "None";
}

function displayMeasurements(context: ClinicalContext): string {
  if (!context.measurements.length) return "None";
  return context.measurements
    .map((measurement) => {
      const value = Number.isFinite(measurement.value) ? measurement.value : "Value required";
      return `${humanizeConcept(measurement.concept || "Unnamed")} ${value} ${measurement.unit}`.trim();
    })
    .join("; ");
}

function changeSummary(original: ClinicalContext, current: ClinicalContext): ChangeItem[] {
  const fields: Array<{
    label: string;
    read: (context: ClinicalContext) => string;
  }> = [
    { label: "Age", read: (context) => displayNullable(context.age) },
    { label: "Sex", read: (context) => displayNullable(context.sex) },
    { label: "Conditions present", read: (context) => displayConcepts(context.conditions) },
    {
      label: "Conditions absent",
      read: (context) => displayConcepts(context.known_absent_conditions),
    },
    {
      label: "Special populations",
      read: (context) => displayConcepts(context.special_populations),
    },
    {
      label: "Populations absent",
      read: (context) => displayConcepts(context.known_absent_special_populations),
    },
    { label: "Care setting", read: (context) => displayNullable(context.care_setting) },
    { label: "Jurisdiction", read: (context) => displayNullable(context.jurisdiction) },
    { label: "Measurements", read: displayMeasurements },
  ];

  return fields.flatMap((field) => {
    const before = field.read(original);
    const after = field.read(current);
    return before === after ? [] : [{ after, before, label: field.label }];
  });
}

export interface ContextDialogProps {
  context: ClinicalContext;
  onChange: (context: ClinicalContext) => void;
  onOpenChange: (open: boolean) => void;
  onSubmit: (context: ClinicalContext) => Promise<boolean>;
  open: boolean;
  originalContext?: ClinicalContext;
}

export function ContextDialog({
  context,
  onChange,
  onOpenChange,
  onSubmit,
  open,
  originalContext,
}: ContextDialogProps) {
  const measurementListId = useId();
  const unitListId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [showValidationSummary, setShowValidationSummary] = useState(false);
  const [measurementConcepts, setMeasurementConcepts] = useState(() =>
    context.measurements.map((measurement) =>
      measurement.concept.replaceAll("_", " ").toLowerCase(),
    ),
  );
  const [measurementValues, setMeasurementValues] = useState(() =>
    context.measurements.map((measurement) => String(measurement.value)),
  );

  const candidateContext = useMemo<ClinicalContext>(
    () => ({
      ...context,
      measurements: context.measurements.map((measurement, index) => ({
        ...measurement,
        concept: normalizeConcept(measurementConcepts[index] ?? measurement.concept),
        value:
          measurementValues[index]?.trim() === ""
            ? Number.NaN
            : Number(measurementValues[index]),
      })),
    }),
    [context, measurementConcepts, measurementValues],
  );
  const validation = clinicalContextSchema.safeParse(candidateContext);
  const issues = validation.success ? [] : validation.error.issues;
  const changes = originalContext ? changeSummary(originalContext, candidateContext) : [];

  function updateContext(nextContext: ClinicalContext) {
    setSubmitMessage(null);
    onChange(nextContext);
  }

  function updateMeasurement(
    index: number,
    updates: Partial<ClinicalContext["measurements"][number]>,
  ) {
    const measurements = [...context.measurements];
    measurements[index] = {
      ...measurements[index],
      ...updates,
      provenance: "USER_ENTERED",
    };
    updateContext({ ...context, measurements });
  }

  function removeMeasurement(index: number) {
    updateContext({
      ...context,
      measurements: context.measurements.filter((_, itemIndex) => itemIndex !== index),
    });
    setMeasurementValues((values) =>
      values.filter((_, itemIndex) => itemIndex !== index),
    );
    setMeasurementConcepts((values) =>
      values.filter((_, itemIndex) => itemIndex !== index),
    );
  }

  function addMeasurement() {
    updateContext({
      ...context,
      measurements: [
        ...context.measurements,
        { concept: "", provenance: "USER_ENTERED", unit: "", value: 0 },
      ],
    });
    setMeasurementConcepts((values) => [...values, ""]);
    setMeasurementValues((values) => [...values, ""]);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    setShowValidationSummary(true);
    setSubmitMessage(null);
    if (!validation.success) {
      const firstInvalid = formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']");
      firstInvalid?.focus();
      return;
    }

    setIsSubmitting(true);
    try {
      const accepted = await onSubmit(validation.data);
      if (accepted) {
        onOpenChange(false);
      } else {
        setSubmitMessage(
          "We couldn't rerun with these changes. Your edits are still here, so you can try again.",
        );
      }
    } catch {
      setSubmitMessage(
        "The rerun could not be started. Your edits are safe. Check your connection and try again.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  const ageError = meaningfulIssue(issueFor(issues, ["age"]));
  const conditionsError = meaningfulIssue(issueFor(issues, ["conditions"]));
  const absentConditionsError = meaningfulIssue(
    issueFor(issues, ["known_absent_conditions"]),
  );
  const populationsError = meaningfulIssue(issueFor(issues, ["special_populations"]));
  const absentPopulationsError = meaningfulIssue(
    issueFor(issues, ["known_absent_special_populations"]),
  );
  const measurementsError = meaningfulIssue(issueFor(issues, ["measurements"]));

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.backdrop} />
        <Dialog.Content className={styles.dialog}>
          <form noValidate onSubmit={handleSubmit} ref={formRef}>
            <header className={styles.heading}>
              <div>
                <span className={styles.kicker}>Question context</span>
                <Dialog.Title asChild>
                  <h2>Edit interpreted context</h2>
                </Dialog.Title>
              </div>
              <Dialog.Close asChild>
                <button
                  aria-label="Close context editor"
                  className={styles.iconButton}
                  type="button"
                >
                  <X size={18} aria-hidden="true" />
                </button>
              </Dialog.Close>
            </header>

            <Dialog.Description className={styles.description}>
              Correct only facts you can confirm. Changes are validated before the evidence search runs again.
            </Dialog.Description>

            {showValidationSummary && issues.length ? (
              <div className={styles.validationSummary} role="status">
                Review {issues.length === 1 ? "the highlighted field" : `${issues.length} highlighted fields`} before running again.
              </div>
            ) : null}

            <section className={styles.section} aria-labelledby="patient-profile-heading">
              <div className={styles.sectionHeading}>
                <div>
                  <h3 id="patient-profile-heading">Patient profile</h3>
                  <p>Use &quot;Not specified&quot; when the source question does not say.</p>
                </div>
              </div>
              <div className={styles.profileGrid}>
                <label className={styles.field}>
                  <span>Age</span>
                  <input
                    aria-label="Age"
                    autoComplete="off"
                    aria-describedby={ageError ? "context-age-error" : undefined}
                    aria-invalid={Boolean(ageError)}
                    inputMode="numeric"
                    max={130}
                    min={0}
                    onChange={(event) =>
                      updateContext({
                        ...context,
                        age: event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                    step={1}
                    type="number"
                    value={context.age ?? ""}
                  />
                  {ageError ? (
                    <span className={styles.fieldError} id="context-age-error">
                      {ageError}
                    </span>
                  ) : null}
                </label>
                <SelectField
                  label="Sex"
                  onChange={(sex) => updateContext({ ...context, sex })}
                  options={SEX_OPTIONS}
                  value={context.sex}
                />
                <SelectField
                  label="Care setting"
                  onChange={(careSetting) =>
                    updateContext({ ...context, care_setting: careSetting })
                  }
                  options={CARE_SETTING_OPTIONS}
                  value={context.care_setting}
                />
                <SelectField
                  label="Jurisdiction"
                  onChange={(jurisdiction) =>
                    updateContext({ ...context, jurisdiction })
                  }
                  options={JURISDICTION_OPTIONS}
                  value={context.jurisdiction}
                />
              </div>
            </section>

            <section className={styles.section} aria-labelledby="terminology-heading">
              <div className={styles.sectionHeading}>
                <div>
                  <h3 id="terminology-heading">Clinical terminology</h3>
                  <p>Present and explicitly absent facts are kept separate.</p>
                </div>
              </div>
              <div className={styles.terminologyGrid}>
                <TerminologyField
                  error={conditionsError}
                  label="Conditions present"
                  onChange={(conditions) => updateContext({ ...context, conditions })}
                  suggestions={CONDITION_SUGGESTIONS}
                  values={context.conditions}
                />
                <TerminologyField
                  error={absentConditionsError}
                  label="Known absent conditions"
                  onChange={(knownAbsentConditions) =>
                    updateContext({
                      ...context,
                      known_absent_conditions: knownAbsentConditions,
                    })
                  }
                  suggestions={CONDITION_SUGGESTIONS}
                  values={context.known_absent_conditions}
                />
                <TerminologyField
                  error={populationsError}
                  label="Special populations"
                  onChange={(specialPopulations) =>
                    updateContext({ ...context, special_populations: specialPopulations })
                  }
                  suggestions={POPULATION_SUGGESTIONS}
                  values={context.special_populations}
                />
                <TerminologyField
                  error={absentPopulationsError}
                  label="Known absent populations"
                  onChange={(knownAbsentPopulations) =>
                    updateContext({
                      ...context,
                      known_absent_special_populations: knownAbsentPopulations,
                    })
                  }
                  suggestions={POPULATION_SUGGESTIONS}
                  values={context.known_absent_special_populations}
                />
              </div>
            </section>

            <section className={styles.section} aria-labelledby="measurements-heading">
              <div className={styles.sectionHeading}>
                <div>
                  <h3 id="measurements-heading">Measurements</h3>
                  <p>Keep the value and unit exactly as clinically documented.</p>
                </div>
                <button className={styles.outlineButton} onClick={addMeasurement} type="button">
                  <Plus size={16} aria-hidden="true" />
                  Add measurement
                </button>
              </div>
              <datalist id={measurementListId}>
                {MEASUREMENT_SUGGESTIONS.map((suggestion) => (
                  <option key={suggestion} value={humanizeConcept(suggestion)} />
                ))}
              </datalist>
              <datalist id={unitListId}>
                {UNIT_SUGGESTIONS.map((suggestion) => (
                  <option key={suggestion} value={suggestion} />
                ))}
              </datalist>

              {context.measurements.length ? (
                <div className={styles.measurementList}>
                  {context.measurements.map((measurement, index) => {
                    const conceptError = meaningfulIssue(
                      issueFor(issues, ["measurements", index, "concept"]),
                    );
                    const valueError = meaningfulIssue(
                      issueFor(issues, ["measurements", index, "value"]),
                    );
                    const unitError = meaningfulIssue(
                      issueFor(issues, ["measurements", index, "unit"]),
                    );
                    const conceptErrorId = `measurement-${index}-concept-error`;
                    const valueErrorId = `measurement-${index}-value-error`;
                    const unitErrorId = `measurement-${index}-unit-error`;
                    const measurementName = measurement.concept
                      ? humanizeConcept(measurement.concept)
                      : `${index + 1}`;

                    return (
                      <fieldset className={styles.measurementCard} key={index}>
                        <legend>Measurement {index + 1}</legend>
                        <button
                          aria-label={`Remove ${measurementName} measurement`}
                          className={styles.removeMeasurement}
                          onClick={() => removeMeasurement(index)}
                          type="button"
                        >
                          <Trash2 size={16} aria-hidden="true" />
                          <span>Remove</span>
                        </button>
                        <div className={styles.measurementGrid}>
                          <label className={styles.field}>
                            <span>Concept</span>
                            <input
                              aria-label="Concept"
                              aria-describedby={joinIds(
                                conceptError ? conceptErrorId : undefined,
                                measurementsError ? "context-measurements-error" : undefined,
                              )}
                              aria-invalid={Boolean(conceptError || measurementsError)}
                              autoComplete="off"
                              list={measurementListId}
                              onChange={(event) => {
                                const nextConcept = event.target.value;
                                setSubmitMessage(null);
                                setMeasurementConcepts((values) =>
                                  values.map((value, itemIndex) =>
                                    itemIndex === index ? nextConcept : value,
                                  ),
                                );
                                updateMeasurement(index, {
                                  concept: normalizeConcept(nextConcept),
                                });
                              }}
                              placeholder="e.g. eGFR"
                              type="text"
                              value={measurementConcepts[index] ?? ""}
                            />
                            {conceptError ? (
                              <span className={styles.fieldError} id={conceptErrorId}>
                                {conceptError}
                              </span>
                            ) : null}
                          </label>
                          <label className={styles.field}>
                            <span>Value</span>
                            <input
                              aria-label="Value"
                              aria-describedby={valueError ? valueErrorId : undefined}
                              aria-invalid={Boolean(valueError)}
                              autoComplete="off"
                              inputMode="decimal"
                              onChange={(event) => {
                                const nextValue = event.target.value;
                                setSubmitMessage(null);
                                setMeasurementValues((values) =>
                                  values.map((value, itemIndex) =>
                                    itemIndex === index ? nextValue : value,
                                  ),
                                );
                                if (nextValue.trim() && Number.isFinite(Number(nextValue))) {
                                  updateMeasurement(index, { value: Number(nextValue) });
                                }
                              }}
                              step="any"
                              type="number"
                              value={measurementValues[index] ?? ""}
                            />
                            {valueError ? (
                              <span className={styles.fieldError} id={valueErrorId}>
                                {valueError}
                              </span>
                            ) : null}
                          </label>
                          <label className={styles.field}>
                            <span>Unit</span>
                            <input
                              aria-label="Unit"
                              aria-describedby={unitError ? unitErrorId : undefined}
                              aria-invalid={Boolean(unitError)}
                              autoComplete="off"
                              list={unitListId}
                              onChange={(event) =>
                                updateMeasurement(index, { unit: event.target.value })
                              }
                              placeholder="e.g. mg/dL"
                              type="text"
                              value={measurement.unit}
                            />
                            {unitError ? (
                              <span className={styles.fieldError} id={unitErrorId}>
                                {unitError}
                              </span>
                            ) : null}
                          </label>
                        </div>
                      </fieldset>
                    );
                  })}
                </div>
              ) : (
                <div className={styles.emptyMeasurements}>No measurements recorded.</div>
              )}
              {measurementsError ? (
                <p className={styles.sectionError} id="context-measurements-error">
                  {measurementsError}
                </p>
              ) : null}
            </section>

            {originalContext ? (
              <section className={styles.changeSummary} aria-labelledby="context-changes-heading">
                <h3 id="context-changes-heading">Changes to review</h3>
                {changes.length ? (
                  <ul>
                    {changes.map((change) => (
                      <li key={change.label}>
                        <strong>{change.label}</strong>
                        <span>{change.before}</span>
                        <ArrowRight size={14} aria-label="changed to" />
                        <span>{change.after}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>No changes yet.</p>
                )}
              </section>
            ) : null}

            {submitMessage ? (
              <div aria-live="polite" className={styles.submitMessage} role="status">
                {submitMessage}
              </div>
            ) : null}

            <footer className={styles.actions}>
              <Dialog.Close asChild>
                <button className={styles.secondaryButton} type="button">
                  Cancel
                </button>
              </Dialog.Close>
              <button
                className={styles.primaryButton}
                disabled={isSubmitting}
                type="submit"
              >
                <RotateCw
                  className={isSubmitting ? styles.spin : undefined}
                  size={16}
                  aria-hidden="true"
                />
                {isSubmitting ? "Running..." : "Run again"}
              </button>
            </footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
