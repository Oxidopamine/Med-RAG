import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ClinicalContext, QuestionResult } from "@/lib/types";

import { useEvidenceRun } from "./use-evidence-run";

const apiMocks = vi.hoisted(() => ({
  getQuestion: vi.fn(),
  replaceQuestionContext: vi.fn(),
  submitQuestion: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  API_URL: "http://api.test",
  ...apiMocks,
}));

class MockEventSource {
  static instances: MockEventSource[] = [];

  closed = false;
  listeners = new Map<string, (event: MessageEvent) => void>();
  onerror: (() => void) | null = null;
  url: string;

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, listener);
  }

  close() {
    this.closed = true;
  }

  emitProgress(payload: unknown) {
    this.listeners.get("progress")?.(
      new MessageEvent("progress", { data: JSON.stringify(payload) }),
    );
  }

  fail() {
    this.onerror?.();
  }
}

const terminalResult: QuestionResult = {
  question_id: "question-1",
  question: "What do guidelines say?",
  status: "ABSTAINED",
  corpus_release: null,
  interpreted_context: null,
  claims: [],
  conflicts: [],
  evidence_details: [],
  retrieval_candidates: [],
  verification_summary: {
    rendered_claims: 0,
    supported_claims: 0,
    withheld_claims: 0,
  },
  abstention: {
    reason_code: "NO_APPROVED_CORPUS",
    message: "No approved guideline corpus is configured.",
    missing_evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
    closest_evidence_ids: [],
  },
  created_at: "2026-08-25T10:00:00+00:00",
  updated_at: "2026-08-25T10:00:04+00:00",
};

const context: ClinicalContext = {
  age: 74,
  sex: null,
  conditions: ["ATRIAL_FIBRILLATION"],
  known_absent_conditions: [],
  measurements: [],
  special_populations: [],
  known_absent_special_populations: [],
  care_setting: null,
  jurisdiction: "UK",
  question_type: "treatment_guideline",
  topic: "anticoagulation",
};

function progress(
  sequence: number,
  status: "CONTEXT_EXTRACTED" | "RETRIEVING" | "ABSTAINED" | "FAILED",
) {
  return {
    question_id: "question-1",
    sequence,
    status,
    occurred_at: `2026-08-25T10:00:0${sequence}+00:00`,
  };
}

