import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  forgetRuns,
  recentRuns,
  recordRun,
  runsSnapshot,
  serverRunsSnapshot,
  subscribeRuns,
} from "@/lib/run-history";

const KEY = "evidence-workspace.recent-runs";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("run history", () => {
  it("remembers a run so a reader can get back to it", () => {
    recordRun("q-1", "How often should viral load be monitored?");

    expect(recentRuns()).toMatchObject([
      { questionId: "q-1", question: "How often should viral load be monitored?" },
    ]);
  });

  it("puts the newest first", () => {
    recordRun("q-1", "First");
    recordRun("q-2", "Second");

    expect(recentRuns().map((run) => run.questionId)).toEqual(["q-2", "q-1"]);
  });

  /*
   * Keyed by identifier rather than appended: re-opening a review is the common case, and
   * appending would fill the list with one review.
   */
  it("moves a run already known to the front rather than repeating it", () => {
    recordRun("q-1", "First");
    recordRun("q-2", "Second");
    recordRun("q-1", "First");

    expect(recentRuns().map((run) => run.questionId)).toEqual(["q-1", "q-2"]);
  });

  it("keeps the list short enough to scan", () => {
    for (let index = 0; index < 12; index += 1) recordRun(`q-${index}`, `Question ${index}`);

    const runs = recentRuns();
    expect(runs).toHaveLength(8);
    expect(runs[0]!.questionId).toBe("q-11");
  });

  it("records a run whose question is not known yet", () => {
    // A review adopted from a shared link is worth listing before its question arrives.
    recordRun("q-1", "");

    expect(recentRuns()).toMatchObject([{ questionId: "q-1", question: "" }]);
  });

  it("ignores a run with no identifier, which is not an address", () => {
    recordRun("", "Question");

    expect(recentRuns()).toEqual([]);
  });

  it("forgets everything when asked", () => {
    recordRun("q-1", "First");
    forgetRuns();

    expect(recentRuns()).toEqual([]);
  });
});

describe("run history, when storage cannot be trusted", () => {
  /*
   * A history is a convenience, and a convenience that can break the workspace is not
   * one. Every unreadable state answers with an empty list rather than throwing into the
   * render that asked.
   */
  it("answers empty for a value that is not a list", () => {
    window.localStorage.setItem(KEY, JSON.stringify({ questionId: "q-1" }));

    expect(recentRuns()).toEqual([]);
  });

  it("answers empty for a value that is not JSON", () => {
    window.localStorage.setItem(KEY, "{not json");

    expect(recentRuns()).toEqual([]);
  });

  it("drops entries an older version of this code might have written", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify([{ questionId: "q-1", question: "Kept", openedAt: "2026-08-28" }, { id: "q-2" }]),
    );

    expect(recentRuns().map((run) => run.questionId)).toEqual(["q-1"]);
  });

  it("survives a browser that refuses storage outright", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
      removeItem: () => {
        throw new Error("SecurityError");
      },
    });

    expect(recentRuns()).toEqual([]);
    expect(() => recordRun("q-1", "Question")).not.toThrow();
    expect(() => forgetRuns()).not.toThrow();
  });
});

describe("run history, as a store", () => {
  /*
   * `useSyncExternalStore` compares snapshots by identity, so a fresh array on every read
   * would re-render forever. The cache is what makes the subscription usable at all.
   */
  it("hands back the same snapshot until something changes it", () => {
    recordRun("q-1", "First");
    const first = runsSnapshot();

    expect(runsSnapshot()).toBe(first);

    recordRun("q-2", "Second");
    expect(runsSnapshot()).not.toBe(first);
    expect(runsSnapshot().map((run) => run.questionId)).toEqual(["q-2", "q-1"]);
  });

  it("tells subscribers when a run is recorded or the list is cleared", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeRuns(listener);

    recordRun("q-1", "First");
    expect(listener).toHaveBeenCalledTimes(1);

    forgetRuns();
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    recordRun("q-2", "Second");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("picks up a run another tab recorded", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeRuns(listener);

    window.dispatchEvent(
      new StorageEvent("storage", { key: "evidence-workspace.recent-runs" }),
    );

    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("gives the server an empty list, stably", () => {
    // Stable identity matters here too: this is the snapshot hydration compares against.
    expect(serverRunsSnapshot()).toEqual([]);
    expect(serverRunsSnapshot()).toBe(serverRunsSnapshot());
  });
});
