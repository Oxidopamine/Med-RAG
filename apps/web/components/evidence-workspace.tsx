"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AnswerPanel } from "@/components/evidence-workspace/answer-panel";
import { ContextDialog } from "@/components/evidence-workspace/context-dialog";
import {
  EvidenceDetails,
  InterpretedContextPanel,
} from "@/components/evidence-workspace/evidence-details";
import {
  FeedbackBanner,
  type FeedbackTone,
} from "@/components/evidence-workspace/feedback-banner";
import {
  GettingStarted,
  type CorpusStatus,
} from "@/components/evidence-workspace/getting-started";
import { ProvenanceStrip } from "@/components/evidence-workspace/provenance-strip";
import { QuestionComposer } from "@/components/evidence-workspace/question-composer";
import { RunProgress } from "@/components/evidence-workspace/run-progress";
import {
  SourceViewer,
  VerificationPanel,
} from "@/components/evidence-workspace/source-verification-column";
import { useEvidenceRun } from "@/components/evidence-workspace/use-evidence-run";
import { getCorpusReadiness } from "@/lib/api";
import { withRetries } from "@/lib/readiness";
import { recordRun } from "@/lib/run-history";
import { buildCitations } from "@/lib/evidence-presentation";
import type { CitationIndex } from "@/lib/evidence-presentation";
import { rankedCandidates } from "@/lib/presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type {
  ClinicalContext,
  EvidenceDetail,
  QuestionResult,
  SourceFilters,
} from "@/lib/types";

import styles from "./evidence-workspace/workspace.module.css";

/*
 * The active release is WHO SMART HIV, whose records are scoped WORLD. Requesting
 * US/EU/UK asked the serving path to narrow to editions this corpus does not contain,
 * and it silently widened the request back to WORLD anyway.
 */
const DEFAULT_SOURCE_FILTERS: SourceFilters = {
  jurisdictions: ["WORLD"],
  organizations: [],
};

interface TransientFeedback {
  id: number;
  message: string;
  title: string;
  tone: FeedbackTone;
}

const INITIAL_CORPUS_STATUS: CorpusStatus = {
  approvedCorpusAvailable: false,
  servingMode: null,
  error: null,
  isLoading: true,
  registryAvailable: false,
  releaseId: null,
};