describe("useEvidenceRun", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });

  it("orders progress, ignores stale events, and retains the previous terminal result", async () => {
    apiMocks.submitQuestion
      .mockResolvedValueOnce({ question_id: "question-1", status: "QUEUED" })
      .mockResolvedValueOnce({ question_id: "question-2", status: "QUEUED" });
    apiMocks.getQuestion.mockResolvedValueOnce(terminalResult);
    const { result } = renderHook(() => useEvidenceRun());
    const filters = { jurisdictions: ["UK"], organizations: ["NICE"] };

    await act(async () => {
      expect(await result.current.submit("First question", filters)).toBe(true);
    });

    const firstSource = MockEventSource.instances[0];
    expect(firstSource.url).toBe("http://api.test/v1/questions/question-1/events");
    act(() => firstSource.emitProgress(progress(2, "RETRIEVING")));
    act(() => firstSource.emitProgress(progress(1, "CONTEXT_EXTRACTED")));

    expect(result.current.progressEvents.map((event) => event.status)).toEqual([
      "QUEUED",
      "RETRIEVING",
    ]);

    act(() => firstSource.emitProgress(progress(3, "ABSTAINED")));
    await waitFor(() => expect(result.current.lifecycle).toBe("abstained"));
    expect(result.current.result).toEqual(terminalResult);

    await act(async () => {
      expect(await result.current.submit("Second question", filters)).toBe(true);
    });

    expect(apiMocks.submitQuestion).toHaveBeenLastCalledWith("Second question", filters);
    expect(result.current.previousResult).toEqual(terminalResult);
    expect(result.current.result).toBeNull();
    expect(result.current.lifecycle).toBe("running");

    act(() => firstSource.emitProgress(progress(4, "FAILED")));
    expect(result.current.status).toBe("QUEUED");
    expect(result.current.error).toBeNull();
  });

  it("clears optimistic state after a request error and retries the same scoped request", async () => {
    apiMocks.submitQuestion
      .mockRejectedValueOnce(new Error("The service is temporarily unavailable."))
      .mockResolvedValueOnce({ question_id: "question-2", status: "QUEUED" });
    const { result } = renderHook(() => useEvidenceRun());
    const filters = { jurisdictions: ["US"], organizations: ["AHA"] };

    await act(async () => {
      expect(await result.current.submit("Scoped question", filters)).toBe(false);
    });

    expect(result.current.lifecycle).toBe("failed");
    expect(result.current.status).toBeNull();
    expect(result.current.completedStages).toEqual([]);
    expect(result.current.progressEvents).toEqual([]);
    expect(result.current.error).toBe("The service is temporarily unavailable.");
    expect(result.current.canRetry).toBe(true);

    act(() => result.current.clearError());
    expect(result.current.error).toBeNull();

    await act(async () => {
      expect(await result.current.retry()).toBe(true);
    });

    expect(apiMocks.submitQuestion).toHaveBeenLastCalledWith("Scoped question", filters);
    expect(result.current.lifecycle).toBe("running");
  });

  it("stops only client monitoring and rejects events from the closed generation", async () => {
    apiMocks.submitQuestion.mockResolvedValue({
      question_id: "question-1",
      status: "QUEUED",
    });
    const { result } = renderHook(() => useEvidenceRun());

    await act(async () => {
      await result.current.submit("A question");
    });
    const source = MockEventSource.instances[0];

    act(() => result.current.stopWaiting());
    expect(source.closed).toBe(true);
    expect(result.current.lifecycle).toBe("cancelled");
    expect(result.current.isRunning).toBe(false);
    expect(result.current.status).toBe("QUEUED");

    act(() => source.emitProgress(progress(2, "RETRIEVING")));
    expect(result.current.status).toBe("QUEUED");
    expect(apiMocks.getQuestion).not.toHaveBeenCalled();
  });

  it("retries a failed context replacement with an immutable copy", async () => {
    apiMocks.submitQuestion.mockResolvedValue({
      question_id: "question-1",
      status: "QUEUED",
    });
    apiMocks.replaceQuestionContext
      .mockRejectedValueOnce(new Error("Context update failed."))
      .mockResolvedValueOnce({ question_id: "question-2", status: "QUEUED" });
    const { result } = renderHook(() => useEvidenceRun());

    await act(async () => {
      await result.current.submit("A question");
    });
    await act(async () => {
      expect(await result.current.replaceContext(context)).toBe(false);
    });
    context.conditions.push("MUTATED_AFTER_REQUEST");

    await act(async () => {
      expect(await result.current.retry()).toBe(true);
    });

    expect(apiMocks.replaceQuestionContext).toHaveBeenLastCalledWith("question-1", {
      ...context,
      conditions: ["ATRIAL_FIBRILLATION"],
    });
  });

  it("falls back to polling and completes immediately when a terminal result is ready", async () => {
    apiMocks.submitQuestion.mockResolvedValue({
      question_id: "question-1",
      status: "QUEUED",
    });
    apiMocks.getQuestion.mockResolvedValue(terminalResult);
    const { result } = renderHook(() => useEvidenceRun());

    await act(async () => {
      await result.current.submit("A question");
    });
    act(() => MockEventSource.instances[0].fail());

    await waitFor(() => expect(result.current.lifecycle).toBe("abstained"));
    expect(result.current.result).toEqual(terminalResult);
    expect(apiMocks.getQuestion).toHaveBeenCalledTimes(1);
  });
});
