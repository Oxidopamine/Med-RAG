"use client";

import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleDashed,
  FileSearch,
  FileText,
  Info,
  Pencil,
  RotateCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";

import {
  API_URL,
  getQuestion,
  replaceQuestionContext,
  submitQuestion,
} from "@/lib/api";
import { contextRows, STATUS_LABELS, TERMINAL_STATUSES } from "@/lib/presentation";
import type {
  ClinicalContext,
  ProgressEvent,
  QuestionResult,
  QuestionStatus,
} from "@/lib/types";

const STARTER_QUESTION =
  "For a 74-year-old with atrial fibrillation and eGFR 28, what do current guidelines say about anticoagulation?";

const PIPELINE_STAGES: QuestionStatus[] = [
  "CONTEXT_EXTRACTED",
  "RETRIEVING",
  "SEARCHING_COUNTER_EVIDENCE",
  "CHECKING_EVIDENCE_COMPLETENESS",
  "VERIFYING",
];

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function conceptInputValue(conditions: string[]): string {
  return conditions.map((item) => item.replaceAll("_", " ").toLowerCase()).join(", ");
}

function parseConceptInput(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim().toUpperCase().replaceAll(/\s+/g, "_"))
    .filter(Boolean);
}

export function EvidenceWorkspace() {
  const [question, setQuestion] = useState(STARTER_QUESTION);
  const [questionId, setQuestionId] = useState<string | null>(null);
  const [status, setStatus] = useState<QuestionStatus | null>(null);
  const [completedStages, setCompletedStages] = useState<QuestionStatus[]>([]);
  const [result, setResult] = useState<QuestionResult | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [draftContext, setDraftContext] = useState<ClinicalContext | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => eventSourceRef.current?.close();
  }, []);

  async function finishRun(id: string) {
    const payload = await getQuestion(id);
    setResult(payload);
    setStatus(payload.status);
    setIsRunning(false);
  }

  async function pollUntilTerminal(id: string) {
    for (let attempt = 0; attempt < 24; attempt += 1) {
      const payload = await getQuestion(id);
      setStatus(payload.status);
      if (TERMINAL_STATUSES.has(payload.status)) {
        setResult(payload);
        setIsRunning(false);
        return;
      }
      await delay(250);
    }
    throw new Error("The evidence run did not reach a terminal state.");
  }

  function listenForProgress(id: string) {
    eventSourceRef.current?.close();
    const source = new EventSource(`${API_URL}/v1/questions/${id}/events`);
    eventSourceRef.current = source;

    source.addEventListener("progress", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as ProgressEvent;
      setStatus(event.status);
      setCompletedStages((current) =>
        current.includes(event.status) ? current : [...current, event.status],
      );
      if (TERMINAL_STATUSES.has(event.status)) {
        source.close();
        void finishRun(id).catch((runError: unknown) => {
          setError(runError instanceof Error ? runError.message : "Unable to load the result.");
          setIsRunning(false);
        });
      }
    });

    source.onerror = () => {
      source.close();
      void pollUntilTerminal(id).catch((runError: unknown) => {
        setError(runError instanceof Error ? runError.message : "Connection to the API failed.");
        setIsRunning(false);
      });
    };
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || isRunning) return;

    setError(null);
    setResult(null);
    setStatus("QUEUED");
    setCompletedStages(["QUEUED"]);
    setIsRunning(true);

    try {
      const accepted = await submitQuestion(trimmedQuestion);
      setQuestionId(accepted.question_id);
      listenForProgress(accepted.question_id);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Unable to submit question.");
      setIsRunning(false);
    }
  }

  function openContextEditor() {
    if (!result?.interpreted_context) return;
    setDraftContext(structuredClone(result.interpreted_context));
    setEditOpen(true);
  }

  async function handleContextSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!questionId || !draftContext || isRunning) return;

    setError(null);
    setEditOpen(false);
    setStatus("QUEUED");
    setCompletedStages(["QUEUED"]);
    setIsRunning(true);
    try {
      const accepted = await replaceQuestionContext(questionId, draftContext);
      setQuestionId(accepted.question_id);
      listenForProgress(accepted.question_id);
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update context.");
      setIsRunning(false);
    }
  }

  const context = result?.interpreted_context ?? null;
  const rows = contextRows(context);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            GE
          </div>
          <div>
            <div className="brand-name">Guideline Evidence QA</div>
            <div className="brand-subtitle">Clinical evidence workspace</div>
          </div>
        </div>
        <div className="header-status">
          <span className="environment-badge">Research prototype</span>
          <span className="corpus-count">
            <span className="status-dot" aria-hidden="true" />0 approved sources
          </span>
        </div>
      </header>

      <div className="research-notice" role="note">
        <Info size={16} aria-hidden="true" />
        <span>Research use only. Not authorized for patient care. Do not enter PHI.</span>
      </div>

      <main>
        <form className="question-bar" onSubmit={handleSubmit}>
          <div className="question-field">
            <label htmlFor="question">Guideline question</label>
            <textarea
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={2}
              maxLength={4000}
              disabled={isRunning}
            />
            <div className="question-meta">
              <span>Source scope: US, EU, UK</span>
              <span>{question.length}/4,000</span>
            </div>
          </div>
          <button className="ask-button" type="submit" disabled={isRunning || !question.trim()}>
            {isRunning ? <CircleDashed className="spin" size={18} /> : <Search size={18} />}
            {isRunning ? "Checking" : "Ask"}
          </button>
        </form>

        {error ? (
          <div className="error-banner" role="alert">
            <AlertTriangle size={18} />
            <span>{error}</span>
          </div>
        ) : null}

        <div className="workspace-grid">
          <div className="result-column">
            <section className="workspace-section answer-section" aria-labelledby="answer-heading">
              <div className="section-heading">
                <div>
                  <span className="section-kicker">Result</span>
                  <h1 id="answer-heading">Answer</h1>
                </div>
                <StatusBadge status={status} />
              </div>

              {!status ? (
                <div className="empty-answer">
                  <FileSearch size={30} strokeWidth={1.5} />
                  <span>No result</span>
                </div>
              ) : isRunning ? (
                <PipelineProgress current={status} completed={completedStages} />
              ) : result?.status === "ABSTAINED" ? (
                <div className="abstention-state">
                  <div className="abstention-title">
                    <AlertTriangle size={21} aria-hidden="true" />
                    <div>
                      <h2>Evidence unavailable</h2>
                      <span>{result.abstention?.reason_code.replaceAll("_", " ")}</span>
                    </div>
                  </div>
                  <p>{result.abstention?.message}</p>
                  <div className="missing-roles">
                    <span>Missing evidence roles</span>
                    <ul>
                      {result.abstention?.missing_evidence_roles.map((role) => (
                        <li key={role}>{role.replaceAll("_", " ").toLowerCase()}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : result?.status === "ANSWER_READY" ? (
                <div className="claim-list">
                  {result.claims.map((claim) => (
                    <p key={claim.claim_id}>{claim.text}</p>
                  ))}
                </div>
              ) : (
                <div className="failed-state">The run failed safely. No clinical claim was rendered.</div>
              )}
            </section>

            <section className="workspace-section" aria-labelledby="context-heading">
              <div className="section-heading compact">
                <div>
                  <span className="section-kicker">Reviewed input</span>
                  <h2 id="context-heading">Interpreted clinical context</h2>
                </div>
                {context ? (
                  <button
                    className="icon-text-button"
                    type="button"
                    onClick={openContextEditor}
                    title="Edit interpreted context"
                  >
                    <Pencil size={15} />
                    Edit
                  </button>
                ) : null}
              </div>
              {rows.length ? (
                <dl className="context-grid">
                  {rows.map(([label, value], index) => (
                    <div key={`${label}-${value}-${index}`}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <div className="section-empty">No context extracted</div>
              )}
            </section>

            <section className="workspace-section" aria-labelledby="evidence-heading">
              <div className="section-heading compact">
                <div>
                  <span className="section-kicker">Sources</span>
                  <h2 id="evidence-heading">Primary evidence</h2>
                </div>
                <span className="item-count">0 items</span>
              </div>
              <div className="section-empty">No verified evidence</div>
            </section>
          </div>

          <aside className="source-column" aria-label="Source and verification">
            <section className="source-viewer" aria-labelledby="source-heading">
              <div className="viewer-toolbar">
                <div>
                  <span className="section-kicker">Document</span>
                  <h2 id="source-heading">Source viewer</h2>
                </div>
                <span className="page-indicator">Page --</span>
              </div>
              <div className="viewer-canvas">
                <FileText size={38} strokeWidth={1.35} />
                <span>No source selected</span>
              </div>
            </section>

            <section className="verification-section" aria-labelledby="verification-heading">
              <div className="section-heading compact">
                <div>
                  <span className="section-kicker">Safety gate</span>
                  <h2 id="verification-heading">Verification</h2>
                </div>
                <ShieldCheck size={20} />
              </div>
              <div className="verification-metrics">
                <div>
                  <strong>{result?.verification_summary.rendered_claims ?? 0}</strong>
                  <span>Rendered</span>
                </div>
                <div>
                  <strong>{result?.verification_summary.supported_claims ?? 0}</strong>
                  <span>Supported</span>
                </div>
                <div>
                  <strong>{result?.verification_summary.withheld_claims ?? 0}</strong>
                  <span>Withheld</span>
                </div>
              </div>
              <div className="verification-state">
                {result?.status === "ANSWER_READY" ? (
                  <><Check size={17} />Evidence gate passed</>
                ) : (
                  <><AlertTriangle size={17} />Evidence gate not passed</>
                )}
              </div>
            </section>
          </aside>
        </div>
      </main>

      {editOpen && draftContext ? (
        <div className="modal-backdrop" role="presentation">
          <form className="context-dialog" onSubmit={handleContextSubmit}>
            <div className="dialog-heading">
              <div>
                <span className="section-kicker">Question context</span>
                <h2>Edit interpreted context</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setEditOpen(false)}
                title="Close context editor"
                aria-label="Close context editor"
              >
                <X size={18} />
              </button>
            </div>
            <div className="dialog-fields">
              <label>
                <span>Age</span>
                <input
                  type="number"
                  min={0}
                  max={130}
                  value={draftContext.age ?? ""}
                  onChange={(event) =>
                    setDraftContext({
                      ...draftContext,
                      age: event.target.value === "" ? null : Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                <span>Conditions</span>
                <input
                  type="text"
                  value={conceptInputValue(draftContext.conditions)}
                  onChange={(event) =>
                    setDraftContext({
                      ...draftContext,
                      conditions: parseConceptInput(event.target.value),
                    })
                  }
                />
              </label>
              {draftContext.measurements.map((measurement, index) => (
                <div className="measurement-fields" key={`${measurement.concept}-${index}`}>
                  <label>
                    <span>{measurement.concept}</span>
                    <input
                      type="number"
                      step="any"
                      value={measurement.value}
                      onChange={(event) => {
                        const measurements = [...draftContext.measurements];
                        measurements[index] = { ...measurement, value: Number(event.target.value) };
                        setDraftContext({ ...draftContext, measurements });
                      }}
                    />
                  </label>
                  <label>
                    <span>Unit</span>
                    <input
                      type="text"
                      value={measurement.unit}
                      onChange={(event) => {
                        const measurements = [...draftContext.measurements];
                        measurements[index] = { ...measurement, unit: event.target.value };
                        setDraftContext({ ...draftContext, measurements });
                      }}
                    />
                  </label>
                </div>
              ))}
            </div>
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setEditOpen(false)}>
                Cancel
              </button>
              <button className="primary-button" type="submit">
                <RotateCw size={16} />Run again
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function StatusBadge({ status }: { status: QuestionStatus | null }) {
  const statusClass = status ? status.toLowerCase().replaceAll("_", "-") : "idle";
  return <span className={`status-badge status-${statusClass}`}>{status ? STATUS_LABELS[status] : "Idle"}</span>;
}

function PipelineProgress({
  current,
  completed,
}: {
  current: QuestionStatus;
  completed: QuestionStatus[];
}) {
  return (
    <ol className="pipeline-list">
      {PIPELINE_STAGES.map((stage) => {
        const isDone = completed.includes(stage) && stage !== current;
        const isCurrent = stage === current;
        return (
          <li className={isDone ? "done" : isCurrent ? "current" : "pending"} key={stage}>
            <span className="pipeline-icon">
              {isDone ? <Check size={14} /> : isCurrent ? <CircleDashed className="spin" size={14} /> : <ChevronRight size={14} />}
            </span>
            <span>{STATUS_LABELS[stage]}</span>
          </li>
        );
      })}
    </ol>
  );
}

