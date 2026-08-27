"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AnswerPanel } from "@/components/evidence-workspace/answer-panel";
import { AppHeader } from "@/components/evidence-workspace/app-header";
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
import { buildCitations } from "@/lib/evidence-presentation";
import type { CitationIndex } from "@/lib/evidence-presentation";
import { rankedCandidates } from "@/lib/presentation";
import type { RankedEvidence } from "@/lib/presentation";
import type { ClinicalContext, QuestionResult, SourceFilters } from "@/lib/types";

import styles from "./evidence-workspace/workspace.module.css";

const DEFAULT_SOURCE_FILTERS: SourceFilters = {
  jurisdictions: ["US", "EU", "UK"],
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
  error: null,
  isLoading: true,
  registryAvailable: false,
  releaseId: null,
};

export function EvidenceWorkspace() {
  const [question, setQuestion] = useState("");
  const [sourceFilters, setSourceFilters] = useState<SourceFilters>(DEFAULT_SOURCE_FILTERS);
  const [editOpen, setEditOpen] = useState(false);
  const [draftContext, setDraftContext] = useState<ClinicalContext | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const [corpusStatus, setCorpusStatus] = useState<CorpusStatus>(INITIAL_CORPUS_STATUS);
  const [transientFeedback, setTransientFeedback] = useState<TransientFeedback | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);
  const previousLifecycleRef = useRef("idle");
  const feedbackIdRef = useRef(0);
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

  const showTransient = useCallback(
    (tone: FeedbackTone, title: string, message: string, duration = 4500) => {
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

  useEffect(() => {
    let active = true;
    void getCorpusReadiness()
      .then((readiness) => {
        if (!active) return;
        setCorpusStatus({
          approvedCorpusAvailable: readiness.approved_corpus_available,
          error: null,
          isLoading: false,
          registryAvailable: readiness.corpus_registry_available,
          releaseId: readiness.corpus_release_id,
        });
      })
      .catch((error: unknown) => {
        if (!active) return;
        setCorpusStatus({
          approvedCorpusAvailable: false,
          error: error instanceof Error ? error.message : "Corpus readiness could not be checked.",
          isLoading: false,
          registryAvailable: false,
          releaseId: null,
        });
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const previousLifecycle = previousLifecycleRef.current;
    previousLifecycleRef.current = run.lifecycle;
    if (previousLifecycle === run.lifecycle) return;

    if (run.lifecycle === "completed") {
      showTransient(
        "success",
        "Evidence review ready",
        "Automated checks completed. Review the claim-linked source passages before use.",
      );
    } else if (run.lifecycle === "cancelled") {
      showTransient(
        "info",
        "Stopped waiting in this browser",
        "Server processing may continue. Submit again if you need a new monitored review.",
        6500,
      );
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

  function selectClaim(claimId: string) {
    setSelectedClaimId(claimId);
    const claim = workspaceResult?.claims.find((item) => item.claim_id === claimId);
    setSelectedEvidenceId(claim?.evidence_ids[0] ?? null);
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
      "The answer, source links, and research-use notice were copied.",
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
  const shouldShowGettingStarted =
    run.lifecycle === "idle" ||
    ((run.lifecycle === "cancelled" || run.lifecycle === "failed") && !workspaceResult);

  return (
    <div className={styles.appShell}>
      <AppHeader />

      <main id="main-content">
        <QuestionComposer
          isRunning={run.isRunning}
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
              actionLabel={run.canRetry ? "Try again" : undefined}
              message={errorFeedback.message}
              onAction={run.canRetry ? () => void run.retry() : undefined}
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

        {transientFeedback ? (
          <FeedbackBanner
            key={transientFeedback.id}
            message={transientFeedback.message}
            onDismiss={() => setTransientFeedback(null)}
            title={transientFeedback.title}
            tone={transientFeedback.tone}
          />
        ) : null}

        {shouldShowGettingStarted ? <GettingStarted corpusStatus={corpusStatus} /> : null}

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
            onCopyRunId={copyRunId}
            onEditContext={openContextEditor}
            onExportAudit={exportAudit}
            onRetry={() => void run.retry()}
            onSelectClaim={selectClaim}
            onSelectEvidence={setSelectedEvidenceId}
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
  isPrevious,
  onCopyAnswer,
  onCopyRunId,
  onEditContext,
  onExportAudit,
  onRetry,
  onSelectClaim,
  onSelectEvidence,
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
  isPrevious: boolean;
  onCopyAnswer: () => void;
  onCopyRunId: () => void;
  onEditContext: () => void;
  onExportAudit: () => void;
  onRetry: () => void;
  onSelectClaim: (claimId: string) => void;
  onSelectEvidence: (evidenceId: string) => void;
  progressEvents: ReturnType<typeof useEvidenceRun>["progressEvents"];
  result: QuestionResult;
  runLifecycle: ReturnType<typeof useEvidenceRun>["lifecycle"];
  runStatus: ReturnType<typeof useEvidenceRun>["status"];
  selectedClaimEvidence: QuestionResult["evidence_details"];
  selectedClaimId: string | null;
  selectedEvidenceId: string | null;
}) {
  const ready = result.status === "ANSWER_READY";
  return (
    <>
      <ProvenanceStrip onCopyRunId={onCopyRunId} result={result} />
      <div className={styles["workspace-grid"]}>
        <div className={styles["result-column"]}>
          <AnswerPanel
            isPrevious={isPrevious}
            onCopyAnswer={onCopyAnswer}
            onExportAudit={onExportAudit}
            onRetry={onRetry}
            onSelectClaim={onSelectClaim}
            onSelectEvidence={onSelectEvidence}
            result={result}
            selectedClaimId={selectedClaimId}
            selectedEvidenceId={selectedEvidenceId}
          />
          {ready ? (
            <EvidenceDetails
              allowContextEditing={allowContextEditing}
              candidates={candidates}
              citations={citations}
              context={result.interpreted_context}
              onEditContext={onEditContext}
              onSelectEvidence={onSelectEvidence}
              result={result}
              selectedClaimId={selectedClaimId}
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
              evidence={selectedClaimEvidence}
              onSelectEvidence={onSelectEvidence}
              selectedEvidenceId={selectedEvidenceId}
            />
          ) : null}
          <VerificationPanel
            isRunning={false}
            lifecycle={runLifecycle}
            progressEvents={progressEvents}
            result={result}
            status={runStatus}
          />
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
      message: "The server may still be processing the run. Retry monitoring when the service is available.",
    };
  }
  return {
    title: "The review could not be started",
    message: "Nothing unsafe was displayed. Try once more or share the run details if the problem continues.",
  };
}