export function EvidenceWorkspace({
  initialQuestionId,
}: {
  /** Set when the workspace was opened at /r/[questionId] rather than at the root. */
  initialQuestionId?: string;
} = {}) {
  const [question, setQuestion] = useState("");
  const [sourceFilters, setSourceFilters] = useState<SourceFilters>(DEFAULT_SOURCE_FILTERS);
  const [editOpen, setEditOpen] = useState(false);
  const [draftContext, setDraftContext] = useState<ClinicalContext | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  /** The record held beside the selected one for comparison, or null when not comparing. */
  const [pinnedEvidenceId, setPinnedEvidenceId] = useState<string | null>(null);
  /** Whether the inspector has been given the whole workspace to itself. */
  const [focusMode, setFocusMode] = useState(false);
  const [corpusStatus, setCorpusStatus] = useState<CorpusStatus>(INITIAL_CORPUS_STATUS);
  const [transientFeedback, setTransientFeedback] = useState<TransientFeedback | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);
  const previousLifecycleRef = useRef("idle");
  const feedbackIdRef = useRef(0);
  const adoptedRef = useRef(false);
  const run = useEvidenceRun();

  const workspaceResult = run.result ?? run.previousResult;
  const isPreviousResult = Boolean(workspaceResult && !run.result);

  const selectedClaim = useMemo(
    () =>
      workspaceResult?.claims.find((claim) => claim.claim_id === selectedClaimId) ??
      workspaceResult?.claims[0] ??
      null,
    [selectedClaimId, workspaceResult],
  );
  const selectedClaimEvidence = useMemo(
    () =>
      selectedClaim && workspaceResult
        ? workspaceResult.evidence_details.filter((detail) =>
            selectedClaim.evidence_ids.includes(detail.evidence_id),
          )
        : [],
    [selectedClaim, workspaceResult],
  );
  const candidates = useMemo(() => rankedCandidates(workspaceResult), [workspaceResult]);
  // One citation numbering for the whole answer, so a reference keeps its number across
  // the claim list, the conflict review, and the supporting-evidence list.
  const citations = useMemo(() => buildCitations(workspaceResult), [workspaceResult]);
  const effectiveSelectedClaimId = selectedClaim?.claim_id ?? null;
  // An uncited candidate stays selectable while the claim selection moves under it, so
  // reading down the ranking is not interrupted by a claim change.
  const selectableEvidenceIds = useMemo(
    () =>
      new Set([
        ...(selectedClaim?.evidence_ids ?? []),
        ...candidates.map(({ detail }) => detail.evidence_id),
      ]),
    [candidates, selectedClaim],
  );
  const effectiveSelectedEvidenceId =
    selectedEvidenceId && selectableEvidenceIds.has(selectedEvidenceId)
      ? selectedEvidenceId
      : selectedClaim?.evidence_ids[0] ?? null;

  /**
   * The pinned record, resolved against everything the result carries.
   *
   * Not against the selected claim's evidence: a conflict routinely names a passage from
   * another claim, and that is precisely the pair worth holding side by side. Resolution
   * happens here rather than in the inspector because this is the only place that has the
   * whole result to look in.
   */
  const pinnedEvidence = useMemo(
    () =>
      pinnedEvidenceId === null
        ? null
        : workspaceResult?.evidence_details.find(
            (detail) => detail.evidence_id === pinnedEvidenceId,
          ) ?? null,
    [pinnedEvidenceId, workspaceResult],
  );

  const showTransient = useCallback(
    // Six seconds rather than four and a half: the completion notice arrives after a
    // review that took minutes, and a reader who looked away for a moment should still
    // see it. Errors never time out.
    (tone: FeedbackTone, title: string, message: string, duration = 6000) => {
      if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
      feedbackIdRef.current += 1;
      setTransientFeedback({ id: feedbackIdRef.current, message, title, tone });
      if (tone !== "error") {
        feedbackTimerRef.current = window.setTimeout(() => {
          setTransientFeedback(null);
          feedbackTimerRef.current = null;
        }, duration);
      }
    },
    [],
  );

  // Opened at /r/[questionId]: attach to that run rather than starting from empty, and
  // show the question it was asked with.
  useEffect(() => {
    if (!initialQuestionId || adoptedRef.current) return;
    adoptedRef.current = true;
    void run.adopt(initialQuestionId).then((adopted) => {
      if (adopted) setQuestion((current) => current || adopted.question);
    });
  }, [initialQuestionId, run]);

  // Keep the address bar on the run being shown, so a reload or a shared link lands
  // back on it. `replaceState` rather than a router push: the workspace is already
  // mounted and a navigation would tear down the run it is monitoring.
  //
  // The same identifier goes into this browser's own history at the same moment. A run
  // takes minutes and has always had an address; nothing wrote it down, so closing the
  // tab was how a review got lost.
  useEffect(() => {
    const id = run.questionId;
    if (!id) return;
    const path = `/r/${encodeURIComponent(id)}`;
    if (window.location.pathname !== path) {
      window.history.replaceState(null, "", path);
    }
  }, [run.questionId]);

  /*
   * Write the run to the local history once, and again when its question is known.
   *
   * Keyed on the question text rather than on the result object it came from. `run.result`
   * is a new object on every progress update, so depending on it ran a read-parse-write of
   * localStorage per event and re-stamped `openedAt` each time - eight times for a run
   * that reports eight stages.
   *
   * `previousResult` is deliberately not consulted. It holds the *last* run while a new
   * one is in flight, so falling back to it labelled the new run's identifier with the
   * previous run's question - a history entry pointing at one review under the name of
   * another. An unnamed entry is recorded instead and named when the result lands, which
   * is also what a run adopted from a shared link does.
   */
  const recordedQuestion = run.result?.question ?? "";
  useEffect(() => {
    if (!run.questionId) return;
    recordRun(run.questionId, recordedQuestion);
  }, [run.questionId, recordedQuestion]);

  /*
   * Readiness is checked with retries, and checked again when the tab regains focus or
   * the browser comes back online. One failed request used to pin "unavailable" on the
   * page until a reload: an API still starting, or a laptop waking, read as a broken
   * corpus. The status stays "checking" while the retries run and settles on unreachable
   * only after the last one; a later focus or reconnect, or the reader's own request,
   * checks again.
   */
  const readinessAttemptRef = useRef(0);
  const readinessFailedRef = useRef(false);
  const runReadinessCheck = useCallback(() => {
    const attempt = (readinessAttemptRef.current += 1);
    const current = () => readinessAttemptRef.current === attempt;
    readinessFailedRef.current = false;
    void withRetries(getCorpusReadiness, { isActive: current })
      .then((readiness) => {
        if (!current()) return;
        setCorpusStatus({
          approvedCorpusAvailable: readiness.approved_corpus_available,
          servingMode: readiness.serving_mode,
          error: null,
          isLoading: false,
          registryAvailable: readiness.corpus_registry_available,
          releaseId: readiness.corpus_release_id,
        });
      })
      .catch((error: unknown) => {
        if (!current()) return;
        readinessFailedRef.current = true;
        setCorpusStatus({
          approvedCorpusAvailable: false,
          servingMode: null,
          error: error instanceof Error ? error.message : "Corpus readiness could not be checked.",
          isLoading: false,
          registryAvailable: false,
          releaseId: null,
        });
      });
  }, []);

  // The first check starts from the initial "checking" status; a later one, asked for by
  // a focus, a reconnect or the reader, announces itself before it runs.
  const checkReadiness = useCallback(() => {
    setCorpusStatus((previous) => ({ ...previous, error: null, isLoading: true }));
    runReadinessCheck();
  }, [runReadinessCheck]);

  useEffect(() => {
    runReadinessCheck();
    function recheckIfFailed() {
      if (readinessFailedRef.current && document.visibilityState === "visible") {
        checkReadiness();
      }
    }
    window.addEventListener("focus", recheckIfFailed);
    window.addEventListener("online", recheckIfFailed);
    document.addEventListener("visibilitychange", recheckIfFailed);
    return () => {
      readinessAttemptRef.current += 1;
      window.removeEventListener("focus", recheckIfFailed);
      window.removeEventListener("online", recheckIfFailed);
      document.removeEventListener("visibilitychange", recheckIfFailed);
    };
  }, [checkReadiness, runReadinessCheck]);

  useEffect(() => {
    const previousLifecycle = previousLifecycleRef.current;
    previousLifecycleRef.current = run.lifecycle;
    if (previousLifecycle === run.lifecycle) return;

    if (run.lifecycle === "completed") {
      showTransient(
        "success",
        "Evidence review ready",
        "Automated checks completed.",
      );
    } else if (run.lifecycle === "cancelled") {
      showTransient(
        "info",
        "Stopped waiting in this browser",
        "Server processing may continue. Submit again if you need a new monitored review.",
        6500,
      );
    }

    // A run can take minutes, so the reader who submitted it is not looking at the same
    // place when it lands. Move focus onto the result rather than announcing it and
    // leaving a keyboard user to tab in from the top of the document. The answer heading
    // already carries `tabIndex={-1}` and a suppressed focus ring for exactly this.
    //
    // Only idle focus is taken over: focus sitting on the submit button that started the
    // run, or dropped to <body>. Someone who has deliberately moved on - typing a
    // follow-up question, reading a source - keeps their place.
    if (["completed", "abstained", "failed"].includes(run.lifecycle)) {
      const active = document.activeElement;
      const focusIsIdle =
        !active ||
        active === document.body ||
        (active instanceof HTMLButtonElement && active.type === "submit");
      if (focusIsIdle) document.getElementById("answer-heading")?.focus();
    }
  }, [run.lifecycle, showTransient]);

  useEffect(
    () => () => {
      if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
    },
    [],
  );

  function openContextEditor() {
    const context = run.result?.interpreted_context;
    if (!context) return;
    setDraftContext(structuredClone(context));
    setEditOpen(true);
  }

  async function replaceContext(nextContext: ClinicalContext): Promise<boolean> {
    const accepted = await run.replaceContext(nextContext);
    if (accepted) {
      showTransient(
        "info",
        "Context update accepted",
        "The previous review remains visible while the updated context is checked.",
      );
    }
    return accepted;
  }

  /**
   * Comparison is a two-record state, so it is set as one.
   *
   * The conflict panel is the caller that matters: it names two passages that disagree
   * and, until now, could only open one of them. Opening the first and pinning the second
   * is the whole gesture.
   */
  function compareEvidence(evidenceId: string, againstEvidenceId: string) {
    setSelectedEvidenceId(evidenceId);
    setPinnedEvidenceId(againstEvidenceId);
  }

  function selectClaim(claimId: string) {
    setSelectedClaimId(claimId);
    const claim = workspaceResult?.claims.find((item) => item.claim_id === claimId);
    setSelectedEvidenceId(claim?.evidence_ids[0] ?? null);
  }

  /**
   * Select a claim, and take the reader to the evidence they asked to inspect.
   *
   * Selecting alone was enough on a wide screen, where the inspector is pinned beside the
   * claim and simply changes under the cursor. It was not enough anywhere the columns
   * stack - "Inspect evidence" moved something several screens below the button and left
   * the reader looking at the button. Focus moves with the view rather than after it,
   * because a keyboard user pressing this has the same request and no scrollbar to notice.
   *
   * Deferred a frame so the panel has rendered the newly selected claim's evidence before
   * it is scrolled to; scrolling to the old contents and repainting under the reader is
   * the one thing worse than not scrolling at all.
   */
  function inspectClaim(claimId: string) {
    selectClaim(claimId);
    requestAnimationFrame(() => {
      const heading = document.getElementById("source-heading");
      if (!(heading instanceof HTMLElement)) return;
      if (typeof heading.scrollIntoView === "function") {
        heading.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      heading.focus({ preventScroll: true });
    });
  }

  async function copyText(value: string, successTitle: string, successMessage: string) {
    try {
      await navigator.clipboard.writeText(value);
      showTransient("success", successTitle, successMessage, 3000);
    } catch {
      showTransient(
        "error",
        "Copy failed",
        "Clipboard access was not available. Select and copy the text manually.",
      );
    }
  }

  function copyRunId() {
    const questionId = run.questionId ?? workspaceResult?.question_id;
    if (!questionId) return;
    void copyText(questionId, "Run ID copied", "The run identifier is available on your clipboard.");
  }

  /**
   * The run's address, not its name.
   *
   * A colleague can open this; a run identifier only tells them what to search for. The
   * identifier stays visible in the provenance strip for the support-ticket case.
   */
  function copyRunLink() {
    const questionId = run.questionId ?? workspaceResult?.question_id;
    if (!questionId) return;
    const url = new URL(`/r/${encodeURIComponent(questionId)}`, window.location.origin);
    void copyText(
      url.toString(),
      "Link copied",
      "Anyone with access can open this review, with its release and verification intact.",
    );
  }

  function copyAnswer() {
    if (!workspaceResult || workspaceResult.status !== "ANSWER_READY") return;
    const sourceById = new Map(
      workspaceResult.evidence_details.map((detail) => [detail.evidence_id, detail]),
    );
    const claims = workspaceResult.claims
      .map((claim, index) => {
        const citations = claim.evidence_ids
          .map((evidenceId) => sourceById.get(evidenceId))
          .filter((detail) => detail !== undefined)
          .map((detail) => `${detail.source_title} (${detail.source_url})`)
          .join("; ");
        return `${index + 1}. ${claim.text}${citations ? `\n   Sources: ${citations}` : ""}`;
      })
      .join("\n\n");
    void copyText(
      `${claims}\n\nResearch use only. Run ID: ${workspaceResult.question_id}`,
      "Answer copied",
      "Answer and citations copied.",
    );
  }

  function exportAudit() {
    if (!workspaceResult) return;
    const payload = JSON.stringify(
      {
        exported_at: new Date().toISOString(),
        research_use_only: true,
        result: workspaceResult,
      },
      null,
      2,
    );
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const safeQuestionId = workspaceResult.question_id.replaceAll(/[^a-zA-Z0-9_-]/g, "_");
    anchor.download = `guideline-review-${safeQuestionId}.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    showTransient(
      "success",
      "Audit record exported",
      "A JSON record containing the question, context, provenance, checks, and evidence was downloaded.",
    );
  }

  const errorFeedback = run.error ? friendlyError(run.error) : null;
  // A stalled monitor is not a failed run: the server is very likely still working, so
  // the offer is to keep watching rather than to start over.
  const monitoringStalled = Boolean(
    run.error && /stopped waiting|connection/i.test(run.error) && run.canResumeMonitoring,
  );
  const errorAction = monitoringStalled
    ? { label: "Keep waiting", run: () => void run.resumeMonitoring() }
    : run.canRetry
      ? { label: "Try again", run: () => void run.retry() }
      : null;
  const shouldShowGettingStarted =
    run.lifecycle === "idle" ||
    ((run.lifecycle === "cancelled" || run.lifecycle === "failed") && !workspaceResult);

  return (
    <div className={styles.appShell}>
      <main id="main-content">
        <QuestionComposer
          isRunning={run.isRunning}
          // Accepted here, not yet by the server: the run is live but has no identifier
          // back. The composer used to infer this from a local flag around its own submit
          // promise, which is precisely the flag that could never be cleared if the
          // request hung.
          isStarting={run.isRunning && run.questionId === null}
          onChange={setQuestion}
          onSourceFiltersChange={setSourceFilters}
          onStopWaiting={run.stopWaiting}
          onSubmit={run.submit}
          question={question}
          showExamples={shouldShowGettingStarted}
          sourceFilters={sourceFilters}
        />

        {errorFeedback ? (
          <>
            <FeedbackBanner
              actionLabel={errorAction?.label}
              message={errorFeedback.message}
              onAction={errorAction?.run}
              onDismiss={run.clearError}
              title={errorFeedback.title}
              tone="error"
            />
            <details className={styles["technical-error"]}>
              <summary>Technical details</summary>
              <code>{run.error}</code>
            </details>
          </>
        ) : null}

        {/* In flow, above the workspace, with its slot reserved while a result is on
            screen. It was floated over the workspace to stop an inline banner pushing the
            result down on arrival and pulling it back on dismiss - but bottom-right put it
            on top of the source inspector, hiding the highlight and evidence-role rows for
            as long as it showed. Occluding provenance is the worse of the two: this is the
            one panel whose whole job is to be read. Reserving the space keeps the result
            still without covering any of it. */}
        <div
          className={`${styles["transient-feedback"]} ${
            workspaceResult ? styles["transient-feedback-reserved"] : ""
          }`}
        >
          {transientFeedback ? (
            <FeedbackBanner
              key={transientFeedback.id}
              message={transientFeedback.message}
              onDismiss={() => setTransientFeedback(null)}
              title={transientFeedback.title}
              tone={transientFeedback.tone}
            />
          ) : null}
        </div>

        {run.lifecycle === "cancelled" && run.canResumeMonitoring && !run.error ? (
          <FeedbackBanner
            actionLabel="Resume monitoring"
            message="This browser stopped watching the run. The server may still be working on it."
            onAction={() => void run.resumeMonitoring()}
            title="Monitoring stopped"
            tone="info"
          />
        ) : null}

        {shouldShowGettingStarted ? (
          <GettingStarted corpusStatus={corpusStatus} onRecheck={checkReadiness} />
        ) : null}

        {run.isRunning ? (
          <div className={styles["running-workspace"]}>
            <RunProgress
              onCopyRunId={copyRunId}
              progressEvents={run.progressEvents}
              questionId={run.questionId}
              status={run.status}
            />
            <VerificationPanel
              isRunning
              lifecycle={run.lifecycle}
              progressEvents={run.progressEvents}
              result={null}
              status={run.status}
            />
          </div>
        ) : null}

        {workspaceResult ? (
          <ResultWorkspace
            allowContextEditing={Boolean(run.result) && !run.isRunning}
            candidates={candidates}
            citations={citations}
            isPrevious={isPreviousResult}
            onCopyAnswer={copyAnswer}
            onCopyLink={copyRunLink}
            onEditContext={openContextEditor}
            onExportAudit={exportAudit}
            onRetry={() => void run.retry()}
            focusMode={focusMode}
            onCompareEvidence={compareEvidence}
            onInspectClaim={inspectClaim}
            onPinEvidence={setPinnedEvidenceId}
            onSelectClaim={selectClaim}
            onSelectEvidence={setSelectedEvidenceId}
            onToggleFocus={() => setFocusMode((current) => !current)}
            pinnedEvidence={pinnedEvidence}
            progressEvents={run.result ? run.progressEvents : []}
            result={workspaceResult}
            runLifecycle={run.result ? run.lifecycle : "completed"}
            runStatus={run.result ? run.status : workspaceResult.status}
            selectedClaimEvidence={selectedClaimEvidence}
            selectedClaimId={effectiveSelectedClaimId}
            selectedEvidenceId={effectiveSelectedEvidenceId}
          />
        ) : null}
      </main>

      {editOpen && draftContext && run.result?.interpreted_context ? (
        <ContextDialog
          context={draftContext}
          originalContext={run.result.interpreted_context}
          onChange={setDraftContext}
          onOpenChange={setEditOpen}
          onSubmit={replaceContext}
          open={editOpen}
        />
      ) : null}
    </div>
  );
}

function ResultWorkspace({
  allowContextEditing,
  candidates,
  citations,
  focusMode,
  isPrevious,
  onCompareEvidence,
  onCopyAnswer,
  onCopyLink,
  onEditContext,
  onExportAudit,
  onInspectClaim,
  onPinEvidence,
  onRetry,
  onSelectClaim,
  onSelectEvidence,
  onToggleFocus,
  pinnedEvidence,
  progressEvents,
  result,
  runLifecycle,
  runStatus,
  selectedClaimEvidence,
  selectedClaimId,
  selectedEvidenceId,
}: {
  allowContextEditing: boolean;
  candidates: RankedEvidence[];
  citations: CitationIndex;
  focusMode: boolean;
  isPrevious: boolean;
  onCompareEvidence: (evidenceId: string, againstEvidenceId: string) => void;
  onCopyAnswer: () => void;
  onCopyLink: () => void;
  onEditContext: () => void;
  onExportAudit: () => void;
  onInspectClaim: (claimId: string) => void;
  onPinEvidence: (evidenceId: string | null) => void;
  onRetry: () => void;
  onSelectClaim: (claimId: string) => void;
  onSelectEvidence: (evidenceId: string) => void;
  onToggleFocus: () => void;
  pinnedEvidence: EvidenceDetail | null;
  progressEvents: ReturnType<typeof useEvidenceRun>["progressEvents"];
  result: QuestionResult;
  runLifecycle: ReturnType<typeof useEvidenceRun>["lifecycle"];
  runStatus: ReturnType<typeof useEvidenceRun>["status"];
  selectedClaimEvidence: QuestionResult["evidence_details"];
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}) {
  const ready = result.status === "ANSWER_READY";
  /*
   * The audit sits under the answer it audits, and the inspector gets a section of its own.
   *
   * Sharing one scrolling column with the verification timeline capped the inspector at
   * roughly half the viewport, which is how a document viewer ended up 377px wide and
   * 450px tall with 1600px of document inside it. Sharing a row with the answer capped
   * its width the same way. It is now the second section of the page rather than the
   * second column of a row, which is also the order the narrow layout has always
   * used - answer, audit, evidence - so every width now agrees.
   *
   * When no answer was rendered there is no source to inspect, and the audit is what the
   * section is for: an empty band under an abstention would be worse than the panel
   * simply staying where it was.
   */
  const verification = (
    <VerificationPanel
      isRunning={false}
      lifecycle={runLifecycle}
      progressEvents={progressEvents}
      result={result}
      status={runStatus}
    />
  );
  return (
    <>
      <ProvenanceStrip onCopyLink={onCopyLink} onExportAudit={onExportAudit} result={result} />
      {/* Focus mode hides the answer section so the inspector is the only thing on the
          page. Reading a page of a guideline is the one task in this workspace limited by
          how much room it gets, and the answer it came from is one keystroke away. */}
      <div
        className={`${styles["workspace-grid"]} ${focusMode && ready ? styles["workspace-focused"] : ""}`}
      >
        <div className={styles["result-column"]} hidden={focusMode && ready}>
          <AnswerPanel
            isPrevious={isPrevious}
            onCopyAnswer={onCopyAnswer}
            onInspectClaim={onInspectClaim}
            onRetry={onRetry}
            onSelectClaim={onSelectClaim}
            onSelectEvidence={onSelectEvidence}
            result={result}
            selectedClaimId={selectedClaimId}
            selectedEvidenceId={selectedEvidenceId}
          />
          {ready ? verification : null}
          {ready ? (
            <EvidenceDetails
              allowContextEditing={allowContextEditing}
              citations={citations}
              context={result.interpreted_context}
              onCompareEvidence={onCompareEvidence}
              onEditContext={onEditContext}
              onSelectEvidence={onSelectEvidence}
              result={result}
              selectedEvidenceId={selectedEvidenceId}
            />
          ) : (
            <InterpretedContextPanel
              allowEditing={allowContextEditing}
              context={result.interpreted_context}
              onEdit={onEditContext}
            />
          )}
        </div>

        <div className={styles["source-column"]}>
          {ready ? (
            <SourceViewer
              candidates={candidates}
              citations={citations}
              // Both from the result rather than from the composer's state: the composer
              // holds whatever has been typed since, and the passage on screen was
              // retrieved for the question this run was actually accepted with.
              claimText={
                result.claims.find((claim) => claim.claim_id === selectedClaimId)?.text ??
                null
              }
              evidence={selectedClaimEvidence}
              focusMode={focusMode}
              onPinEvidence={onPinEvidence}
              onSelectEvidence={onSelectEvidence}
              onToggleFocus={onToggleFocus}
              pinnedEvidence={pinnedEvidence}
              question={result.question}
              selectedEvidenceId={selectedEvidenceId}
            />
          ) : (
            verification
          )}
        </div>
      </div>
    </>
  );
}

function friendlyError(error: string): { message: string; title: string } {
  const normalized = error.toLowerCase();
  if (normalized.includes("fetch") || normalized.includes("connection")) {
    return {
      title: "Could not reach the evidence service",
      message: "Check the API connection, then try the review again. No clinical claim was displayed.",
    };
  }
  if (normalized.includes("invalid") || normalized.includes("contract")) {
    return {
      title: "The evidence service returned an unexpected response",
      message: "The response was rejected before it entered the interface. Try again or share the technical details with support.",
    };
  }
  if (normalized.includes("five minutes")) {
    return {
      title: "This browser stopped waiting",
      message:
        "The run was not finished after five minutes. The server may still be working on it - keep waiting to pick monitoring back up.",
    };
  }
  return {
    title: "The review could not be started",
    message: "Try once more, or share the run details if the problem continues.",
  };
}
