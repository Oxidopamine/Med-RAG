import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { answerReadyResult } from "@/lib/fixtures/answer-lane";
import type { QuestionStatus } from "@/lib/types";

import { VerificationPanel } from "./source-verification-column";
import type { EvidenceProgressEvent } from "./use-evidence-run";

afterEach(cleanup);

const START = Date.parse("2026-08-28T10:00:00.000Z");

/** Events as the run emits them: a status, and when the run reached it. */
function at(status: QuestionStatus, secondsFromStart: number): EvidenceProgressEvent {
  return {
    status,
    occurredAt: new Date(START + secondsFromStart * 1000).toISOString(),
  };
}

function renderPanel(progressEvents: EvidenceProgressEvent[]) {
  render(
    <VerificationPanel
      isRunning={false}
      lifecycle="completed"
      progressEvents={progressEvents}
      result={answerReadyResult()}
      status="ANSWER_READY"
    />,
  );
  return screen.getByRole("list");
}

function stepRow(index: number): HTMLElement {
  return within(screen.getByRole("list")).getAllByRole("listitem")[index]!;
}

/**
 * The audit timeline reported `0ms` against a stage that had taken most of the run.
 *
 * `RETRIEVING` is emitted when retrieval begins and `CONTEXT_EXTRACTED` immediately
 * before it, so measuring from the previous stage's last event to this one's last event
 * timed the handoff between two adjacent writes rather than the stage between them.
 */
describe("VerificationPanel, how long each stage took", () => {
  it("times a stage from entering it to entering the next", () => {
    renderPanel([
      at("QUEUED", 0),
      at("CONTEXT_EXTRACTED", 1),
      at("RETRIEVING", 1),
      at("RERANKING", 5),
      at("SEARCHING_COUNTER_EVIDENCE", 12),
      at("CHECKING_EVIDENCE_COMPLETENESS", 14),
      at("VERIFYING", 15),
      at("ANSWER_READY", 18),
    ]);

    // Entered retrieval at 1s, left it at 12s. Not the 0ms between CONTEXT_EXTRACTED and
    // RETRIEVING, which is what the handoff measured.
    expect(stepRow(1)).toHaveTextContent("11.0s");
    expect(stepRow(1)).not.toHaveTextContent("0ms");
    expect(stepRow(2)).toHaveTextContent("2.0s");
    expect(stepRow(3)).toHaveTextContent("1.0s");
    expect(stepRow(4)).toHaveTextContent("3.0s");
  });

  it("reports milliseconds for a stage that really was quick", () => {
    renderPanel([
      at("CONTEXT_EXTRACTED", 0),
      at("RETRIEVING", 0),
      { status: "SEARCHING_COUNTER_EVIDENCE", occurredAt: new Date(START + 40).toISOString() },
      at("VERIFYING", 1),
      at("ANSWER_READY", 2),
    ]);

    expect(stepRow(1)).toHaveTextContent("40ms");
  });

  /*
   * A stage with no event after it is still in it, and a number that grows while a reader
   * looks at it is not a duration - so the row falls back to the time it started.
   */
  it("gives no duration for a stage the run has not left", () => {
    renderPanel([at("CONTEXT_EXTRACTED", 0), at("RETRIEVING", 1)]);

    expect(stepRow(1)).not.toHaveTextContent(/\d+(\.\d+)?s$/);
    expect(stepRow(1)).not.toHaveTextContent("ms");
  });

  /*
   * The only marker before `CONTEXT_EXTRACTED` is `QUEUED`, and the gap between them is
   * mostly time spent waiting to start rather than time spent extracting.
   */
  it("does not time the first stage against the queue", () => {
    renderPanel([at("QUEUED", 0), at("CONTEXT_EXTRACTED", 30), at("RETRIEVING", 30)]);

    expect(stepRow(0)).not.toHaveTextContent("30.0s");
  });

  it("says nothing about a stage the run never reached", () => {
    renderPanel([at("CONTEXT_EXTRACTED", 0), at("RETRIEVING", 1), at("ANSWER_READY", 2)]);

    // Counter-evidence was never entered, so there is no span to report for it.
    expect(stepRow(2)).not.toHaveTextContent("ms");
  });
});

describe("VerificationPanel, what each stage concluded", () => {
  it("names the gates and their outcome", () => {
    renderPanel([at("CONTEXT_EXTRACTED", 0), at("RETRIEVING", 1), at("ANSWER_READY", 2)]);

    expect(screen.getByText("Context extracted")).toBeInTheDocument();
    expect(screen.getByText("Retrieval complete")).toBeInTheDocument();
    expect(screen.getByText("Answer gate passed")).toBeInTheDocument();
  });

  it("reports a withheld answer as a blocked gate rather than a passed one", () => {
    render(
      <VerificationPanel
        isRunning={false}
        lifecycle="abstained"
        progressEvents={[at("CONTEXT_EXTRACTED", 0)]}
        result={{ ...answerReadyResult(), status: "ABSTAINED" }}
        status="ABSTAINED"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Answer gate blocked");
  });
});
