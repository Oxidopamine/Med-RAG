"use client";

import { useEffect, useMemo, useState } from "react";

import shellStyles from "@/components/shell/shell.module.css";
import { downloadText } from "@/lib/export";
import {
  ARMS,
  AGREEMENT_LABELS,
  ATTRIBUTION_LABELS,
  ELIGIBILITY_CONDITIONS,
  MISLEAD_EXTENTS,
  MISLEAD_LIKELIHOODS,
  Census,
  abstentionItems,
  agreementItems,
  attributionRecords,
  clearLabels,
  emptyLabelFile,
  labelKey,
  loadLabels,
  misleadItems,
  passProgress,
  questionValidityItems,
  saveLabels,
  withTextSourceCounts,
  type AbstentionItem,
  type AgreementItem,
  type AgreementLabel,
  type Arm,
  type AttributionLabel,
  type AttributionRecord,
  type ClaimLabel,
  type LabelFile,
  type MisleadItem,
  type QuestionSet,
  type RunFile,
} from "@/lib/labelling";

import styles from "./labelling-workbench.module.css";

type AbstainedEntry = {
  any_retrieved_passage_answers: boolean | null;
  question_valid: boolean | null;
  note?: string;
};

type LoadedFile<T> = { name: string; size: number; data: T; summary: string };
type BundleFile = { name: string; size: number; summary: string; renderFull: (evidenceId: string) => string | null };

type SlotKind = "production" | "closed_book" | "naive" | "questions" | "bundle" | "labels";
type RunArm = "production" | "closed_book" | "naive";

