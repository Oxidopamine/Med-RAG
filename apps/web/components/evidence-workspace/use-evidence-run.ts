"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  API_URL,
  getQuestion,
  replaceQuestionContext,
  submitQuestion,
} from "@/lib/api";
import { parseContract, progressEventSchema } from "@/lib/contracts";
import { TERMINAL_STATUSES } from "@/lib/presentation";
import type {
  ClinicalContext,
  QuestionAccepted,
  QuestionResult,
  QuestionStatus,
  SourceFilters,
} from "@/lib/types";

const POLL_TIMEOUT_MS = 5 * 60_000;
const POLL_INTERVAL_MS = 1_500;

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function cloneContext(context: ClinicalContext): ClinicalContext {
  return {
    ...context,
    conditions: [...context.conditions],
    known_absent_conditions: [...context.known_absent_conditions],
    measurements: context.measurements.map((measurement) => ({ ...measurement })),
    special_populations: [...context.special_populations],
    known_absent_special_populations: [...context.known_absent_special_populations],
  };
}

function cloneSourceFilters(sourceFilters?: SourceFilters): SourceFilters | undefined {
  if (!sourceFilters) return undefined;
  return {
    jurisdictions: [...sourceFilters.jurisdictions],
    organizations: [...sourceFilters.organizations],
  };
}

function terminalLifecycle(status: QuestionStatus): EvidenceRunLifecycle {
  if (status === "ANSWER_READY") return "completed";
  if (status === "ABSTAINED") return "abstained";
  return "failed";
}

type LastRequest =
  | {
      kind: "submit";
      question: string;
      sourceFilters?: SourceFilters;
    }
  | {
      kind: "replace-context";
      context: ClinicalContext;
      questionId: string;
    };

export type EvidenceRunLifecycle =
  | "idle"
  | "running"
  | "completed"
  | "abstained"
  | "failed"
  | "cancelled";

export interface EvidenceProgressEvent {
  occurredAt: string;
  status: QuestionStatus;
}

export interface EvidenceRunState {
  canRetry: boolean;
  clearError: () => void;
  completedStages: QuestionStatus[];
  error: string | null;
  isRunning: boolean;
  lifecycle: EvidenceRunLifecycle;
  previousResult: QuestionResult | null;
  progressEvents: EvidenceProgressEvent[];
  questionId: string | null;
  result: QuestionResult | null;
  status: QuestionStatus | null;
  replaceContext: (context: ClinicalContext) => Promise<boolean>;
  retry: () => Promise<boolean>;
  stopWaiting: () => void;
  submit: (question: string, sourceFilters?: SourceFilters) => Promise<boolean>;
}

export function useEvidenceRun(): EvidenceRunState {
  const [canRetry, setCanRetry] = useState(false);
  const [questionId, setQuestionId] = useState<string | null>(null);
  const [status, setStatus] = useState<QuestionStatus | null>(null);
  const [completedStages, setCompletedStages] = useState<QuestionStatus[]>([]);
  const [progressEvents, setProgressEvents] = useState<EvidenceProgressEvent[]>([]);
  const [result, setResult] = useState<QuestionResult | null>(null);
  const [previousResult, setPreviousResult] = useState<QuestionResult | null>(null);
  const [lifecycle, setLifecycle] = useState<EvidenceRunLifecycle>("idle");
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const generationRef = useRef(0);
  const lastEventSequenceRef = useRef(0);
  const lastRequestRef = useRef<LastRequest | null>(null);
  const resultRef = useRef<QuestionResult | null>(null);

  const closeProgressStream = useCallback(() => {
    const source = eventSourceRef.current;
    eventSourceRef.current = null;
    source?.close();
  }, []);

  useEffect(() => {
    return () => {
      generationRef.current += 1;
      closeProgressStream();
    };
  }, [closeProgressStream]);

  const recordStatus = useCallback(
    (nextStatus: QuestionStatus, occurredAt: string) => {
      setStatus(nextStatus);
      setCompletedStages((current) =>
        current.includes(nextStatus) ? current : [...current, nextStatus],
      );
      setProgressEvents((current) => {
        const lastEvent = current.at(-1);
        if (lastEvent?.status === nextStatus) {
          if (lastEvent.occurredAt === occurredAt) return current;
          return [...current.slice(0, -1), { status: nextStatus, occurredAt }];
        }
        return [...current, { status: nextStatus, occurredAt }];
      });
    },
    [],
  );

  const applyTerminalResult = useCallback(
    (payload: QuestionResult, generation: number) => {
      if (generation !== generationRef.current) return;
      resultRef.current = payload;
      setResult(payload);
      recordStatus(payload.status, payload.updated_at);
      setLifecycle(terminalLifecycle(payload.status));
      setError(null);
      closeProgressStream();
    },
    [closeProgressStream, recordStatus],
  );

  const pollUntilTerminal = useCallback(
    async (id: string, generation: number) => {
      const deadline = Date.now() + POLL_TIMEOUT_MS;

      while (Date.now() < deadline) {
        const payload = await getQuestion(id);
        if (generation !== generationRef.current) return;
        recordStatus(payload.status, payload.updated_at);
        if (TERMINAL_STATUSES.has(payload.status)) {
          applyTerminalResult(payload, generation);
          return;
        }

        const remainingMilliseconds = deadline - Date.now();
        if (remainingMilliseconds <= 0) break;
        await delay(Math.min(POLL_INTERVAL_MS, remainingMilliseconds));
        if (generation !== generationRef.current) return;
      }

      throw new Error(
        "This browser stopped waiting after five minutes. The server may still be processing the run.",
      );
    },
    [applyTerminalResult, recordStatus],
  );

  const finishRun = useCallback(
    async (id: string, generation: number) => {
      const payload = await getQuestion(id);
      if (generation !== generationRef.current) return;
      if (TERMINAL_STATUSES.has(payload.status)) {
        applyTerminalResult(payload, generation);
        return;
      }
      recordStatus(payload.status, payload.updated_at);
      await pollUntilTerminal(id, generation);
    },
    [applyTerminalResult, pollUntilTerminal, recordStatus],
  );

  const failMonitoring = useCallback(
    (runError: unknown, fallback: string, generation: number) => {
      if (generation !== generationRef.current) return;
      closeProgressStream();
      setError(errorMessage(runError, fallback));
      setLifecycle("failed");
    },
    [closeProgressStream],
  );

  const listenForProgress = useCallback(
    (id: string, generation: number) => {
      closeProgressStream();
      const source = new EventSource(`${API_URL}/v1/questions/${id}/events`);
      let fallbackStarted = false;
      let terminalObserved = false;
      eventSourceRef.current = source;

      source.addEventListener("progress", (message) => {
        if (
          generation !== generationRef.current ||
          eventSourceRef.current !== source
        ) {
          return;
        }

        try {
          const event = parseContract(
            progressEventSchema,
            JSON.parse((message as MessageEvent).data),
            "progress event",
          );
          if (event.question_id !== id) {
            throw new Error(
              "The API returned a progress event for a different question.",
            );
          }
          if (event.sequence <= lastEventSequenceRef.current) return;

          lastEventSequenceRef.current = event.sequence;
          recordStatus(event.status, event.occurred_at);
          if (TERMINAL_STATUSES.has(event.status)) {
            terminalObserved = true;
            closeProgressStream();
            void finishRun(id, generation).catch((runError: unknown) => {
              failMonitoring(runError, "Unable to load the result.", generation);
            });
          }
        } catch (eventError) {
          failMonitoring(
            eventError,
            "The progress stream returned an invalid event.",
            generation,
          );
        }
      });

      source.onerror = () => {
        if (
          terminalObserved ||
          fallbackStarted ||
          generation !== generationRef.current ||
          eventSourceRef.current !== source
        ) {
          return;
        }
        fallbackStarted = true;
        closeProgressStream();
        void pollUntilTerminal(id, generation).catch((runError: unknown) => {
          failMonitoring(runError, "Connection to the API failed.", generation);
        });
      };
    },
    [closeProgressStream, failMonitoring, finishRun, pollUntilTerminal, recordStatus],
  );

  const beginRun = useCallback(
    async (request: LastRequest) => {
      const generation = generationRef.current + 1;
      generationRef.current = generation;
      lastEventSequenceRef.current = 0;
      lastRequestRef.current = request;
      setCanRetry(true);
      closeProgressStream();

      const terminalResult = resultRef.current;
      if (terminalResult) setPreviousResult(terminalResult);
      resultRef.current = null;
      setResult(null);
      setQuestionId(null);
      setStatus(null);
      setCompletedStages([]);
      setProgressEvents([]);
      setError(null);
      setLifecycle("running");

      let accepted: QuestionAccepted;
      try {
        accepted =
          request.kind === "submit"
            ? await submitQuestion(request.question, request.sourceFilters)
            : await replaceQuestionContext(request.questionId, request.context);
      } catch (requestError) {
        if (generation !== generationRef.current) return false;
        setQuestionId(null);
        setStatus(null);
        setCompletedStages([]);
        setProgressEvents([]);
        setError(errorMessage(requestError, "Unable to start the evidence run."));
        setLifecycle("failed");
        return false;
      }

      if (generation !== generationRef.current) return false;
      setQuestionId(accepted.question_id);
      recordStatus(accepted.status, new Date().toISOString());
      if (TERMINAL_STATUSES.has(accepted.status)) {
        try {
          await finishRun(accepted.question_id, generation);
        } catch (runError) {
          failMonitoring(runError, "Unable to load the result.", generation);
        }
        return true;
      }
      try {
        listenForProgress(accepted.question_id, generation);
      } catch {
        closeProgressStream();
        void pollUntilTerminal(accepted.question_id, generation).catch(
          (runError: unknown) => {
            failMonitoring(runError, "Connection to the API failed.", generation);
          },
        );
      }
      return true;
    },
    [
      closeProgressStream,
      failMonitoring,
      finishRun,
      listenForProgress,
      pollUntilTerminal,
      recordStatus,
    ],
  );

  const submit = useCallback(
    async (question: string, sourceFilters?: SourceFilters) =>
      beginRun({
        kind: "submit",
        question,
        sourceFilters: cloneSourceFilters(sourceFilters),
      }),
    [beginRun],
  );

  const replaceContext = useCallback(
    async (context: ClinicalContext) => {
      if (!questionId) return false;
      return beginRun({
        kind: "replace-context",
        context: cloneContext(context),
        questionId,
      });
    },
    [beginRun, questionId],
  );

  const retry = useCallback(async () => {
    const request = lastRequestRef.current;
    if (!request) return false;
    if (request.kind === "submit") {
      return beginRun({
        ...request,
        sourceFilters: cloneSourceFilters(request.sourceFilters),
      });
    }
    return beginRun({ ...request, context: cloneContext(request.context) });
  }, [beginRun]);

  const stopWaiting = useCallback(() => {
    if (lifecycle !== "running") return;
    generationRef.current += 1;
    closeProgressStream();
    setError(null);
    setLifecycle("cancelled");
  }, [closeProgressStream, lifecycle]);

  const clearError = useCallback(() => setError(null), []);

  return {
    canRetry,
    clearError,
    completedStages,
    error,
    isRunning: lifecycle === "running",
    lifecycle,
    previousResult,
    progressEvents,
    questionId,
    replaceContext,
    result,
    retry,
    status,
    stopWaiting,
    submit,
  };
}