function humanize(value: string): string {
  const text = value.toLowerCase().replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function clamp(index: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(Math.max(index, 0), total - 1);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * What a dropped or picked file looks like, read the way `lib/labelling.ts` reads it: a
 * run file carries a `results` array, a question set an `items` array, a release bundle
 * an `evidence` array, and a label file `schema_version: 1` with a `records` object. The
 * three run files share a shape, so the caller still needs the filename to tell them apart.
 */
function shapeKind(json: unknown): "run" | "questions" | "bundle" | "labels" | null {
  if (!json || typeof json !== "object") return null;
  const obj = json as Record<string, unknown>;
  if (obj.schema_version === 1 && typeof obj.records === "object" && obj.records !== null) return "labels";
  if (Array.isArray(obj.results)) return "run";
  if (Array.isArray(obj.items)) return "questions";
  if (Array.isArray(obj.evidence)) return "bundle";
  return null;
}

/**
 * Which run arm a run-shaped file belongs to: the filename first, since it is the only
 * signal that distinguishes production from closed-book from naive; failing that, the
 * first arm slot still empty, so an anonymous batch of three still lands somewhere sane.
 */
function armForRunFile(name: string, filled: Record<RunArm, boolean>): RunArm {
  const lower = name.toLowerCase();
  if (/naive/.test(lower)) return "naive";
  if (/closed[_-]?book/.test(lower)) return "closed_book";
  if (/production/.test(lower)) return "production";
  if (!filled.production) return "production";
  if (!filled.closed_book) return "closed_book";
  if (!filled.naive) return "naive";
  return "production";
}

function findClaim(records: LabelFile["records"], key: string, index: number): ClaimLabel | null {
  return records[key]?.claims.find((claim) => claim.index === index) ?? null;
}

function stampStarted(passes: LabelFile["passes"], pass: keyof LabelFile["passes"]): LabelFile["passes"] {
  if (passes[pass].started_at !== null) return passes;
  return { ...passes, [pass]: { ...passes[pass], started_at: new Date().toISOString() } };
}

function maybeFinishAgreement(current: LabelFile): LabelFile {
  if (current.passes.agreement.finished_at !== null) return current;
  const records = Object.values(current.records);
  const agreementDone = records.every((record) => record.claims.every((claim) => claim.agreement !== null));
  const questionValidDone = records.every((record) => record.question_valid !== null);
  if (agreementDone && questionValidDone) {
    return {
      ...current,
      passes: { ...current.passes, agreement: { ...current.passes.agreement, finished_at: new Date().toISOString() } },
    };
  }
  return current;
}

/** j/n advance, k/p go back, but never while the annotator is typing in a field. */
function useKeyboardNav(active: boolean, onNext: () => void, onPrev: () => void): void {
  useEffect(() => {
    if (!active) return;
    function handleKeydown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return;
      if (event.key === "j" || event.key === "n") onNext();
      else if (event.key === "k" || event.key === "p") onPrev();
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [active, onNext, onPrev]);
}

function NavBar({
  index,
  total,
  onPrev,
  onNext,
  onJump,
  jumpDisabled,
}: {
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onJump: () => void;
  jumpDisabled: boolean;
}) {
  return (
    <div className={styles.navBar}>
      <p aria-live="polite" className={shellStyles.muted}>
        Item {total ? index + 1 : 0} of {total}
      </p>
      <div>
        <button className={shellStyles.button} disabled={index <= 0} onClick={onPrev} type="button">
          Previous
        </button>
        <button className={shellStyles.button} disabled={index >= total - 1} onClick={onNext} type="button">
          Next
        </button>
        <button className={shellStyles.button} disabled={jumpDisabled} onClick={onJump} type="button">
          Jump to first unlabelled
        </button>
      </div>
      <p className={styles.hint}>Keyboard: j or n for next, k or p for previous, outside a text field.</p>
    </div>
  );
}

function RadioGroup({
  legend,
  name,
  options,
  value,
  onChange,
}: {
  legend: string;
  name: string;
  options: readonly string[];
  value: string | null;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend>{legend}</legend>
      <div className={`${shellStyles.choices} ${styles.choicesInline}`}>
        {options.map((option) => (
          <label className={shellStyles.choice} key={option}>
            <input checked={value === option} name={name} onChange={() => onChange(option)} type="radio" value={option} />
            <span className={shellStyles["choice-body"]}>
              <strong>{humanize(option)}</strong>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function YesNoGroup({
  legend,
  name,
  value,
  onChange,
}: {
  legend: string;
  name: string;
  value: boolean | null;
  onChange: (value: boolean) => void;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend>{legend}</legend>
      <div className={`${shellStyles.choices} ${styles.choicesInline}`}>
        <label className={shellStyles.choice}>
          <input checked={value === true} name={name} onChange={() => onChange(true)} type="radio" value="yes" />
          <span className={shellStyles["choice-body"]}>
            <strong>Yes</strong>
          </span>
        </label>
        <label className={shellStyles.choice}>
          <input checked={value === false} name={name} onChange={() => onChange(false)} type="radio" value="no" />
          <span className={shellStyles["choice-body"]}>
            <strong>No</strong>
          </span>
        </label>
      </div>
    </fieldset>
  );
}

function EligibilitySelect({ value, onChange }: { value: string | null; onChange: (value: string) => void }) {
  const current = value ?? "";
  const known = ELIGIBILITY_CONDITIONS as readonly string[];
  const isOther = current !== "" && !known.includes(current);
  const selectValue = isOther ? "OTHER" : current;
  const otherText = isOther ? current.replace(/^OTHER:?/, "") : "";
  return (
    <div className={shellStyles.field}>
      <label htmlFor="eligibility-condition">Eligibility condition</label>
      <select
        id="eligibility-condition"
        onChange={(event) => {
          const next = event.target.value;
          onChange(next === "OTHER" ? "OTHER:" : next);
        }}
        value={selectValue}
      >
        <option value="">Not applicable</option>
        {ELIGIBILITY_CONDITIONS.map((condition) => (
          <option key={condition} value={condition}>
            {humanize(condition)}
          </option>
        ))}
        <option value="OTHER">Other</option>
      </select>
      {selectValue === "OTHER" && (
        <input
          aria-label="Other eligibility condition"
          onChange={(event) => onChange(`OTHER:${event.target.value}`)}
          type="text"
          value={otherText}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Setup: the drop zone and the slot list
// ---------------------------------------------------------------------------------------

const SLOTS: Array<{ kind: SlotKind; title: string; required: boolean; hint: string }> = [
  {
    kind: "production",
    title: "Production run",
    required: true,
    hint: "The arm every label file is built from. Required to start the census.",
  },
  {
    kind: "closed_book",
    title: "Closed-book run",
    required: false,
    hint: "Adds the closed-book arm to Pass A's concealed comparison.",
  },
  {
    kind: "naive",
    title: "Naive run",
    required: false,
    hint: "Adds the naive arm to Pass B's attribution records.",
  },
  {
    kind: "questions",
    title: "Question set",
    required: false,
    hint: "Supplies each question's gold statement.",
  },
  {
    kind: "bundle",
    title: "Release bundle",
    required: false,
    hint: "Supplies full passage text; without it, the census falls back to the run file and is not valid.",
  },
  {
    kind: "labels",
    title: "Existing label file",
    required: false,
    hint: "Resumes labelling from a label file already in progress.",
  },
];

/** A single bordered drop target, with a hidden multi-file input for click and keyboard. */
function FileDropZone({ onFiles }: { onFiles: (files: FileList) => void }) {
  const [dragActive, setDragActive] = useState(false);

  return (
    <label
      className={`${styles.dropzone} ${dragActive ? styles.dropzoneActive : ""}`}
      htmlFor="census-file-input"
      onDragLeave={() => setDragActive(false)}
      onDragOver={(event) => {
        event.preventDefault();
        setDragActive(true);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        if (event.dataTransfer.files.length) onFiles(event.dataTransfer.files);
      }}
    >
      <strong>Drop census files here</strong>
      <span className={shellStyles.muted}>
        or choose files. Several at once is fine: each is routed to the slot it matches.
      </span>
      <input
        accept="application/json"
        className={shellStyles.srOnly}
        id="census-file-input"
        multiple
        onChange={(event) => {
          if (event.target.files?.length) onFiles(event.target.files);
          event.target.value = "";
        }}
        type="file"
      />
    </label>
  );
}

function SlotRow({
  slot,
  loaded,
  onClear,
}: {
  slot: { kind: SlotKind; title: string; required: boolean; hint: string };
  loaded: { name: string; size: number; summary: string } | null;
  onClear: () => void;
}) {
  return (
    <li className={styles.slotRow}>
      <div className={styles.slotInfo}>
        <p className={styles.slotTitle}>
          {slot.title}
          {slot.required && <span className={styles.requiredMark}> (required)</span>}
        </p>
        <p className={styles.slotHint}>{slot.hint}</p>
      </div>
      <div className={styles.slotState}>
        {loaded ? (
          <>
            <p className={styles.slotFile}>
              {loaded.name}
              <span className={shellStyles.muted}>
                {" "}
                {formatBytes(loaded.size)}, {loaded.summary}
              </span>
            </p>
            <span className={`${shellStyles.status} ${shellStyles.ok}`}>Loaded</span>
            <button className={`${shellStyles.button} ${shellStyles.small}`} onClick={onClear} type="button">
              Clear
            </button>
          </>
        ) : (
          <span className={`${shellStyles.status} ${slot.required ? shellStyles.warn : shellStyles.neutral}`}>
            {slot.required ? "Required, not loaded" : "Not loaded"}
          </span>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------------------
// Pass A: agreement
// ---------------------------------------------------------------------------------------

function PassAgreement({
  items,
  questionItems,
  records,
  onUpdateClaim,
  onUpdateQuestion,
}: {
  items: AgreementItem[];
  questionItems: ReturnType<typeof questionValidityItems>;
  records: LabelFile["records"];
  onUpdateClaim: (
    key: string,
    index: number,
    patch: Partial<Pick<ClaimLabel, "agreement" | "eligibility_drop" | "eligibility_condition" | "note">>,
  ) => void;
  onUpdateQuestion: (keys: string[], patch: { question_valid?: boolean; note?: string }) => void;
}) {
  const [section, setSection] = useState<"claims" | "questions">("claims");
  const [claimIndex, setClaimIndex] = useState(0);
  const [questionIndex, setQuestionIndex] = useState(0);

  const claimTotal = items.length;
  const questionTotal = questionItems.length;
  const boundedClaimIndex = clamp(claimIndex, claimTotal);
  const boundedQuestionIndex = clamp(questionIndex, questionTotal);

  function claimLabelled(item: AgreementItem): boolean {
    const claim = findClaim(records, item.key, item.index);
    return claim ? claim.agreement !== null : false;
  }
  function questionLabelled(item: (typeof questionItems)[number]): boolean {
    const record = records[item.keys[0] ?? ""];
    return record ? record.question_valid !== null : false;
  }

  useKeyboardNav(
    section === "claims",
    () => setClaimIndex((i) => Math.min(claimTotal - 1, i + 1)),
    () => setClaimIndex((i) => Math.max(0, i - 1)),
  );
  useKeyboardNav(
    section === "questions",
    () => setQuestionIndex((i) => Math.min(questionTotal - 1, i + 1)),
    () => setQuestionIndex((i) => Math.max(0, i - 1)),
  );

  const claimItem = items[boundedClaimIndex] ?? null;
  const claimLabel = claimItem ? findClaim(records, claimItem.key, claimItem.index) : null;
  const questionItem = questionItems[boundedQuestionIndex] ?? null;
  const questionRecord = questionItem ? records[questionItem.keys[0] ?? ""] ?? null : null;

  return (
    <div>
      <div className={styles.subNav}>
        <button aria-pressed={section === "claims"} className={styles.subNavButton} onClick={() => setSection("claims")} type="button">
          Claims
        </button>
        <button
          aria-pressed={section === "questions"}
          className={styles.subNavButton}
          onClick={() => setSection("questions")}
          type="button"
        >
          Question validity
        </button>
      </div>

      {section === "claims" && claimItem && claimLabel && (
        <div className={styles.grid}>
          <div className={styles.item}>
            <p className={styles.itemId}>{claimItem.id}</p>
            <p className={styles.question}>{claimItem.question}</p>
            <p className={styles.gold}>{claimItem.gold}</p>
            <p className={`reading ${styles.claimText}`}>{claimItem.claimText}</p>
          </div>
          <div className={styles.controls}>
            <NavBar
              index={boundedClaimIndex}
              jumpDisabled={items.every(claimLabelled)}
              onJump={() => {
                const idx = items.findIndex((item) => !claimLabelled(item));
                if (idx !== -1) setClaimIndex(idx);
              }}
              onNext={() => setClaimIndex((i) => Math.min(claimTotal - 1, i + 1))}
              onPrev={() => setClaimIndex((i) => Math.max(0, i - 1))}
              total={claimTotal}
            />
            <RadioGroup
              legend="Agreement with the gold statement"
              name={`agreement-${claimItem.id}`}
              onChange={(value) => onUpdateClaim(claimItem.key, claimItem.index, { agreement: value as AgreementLabel })}
              options={AGREEMENT_LABELS}
              value={claimLabel.agreement}
            />
            <YesNoGroup
              legend="Does the claim drop an eligibility condition?"
              name={`eligibility-drop-${claimItem.id}`}
              onChange={(value) => onUpdateClaim(claimItem.key, claimItem.index, { eligibility_drop: value })}
              value={claimLabel.eligibility_drop}
            />
            <EligibilitySelect
              onChange={(value) => onUpdateClaim(claimItem.key, claimItem.index, { eligibility_condition: value })}
              value={claimLabel.eligibility_condition}
            />
            <div className={shellStyles.field}>
              <label htmlFor={`agreement-note-${claimItem.id}`}>Note</label>
              <textarea
                className={styles.textarea}
                id={`agreement-note-${claimItem.id}`}
                onChange={(event) => onUpdateClaim(claimItem.key, claimItem.index, { note: event.target.value })}
                value={claimLabel.note}
              />
            </div>
          </div>
        </div>
      )}

      {section === "questions" && questionItem && questionRecord && (
        <div className={styles.grid}>
          <div className={styles.item}>
            <p className={styles.question}>{questionItem.question}</p>
            <p className={styles.gold}>{questionItem.gold}</p>
          </div>
          <div className={styles.controls}>
            <NavBar
              index={boundedQuestionIndex}
              jumpDisabled={questionItems.every(questionLabelled)}
              onJump={() => {
                const idx = questionItems.findIndex((item) => !questionLabelled(item));
                if (idx !== -1) setQuestionIndex(idx);
              }}
              onNext={() => setQuestionIndex((i) => Math.min(questionTotal - 1, i + 1))}
              onPrev={() => setQuestionIndex((i) => Math.max(0, i - 1))}
              total={questionTotal}
            />
            <YesNoGroup
              legend="Is the question valid?"
              name={`question-valid-${questionItem.questionId}`}
              onChange={(value) => onUpdateQuestion(questionItem.keys, { question_valid: value })}
              value={questionRecord.question_valid}
            />
            <div className={shellStyles.field}>
              <label htmlFor={`question-note-${questionItem.questionId}`}>Note</label>
              <textarea
                className={styles.textarea}
                id={`question-note-${questionItem.questionId}`}
                onChange={(event) => onUpdateQuestion(questionItem.keys, { note: event.target.value })}
                value={questionRecord.note}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Pass B: attribution
// ---------------------------------------------------------------------------------------

function AbstentionItemView({
  item,
  index,
  total,
  labels,
  onUpdateGateBlocked,
  onUpdateAbstained,
  onNext,
  onPrev,
  onJump,
  jumpDisabled,
}: {
  item: AbstentionItem;
  index: number;
  total: number;
  labels: LabelFile;
  onUpdateGateBlocked: (questionId: string, patch: Partial<{ gate_right: boolean; dak_has_answer: boolean; note: string }>) => void;
  onUpdateAbstained: (
    questionId: string,
    patch: Partial<{ any_retrieved_passage_answers: boolean; question_valid: boolean; note: string }>,
  ) => void;
  onNext: () => void;
  onPrev: () => void;
  onJump: () => void;
  jumpDisabled: boolean;
}) {
  const gateEntry = labels.gate_blocked[item.questionId] ?? null;
  const sampleEntryRaw = labels.abstained_sample[item.questionId];
  const sampleEntry = sampleEntryRaw && typeof sampleEntryRaw === "object" ? (sampleEntryRaw as AbstainedEntry) : null;
  return (
    <div className={styles.grid}>
      <div className={styles.item}>
        {item.chapter ? <p className={shellStyles.muted}>{item.chapter}</p> : null}
        <p className={styles.question}>{item.question}</p>
        <p className={styles.gold}>{item.gold}</p>
        <p className={shellStyles.muted}>
          {item.gateBlocked ? `Gate reason: ${item.gateReason ?? "not stated"}` : `Model said: ${item.modelSaid || "(nothing recorded)"}`}
        </p>
        {item.passages.map((passage) => (
          <div className={styles.passage} key={passage.evidence_id}>
            <div className={styles.passageHead}>
              <span>{passage.evidence_id}</span>
              {passage.text_source === "run" && (
                <span className={styles.passageMark}>[text from the run file, possibly truncated]</span>
              )}
            </div>
            <p className="reading">{passage.rendered_text}</p>
          </div>
        ))}
      </div>
      <div className={styles.controls}>
        <NavBar index={index} jumpDisabled={jumpDisabled} onJump={onJump} onNext={onNext} onPrev={onPrev} total={total} />
        {item.gateBlocked && gateEntry ? (
          <>
            <YesNoGroup
              legend="Was the gate right to block this question?"
              name={`gate-right-${item.questionId}`}
              onChange={(value) => onUpdateGateBlocked(item.questionId, { gate_right: value })}
              value={gateEntry.gate_right}
            />
            <YesNoGroup
              legend="Does the digital adaptation kit have an answer?"
              name={`dak-${item.questionId}`}
              onChange={(value) => onUpdateGateBlocked(item.questionId, { dak_has_answer: value })}
              value={gateEntry.dak_has_answer}
            />
            <div className={shellStyles.field}>
              <label htmlFor={`gate-note-${item.questionId}`}>Note</label>
              <textarea
                className={styles.textarea}
                id={`gate-note-${item.questionId}`}
                onChange={(event) => onUpdateGateBlocked(item.questionId, { note: event.target.value })}
                value={gateEntry.note}
              />
            </div>
          </>
        ) : sampleEntry ? (
          <>
            <YesNoGroup
              legend="Does any retrieved passage answer the question?"
              name={`any-answer-${item.questionId}`}
              onChange={(value) => onUpdateAbstained(item.questionId, { any_retrieved_passage_answers: value })}
              value={sampleEntry.any_retrieved_passage_answers}
            />
            <YesNoGroup
              legend="Is the question valid?"
              name={`sample-valid-${item.questionId}`}
              onChange={(value) => onUpdateAbstained(item.questionId, { question_valid: value })}
              value={sampleEntry.question_valid}
            />
            <div className={shellStyles.field}>
              <label htmlFor={`sample-note-${item.questionId}`}>Note</label>
              <textarea
                className={styles.textarea}
                id={`sample-note-${item.questionId}`}
                onChange={(event) => onUpdateAbstained(item.questionId, { note: event.target.value })}
                value={sampleEntry.note ?? ""}
              />
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

function PassAttribution({
  records,
  abstentionItems: abstentionList,
  labels,
  onUpdatePair,
  onUpdateJoint,
  onUpdateNote,
  onUpdatePresentationDefect,
  onUpdateRecordNote,
  onUpdateGateBlocked,
  onUpdateAbstained,
}: {
  records: AttributionRecord[];
  abstentionItems: AbstentionItem[];
  labels: LabelFile;
  onUpdatePair: (key: string, index: number, evidenceId: string, value: AttributionLabel) => void;
  onUpdateJoint: (key: string, index: number, value: AttributionLabel) => void;
  onUpdateNote: (key: string, index: number, note: string) => void;
  onUpdatePresentationDefect: (key: string, value: boolean) => void;
  onUpdateRecordNote: (key: string, note: string) => void;
  onUpdateGateBlocked: (questionId: string, patch: Partial<{ gate_right: boolean; dak_has_answer: boolean; note: string }>) => void;
  onUpdateAbstained: (
    questionId: string,
    patch: Partial<{ any_retrieved_passage_answers: boolean; question_valid: boolean; note: string }>,
  ) => void;
}) {
  const [section, setSection] = useState<"records" | "abstention">("records");
  const [recordIndex, setRecordIndex] = useState(0);
  const [abstentionIndex, setAbstentionIndex] = useState(0);

  const recordTotal = records.length;
  const abstentionTotal = abstentionList.length;
  const boundedRecordIndex = clamp(recordIndex, recordTotal);
  const boundedAbstentionIndex = clamp(abstentionIndex, abstentionTotal);

  function recordLabelled(candidate: AttributionRecord): boolean {
    const label = labels.records[candidate.key];
    if (!label) return false;
    return label.presentation_defect !== null && label.claims.every((claim) => claim.joint_attribution !== null);
  }
  function abstentionLabelled(item: AbstentionItem): boolean {
    if (item.gateBlocked) {
      const entry = labels.gate_blocked[item.questionId];
      return entry ? entry.gate_right !== null : false;
    }
    const entry = labels.abstained_sample[item.questionId];
    return entry && typeof entry === "object" ? (entry as AbstainedEntry).any_retrieved_passage_answers !== null : false;
  }

  useKeyboardNav(
    section === "records",
    () => setRecordIndex((i) => Math.min(recordTotal - 1, i + 1)),
    () => setRecordIndex((i) => Math.max(0, i - 1)),
  );
  useKeyboardNav(
    section === "abstention",
    () => setAbstentionIndex((i) => Math.min(abstentionTotal - 1, i + 1)),
    () => setAbstentionIndex((i) => Math.max(0, i - 1)),
  );

  const record = records[boundedRecordIndex] ?? null;
  const recordLabel = record ? labels.records[record.key] ?? null : null;
  const abstentionItem = abstentionList[boundedAbstentionIndex] ?? null;

  return (
    <div>
      <div className={styles.subNav}>
        <button aria-pressed={section === "records"} className={styles.subNavButton} onClick={() => setSection("records")} type="button">
          Records
        </button>
        <button
          aria-pressed={section === "abstention"}
          className={styles.subNavButton}
          onClick={() => setSection("abstention")}
          type="button"
        >
          Abstention
        </button>
      </div>

      {section === "records" && record && recordLabel && (
        <div className={styles.grid}>
          <div className={styles.item}>
            <p className={styles.itemId}>{record.key}</p>
            {record.chapter ? <p className={shellStyles.muted}>{record.chapter}</p> : null}
            <p className={styles.question}>{record.question}</p>
            {record.claims.map((claim) => {
              const claimLabel = recordLabel.claims.find((entry) => entry.index === claim.index);
              if (!claimLabel) return null;
              return (
                <div className={styles.claim} key={claim.index}>
                  <p className={`reading ${styles.claimText}`}>{claim.text}</p>
                  {claim.cited.map((evidenceId) => {
                    const passage = record.passages.get(evidenceId);
                    return (
                      <div className={styles.passage} key={evidenceId}>
                        <div className={styles.passageHead}>
                          <span>{evidenceId}</span>
                          {!passage && <span className={styles.passageMark}>[NOT IN RETRIEVED SET]</span>}
                          {passage?.text_source === "run" && (
                            <span className={styles.passageMark}>[text from the run file, possibly truncated]</span>
                          )}
                        </div>
                        {passage && <p className="reading">{passage.rendered_text}</p>}
                        <RadioGroup
                          legend={`Attribution of ${evidenceId}`}
                          name={`pair-${record.key}-${claim.index}-${evidenceId}`}
                          onChange={(value) => onUpdatePair(record.key, claim.index, evidenceId, value as AttributionLabel)}
                          options={ATTRIBUTION_LABELS}
                          value={claimLabel.pair_attribution[evidenceId] ?? null}
                        />
                      </div>
                    );
                  })}
                  <RadioGroup
                    legend="Joint attribution"
                    name={`joint-${record.key}-${claim.index}`}
                    onChange={(value) => onUpdateJoint(record.key, claim.index, value as AttributionLabel)}
                    options={ATTRIBUTION_LABELS}
                    value={claimLabel.joint_attribution}
                  />
                  <div className={shellStyles.field}>
                    <label htmlFor={`claim-note-${record.key}-${claim.index}`}>Note</label>
                    <textarea
                      className={styles.textarea}
                      id={`claim-note-${record.key}-${claim.index}`}
                      onChange={(event) => onUpdateNote(record.key, claim.index, event.target.value)}
                      value={claimLabel.note}
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <div className={styles.controls}>
            <NavBar
              index={boundedRecordIndex}
              jumpDisabled={records.every(recordLabelled)}
              onJump={() => {
                const idx = records.findIndex((candidate) => !recordLabelled(candidate));
                if (idx !== -1) setRecordIndex(idx);
              }}
              onNext={() => setRecordIndex((i) => Math.min(recordTotal - 1, i + 1))}
              onPrev={() => setRecordIndex((i) => Math.max(0, i - 1))}
              total={recordTotal}
            />
            <YesNoGroup
              legend="Presentation defect?"
              name={`presentation-defect-${record.key}`}
              onChange={(value) => onUpdatePresentationDefect(record.key, value)}
              value={recordLabel.presentation_defect}
            />
            <div className={shellStyles.field}>
              <label htmlFor={`record-note-${record.key}`}>Record note</label>
              <textarea
                className={styles.textarea}
                id={`record-note-${record.key}`}
                onChange={(event) => onUpdateRecordNote(record.key, event.target.value)}
                value={recordLabel.note}
              />
            </div>
          </div>
        </div>
      )}

      {section === "abstention" && abstentionItem && (
        <AbstentionItemView
          index={boundedAbstentionIndex}
          item={abstentionItem}
          jumpDisabled={abstentionList.every(abstentionLabelled)}
          labels={labels}
          onJump={() => {
            const idx = abstentionList.findIndex((candidate) => !abstentionLabelled(candidate));
            if (idx !== -1) setAbstentionIndex(idx);
          }}
          onNext={() => setAbstentionIndex((i) => Math.min(abstentionTotal - 1, i + 1))}
          onPrev={() => setAbstentionIndex((i) => Math.max(0, i - 1))}
          onUpdateAbstained={onUpdateAbstained}
          onUpdateGateBlocked={onUpdateGateBlocked}
          total={abstentionTotal}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Pass C: mislead
// ---------------------------------------------------------------------------------------

function PassMislead({
  items,
  labels,
  onUpdate,
}: {
  items: MisleadItem[];
  labels: LabelFile;
  onUpdate: (key: string, index: number, patch: { extent: string; likelihood: string; reason: string }) => void;
}) {
  const [index, setIndex] = useState(0);
  const total = items.length;
  const bounded = clamp(index, total);
  const item = items[bounded] ?? null;
  const claim = item ? findClaim(labels.records, item.key, item.index) : null;

  function isLabelled(candidate: MisleadItem): boolean {
    const claimLabel = findClaim(labels.records, candidate.key, candidate.index);
    return claimLabel ? claimLabel.mislead !== null : false;
  }

  useKeyboardNav(
    true,
    () => setIndex((i) => Math.min(total - 1, i + 1)),
    () => setIndex((i) => Math.max(0, i - 1)),
  );

  if (!item || !claim) return <p className={shellStyles.empty}>Nothing to label yet.</p>;
  const mislead = claim.mislead;

  return (
    <div className={styles.grid}>
      <div className={styles.item}>
        <p className={styles.itemId}>{item.id}</p>
        <p className={styles.question}>{item.question}</p>
        <p className={styles.gold}>{item.gold}</p>
        <p className={`reading ${styles.claimText}`}>{item.claimText}</p>
      </div>
      <div className={styles.controls}>
        <NavBar
          index={bounded}
          jumpDisabled={items.every(isLabelled)}
          onJump={() => {
            const idx = items.findIndex((candidate) => !isLabelled(candidate));
            if (idx !== -1) setIndex(idx);
          }}
          onNext={() => setIndex((i) => Math.min(total - 1, i + 1))}
          onPrev={() => setIndex((i) => Math.max(0, i - 1))}
          total={total}
        />
        <RadioGroup
          legend="Extent"
          name={`mislead-extent-${item.id}`}
          onChange={(value) =>
            onUpdate(item.key, item.index, { extent: value, likelihood: mislead?.likelihood ?? "", reason: mislead?.reason ?? "" })
          }
          options={MISLEAD_EXTENTS}
          value={mislead?.extent ?? null}
        />
        <RadioGroup
          legend="Likelihood"
          name={`mislead-likelihood-${item.id}`}
          onChange={(value) =>
            onUpdate(item.key, item.index, { extent: mislead?.extent ?? "", likelihood: value, reason: mislead?.reason ?? "" })
          }
          options={MISLEAD_LIKELIHOODS}
          value={mislead?.likelihood ?? null}
        />
        <div className={shellStyles.field}>
          <label htmlFor={`mislead-reason-${item.id}`}>Reason</label>
          <textarea
            className={styles.textarea}
            id={`mislead-reason-${item.id}`}
            onChange={(event) =>
              onUpdate(item.key, item.index, {
                extent: mislead?.extent ?? "",
                likelihood: mislead?.likelihood ?? "",
                reason: event.target.value,
              })
            }
            value={mislead?.reason ?? ""}
          />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// The workbench
// ---------------------------------------------------------------------------------------

export function LabellingWorkbench({
  initialFiles,
}: {
  initialFiles?: { production: RunFile; questions?: QuestionSet };
} = {}) {
  const [productionFile, setProductionFile] = useState<LoadedFile<RunFile> | null>(() =>
    initialFiles?.production
      ? {
          name: "production.json",
          size: 0,
          data: initialFiles.production,
          summary: `${initialFiles.production.results.length} records`,
        }
      : null,
  );
  const [closedBookFile, setClosedBookFile] = useState<LoadedFile<RunFile> | null>(null);
  const [naiveFile, setNaiveFile] = useState<LoadedFile<RunFile> | null>(null);
  const [questionFile, setQuestionFile] = useState<LoadedFile<QuestionSet> | null>(() =>
    initialFiles?.questions
      ? {
          name: "questions.json",
          size: 0,
          data: initialFiles.questions,
          summary: `${initialFiles.questions.items.length} questions`,
        }
      : null,
  );
  const [bundleFile, setBundleFile] = useState<BundleFile | null>(null);
  const [existingLabelFile, setExistingLabelFile] = useState<LoadedFile<LabelFile> | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileAnnouncement, setFileAnnouncement] = useState<string | null>(null);
  const [shuffleSeed, setShuffleSeed] = useState(20260906);
  const [annotator, setAnnotator] = useState("");
  const [priorLabels, setPriorLabels] = useState<LabelFile | null>(() => (typeof window !== "undefined" ? loadLabels() : null));

  const [census, setCensus] = useState<Census | null>(null);
  const [labels, setLabels] = useState<LabelFile | null>(null);
  const [keyMismatch, setKeyMismatch] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"agreement" | "attribution" | "mislead">("agreement");

  const agreementList = useMemo(() => (census ? agreementItems(census) : []), [census]);
  const questionValidityList = useMemo(() => (census ? questionValidityItems(census) : []), [census]);
  const attributionList = useMemo(() => (census ? attributionRecords(census) : []), [census]);
  const abstentionList = useMemo(() => (census ? abstentionItems(census) : []), [census]);
  const misleadList = useMemo(() => (census && labels ? misleadItems(census, labels, 30) : []), [census, labels]);
  const progress = labels ? passProgress(labels) : null;
  const attributionStarted = labels ? labels.passes.attribution.started_at !== null : false;

  function applyLabels(updater: (current: LabelFile) => LabelFile) {
    setLabels((current) => {
      if (!current) return current;
      const next = withTextSourceCounts(updater(current));
      saveLabels(next);
      return next;
    });
  }

  /**
   * Reads every file in a drop or a picker selection, works out which slot each belongs
   * in from its JSON shape and, for the three run files, its name, and assigns it. One
   * bad file does not stop the rest: every failure is collected and reported together.
   */
  async function processFiles(fileList: FileList) {
    const files = Array.from(fileList);
    const errors: string[] = [];
    const loaded: string[] = [];
    const filled: Record<RunArm, boolean> = {
      production: productionFile !== null,
      closed_book: closedBookFile !== null,
      naive: naiveFile !== null,
    };

    for (const file of files) {
      try {
        const text = await file.text();
        const json = JSON.parse(text) as Record<string, unknown>;
        const shape = shapeKind(json);
        if (shape === "run") {
          const arm = armForRunFile(file.name, filled);
          filled[arm] = true;
          const entry: LoadedFile<RunFile> = {
            name: file.name,
            size: file.size,
            data: json as unknown as RunFile,
            summary: `${(json.results as unknown[]).length} records`,
          };
          if (arm === "production") setProductionFile(entry);
          else if (arm === "closed_book") setClosedBookFile(entry);
          else setNaiveFile(entry);
          loaded.push(`${file.name} as the ${humanize(arm).toLowerCase()} run`);
        } else if (shape === "questions") {
          setQuestionFile({
            name: file.name,
            size: file.size,
            data: json as unknown as QuestionSet,
            summary: `${(json.items as unknown[]).length} questions`,
          });
          loaded.push(`${file.name} as the question set`);
        } else if (shape === "bundle") {
          const byId = new Map<string, { exact_text?: unknown; text?: unknown }>();
          for (const record of json.evidence as unknown[]) {
            if (record && typeof record === "object" && typeof (record as { evidence_id?: unknown }).evidence_id === "string") {
              byId.set((record as { evidence_id: string }).evidence_id, record as { exact_text?: unknown; text?: unknown });
            }
          }
          const renderFull = (id: string): string | null => {
            const record = byId.get(id);
            if (!record) return null;
            if (typeof record.exact_text === "string") return record.exact_text;
            if (typeof record.text === "string") return record.text;
            return null;
          };
          setBundleFile({
            name: file.name,
            size: file.size,
            summary: `${(json.evidence as unknown[]).length} passages`,
            renderFull,
          });
          loaded.push(`${file.name} as the release bundle`);
        } else if (shape === "labels") {
          const records = (json.records as Record<string, unknown>) ?? {};
          setExistingLabelFile({
            name: file.name,
            size: file.size,
            data: json as unknown as LabelFile,
            summary: `${Object.keys(records).length} records`,
          });
          loaded.push(`${file.name} as the existing label file`);
        } else {
          errors.push(`${file.name}: not a run file, question set, release bundle or label file`);
        }
      } catch (error) {
        errors.push(`${file.name}: ${error instanceof Error ? error.message : "not valid JSON"}`);
      }
    }

    setFileError(errors.length ? errors.join(" ") : null);
    setFileAnnouncement(loaded.length ? `Loaded ${loaded.join(", ")}.` : null);
  }

  function clearSlot(kind: SlotKind) {
    if (kind === "production") setProductionFile(null);
    else if (kind === "closed_book") setClosedBookFile(null);
    else if (kind === "naive") setNaiveFile(null);
    else if (kind === "questions") setQuestionFile(null);
    else if (kind === "bundle") setBundleFile(null);
    else setExistingLabelFile(null);
    setFileAnnouncement(null);
  }

  function startCensus() {
    if (!productionFile) return;
    const runs: Partial<Record<Arm, RunFile>> = { production: productionFile.data };
    if (closedBookFile) runs.closed_book = closedBookFile.data;
    if (naiveFile) runs.naive = naiveFile.data;

    const newCensus = new Census(runs, {
      questions: questionFile?.data ?? null,
      renderFull: bundleFile?.renderFull ?? null,
      shuffleSeed,
      abstentionSample: 20,
    });

    let newLabels: LabelFile;
    let mismatch = 0;
    if (existingLabelFile) {
      const expectedKeys = new Set<string>();
      for (const arm of ARMS) {
        for (const record of newCensus.armRecords(arm)) expectedKeys.add(labelKey(record.question_id, arm));
      }
      const actualKeys = new Set(Object.keys(existingLabelFile.data.records));
      for (const key of expectedKeys) if (!actualKeys.has(key)) mismatch += 1;
      for (const key of actualKeys) if (!expectedKeys.has(key)) mismatch += 1;
      newLabels = existingLabelFile.data;
    } else {
      const runPaths: Partial<Record<Arm, string>> = { production: productionFile.name };
      if (closedBookFile) runPaths.closed_book = closedBookFile.name;
      if (naiveFile) runPaths.naive = naiveFile.name;
      newLabels = emptyLabelFile(newCensus, { runPaths, rubricSha256: null, annotator: annotator.trim() || null });
    }
    newLabels = withTextSourceCounts(newLabels);
    saveLabels(newLabels);
    setCensus(newCensus);
    setLabels(newLabels);
    setKeyMismatch(existingLabelFile ? mismatch : null);
    setPriorLabels(null);
    setActiveTab("agreement");
  }

  function continuePrior() {
    if (!priorLabels) return;
    setExistingLabelFile({
      name: "(an earlier session in this browser)",
      size: 0,
      data: priorLabels,
      summary: `${Object.keys(priorLabels.records).length} records`,
    });
    setPriorLabels(null);
  }

  function discardPrior() {
    clearLabels();
    setPriorLabels(null);
  }

  function downloadLabels() {
    if (!labels) return;
    const now = new Date();
    const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
    downloadText(`labels-${labels.annotator || "annotator"}-${stamp}.json`, JSON.stringify(labels, null, 2), "application/json");
  }

  function discardEverything() {
    if (!window.confirm("Discard every label held in this browser? This cannot be undone.")) return;
    clearLabels();
    setLabels(null);
    setCensus(null);
    setKeyMismatch(null);
    setPriorLabels(null);
  }

  function updateAgreementClaim(
    key: string,
    index: number,
    patch: Partial<Pick<ClaimLabel, "agreement" | "eligibility_drop" | "eligibility_condition" | "note">>,
  ) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const claims = record.claims.map((claim) => (claim.index === index ? { ...claim, ...patch } : claim));
      const records = { ...current.records, [key]: { ...record, claims } };
      const passes = stampStarted(current.passes, "agreement");
      return maybeFinishAgreement({ ...current, records, passes });
    });
  }

  function updateQuestionValidity(keys: string[], patch: { question_valid?: boolean; note?: string }) {
    applyLabels((current) => {
      const records = { ...current.records };
      for (const key of keys) {
        const record = records[key];
        if (!record) continue;
        records[key] = {
          ...record,
          ...(patch.question_valid !== undefined ? { question_valid: patch.question_valid } : {}),
          ...(patch.note !== undefined ? { note: patch.note } : {}),
        };
      }
      const passes = stampStarted(current.passes, "agreement");
      return maybeFinishAgreement({ ...current, records, passes });
    });
  }

  function updatePairAttribution(key: string, index: number, evidenceId: string, value: AttributionLabel) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const claims = record.claims.map((claim) =>
        claim.index === index ? { ...claim, pair_attribution: { ...claim.pair_attribution, [evidenceId]: value } } : claim,
      );
      const records = { ...current.records, [key]: { ...record, claims } };
      return { ...current, records, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updateJointAttribution(key: string, index: number, value: AttributionLabel) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const claims = record.claims.map((claim) => (claim.index === index ? { ...claim, joint_attribution: value } : claim));
      const records = { ...current.records, [key]: { ...record, claims } };
      return { ...current, records, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updateClaimNote(key: string, index: number, note: string) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const claims = record.claims.map((claim) => (claim.index === index ? { ...claim, note } : claim));
      const records = { ...current.records, [key]: { ...record, claims } };
      return { ...current, records, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updatePresentationDefect(key: string, value: boolean) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const records = { ...current.records, [key]: { ...record, presentation_defect: value } };
      return { ...current, records, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updateRecordNote(key: string, note: string) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const records = { ...current.records, [key]: { ...record, note } };
      return { ...current, records, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updateGateBlocked(questionId: string, patch: Partial<{ gate_right: boolean; dak_has_answer: boolean; note: string }>) {
    applyLabels((current) => {
      const entry = current.gate_blocked[questionId];
      if (!entry) return current;
      const gate_blocked = { ...current.gate_blocked, [questionId]: { ...entry, ...patch } };
      return { ...current, gate_blocked, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updateAbstainedSample(
    questionId: string,
    patch: Partial<{ any_retrieved_passage_answers: boolean; question_valid: boolean; note: string }>,
  ) {
    applyLabels((current) => {
      const raw = current.abstained_sample[questionId];
      if (!raw || typeof raw !== "object") return current;
      const merged: AbstainedEntry = { ...(raw as AbstainedEntry), ...patch };
      const abstained_sample = {
        ...current.abstained_sample,
        [questionId]: merged,
      } as unknown as LabelFile["abstained_sample"];
      return { ...current, abstained_sample, passes: stampStarted(current.passes, "attribution") };
    });
  }

  function updateMislead(key: string, index: number, patch: { extent: string; likelihood: string; reason: string }) {
    applyLabels((current) => {
      const record = current.records[key];
      if (!record) return current;
      const claims = record.claims.map((claim) => (claim.index === index ? { ...claim, mislead: patch } : claim));
      const records = { ...current.records, [key]: { ...record, claims } };
      return { ...current, records, passes: stampStarted(current.passes, "mislead") };
    });
  }

  const tabs: Array<{ id: "agreement" | "attribution" | "mislead"; label: string }> = [
    { id: "agreement", label: "Pass A, agreement" },
    { id: "attribution", label: "Pass B, attribution" },
    { id: "mislead", label: "Pass C, mislead" },
  ];

  /** Attribution folds in abstention, since one tab holds both sections. */
  const tabProgress: Partial<Record<"agreement" | "attribution" | "mislead", { done: number; total: number }>> = progress
    ? {
        agreement: { done: progress.agreement.done, total: progress.agreement.total },
        attribution: {
          done: progress.attribution.done + progress.abstention.done,
          total: progress.attribution.total + progress.abstention.total,
        },
        mislead: { done: progress.mislead.done, total: progress.mislead.total },
      }
    : {};

  return (
    <>
      {priorLabels && !census && (
        <div className={shellStyles.notice} role="status">
          <p>A label file from an earlier session is in this browser.</p>
          <div className={shellStyles.actions}>
            <button className={shellStyles.button} onClick={continuePrior} type="button">
              Continue with it
            </button>
            <button className={shellStyles.button} onClick={discardPrior} type="button">
              Discard
            </button>
          </div>
        </div>
      )}

      <section aria-labelledby="labelling-files">
        <h2 id="labelling-files">Files</h2>
        <p className={shellStyles["section-lede"]}>
          Drop every file the census needs at once, or add them one at a time. Each is routed to the slot it matches
          by name and by shape; nothing here leaves the browser.
        </p>
        {fileError && (
          <p className={shellStyles.notice} role="alert">
            {fileError}
          </p>
        )}
        <p aria-live="polite" className={shellStyles.srOnly} role="status">
          {fileAnnouncement}
        </p>

        <FileDropZone onFiles={(files) => void processFiles(files)} />

        <ul className={styles.slotList}>
          {SLOTS.map((slot) => (
            <SlotRow
              key={slot.kind}
              loaded={
                slot.kind === "production"
                  ? productionFile
                  : slot.kind === "closed_book"
                    ? closedBookFile
                    : slot.kind === "naive"
                      ? naiveFile
                      : slot.kind === "questions"
                        ? questionFile
                        : slot.kind === "bundle"
                          ? bundleFile
                          : existingLabelFile
              }
              onClear={() => clearSlot(slot.kind)}
              slot={slot}
            />
          ))}
        </ul>

        <div className={styles.settingsRow}>
          <div className={`${shellStyles.field} ${styles.seedField}`}>
            <label htmlFor="shuffle-seed">Shuffle seed</label>
            <input
              className={shellStyles["text-input"]}
              id="shuffle-seed"
              onChange={(event) => {
                const value = Math.trunc(Number(event.target.value));
                setShuffleSeed(Number.isFinite(value) ? Math.min(4294967295, Math.max(0, value)) : 0);
              }}
              type="number"
              value={shuffleSeed}
            />
          </div>
          <div className={`${shellStyles.field} ${styles.annotatorField}`}>
            <label htmlFor="annotator-name">Annotator</label>
            <input
              className={shellStyles["text-input"]}
              id="annotator-name"
              onChange={(event) => setAnnotator(event.target.value)}
              type="text"
              value={annotator}
            />
          </div>
        </div>

        <div className={shellStyles.actions}>
          <button
            className={`${shellStyles.button} ${shellStyles.primary}`}
            disabled={!productionFile}
            onClick={startCensus}
            type="button"
          >
            Start the census
          </button>
          {!productionFile && <span className={shellStyles.muted}>Load the production run to continue.</span>}
        </div>
        {keyMismatch !== null && keyMismatch > 0 && (
          <p className={shellStyles.notice} role="alert">
            The loaded label file does not match this census: {keyMismatch} record{keyMismatch === 1 ? "" : "s"} differ.
          </p>
        )}
      </section>

      {census && labels && progress && (
        <>
          <section aria-labelledby="labelling-progress">
            <h2 id="labelling-progress">Progress</h2>
            <dl className={styles.progressRow}>
              <div>
                <dt>Agreement</dt>
                <dd>
                  {progress.agreement.done} / {progress.agreement.total}
                </dd>
              </div>
              <div>
                <dt>Attribution</dt>
                <dd>
                  {progress.attribution.done} / {progress.attribution.total}
                </dd>
              </div>
              <div>
                <dt>Abstention</dt>
                <dd>
                  {progress.abstention.done} / {progress.abstention.total}
                </dd>
              </div>
              <div>
                <dt>Mislead</dt>
                <dd>
                  {progress.mislead.done} / {progress.mislead.total}
                </dd>
              </div>
            </dl>
            <p className={`${shellStyles.status} ${labels.census_valid ? shellStyles.ok : shellStyles.warn}`}>
              {labels.census_valid
                ? "Census valid: every cited passage came from the bundle"
                : `${labels.pair_text_sources.run} passages came from the run file, not the bundle`}
            </p>
          </section>

          <section aria-labelledby="labelling-passes">
            <h2 id="labelling-passes">Passes</h2>
            <div aria-label="Labelling passes" className={styles.tablist} role="tablist">
              {tabs.map((tab) => {
                const stat = tabProgress[tab.id];
                return (
                  <button
                    aria-controls={`panel-${tab.id}`}
                    aria-selected={activeTab === tab.id}
                    className={styles.tab}
                    id={`tab-${tab.id}`}
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    role="tab"
                    type="button"
                  >
                    {tab.label}
                    {stat && (
                      <span className={styles.tabProgress}>
                        {stat.done} / {stat.total}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div aria-labelledby={`tab-${activeTab}`} id={`panel-${activeTab}`} role="tabpanel">
              {activeTab === "agreement" && (
                <PassAgreement
                  items={agreementList}
                  onUpdateClaim={updateAgreementClaim}
                  onUpdateQuestion={updateQuestionValidity}
                  questionItems={questionValidityList}
                  records={labels.records}
                />
              )}
              {activeTab === "attribution" && (
                <PassAttribution
                  abstentionItems={abstentionList}
                  labels={labels}
                  onUpdateAbstained={updateAbstainedSample}
                  onUpdateGateBlocked={updateGateBlocked}
                  onUpdateJoint={updateJointAttribution}
                  onUpdateNote={updateClaimNote}
                  onUpdatePair={updatePairAttribution}
                  onUpdatePresentationDefect={updatePresentationDefect}
                  onUpdateRecordNote={updateRecordNote}
                  records={attributionList}
                />
              )}
              {activeTab === "mislead" &&
                (attributionStarted ? (
                  <PassMislead items={misleadList} labels={labels} onUpdate={updateMislead} />
                ) : (
                  <p className={shellStyles.empty}>Pass C opens once Pass B carries at least one label.</p>
                ))}
            </div>
          </section>

          <section aria-labelledby="labelling-save">
            <h2 id="labelling-save">Saving</h2>
            <div className={shellStyles.actions}>
              <button className={`${shellStyles.button} ${shellStyles.primary}`} onClick={downloadLabels} type="button">
                Download label file
              </button>
              <button className={shellStyles.button} onClick={discardEverything} type="button">
                Discard everything in this browser
              </button>
            </div>
          </section>
        </>
      )}
    </>
  );
}
