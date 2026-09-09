import { describe, expect, it, vi } from "vitest";

import { withRetries } from "./readiness";

const noSleep = () => Promise.resolve();
const sleepSpy = () => vi.fn<(ms: number) => Promise<void>>(noSleep);

describe("withRetries", () => {
  it("returns the first success without waiting", async () => {
    const sleep = sleepSpy();
    const check = vi.fn(async () => "ready");
    await expect(withRetries(check, { delays: [10, 20], sleep })).resolves.toBe("ready");
    expect(check).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("retries on the configured delays and returns a late success", async () => {
    const sleep = sleepSpy();
    const check = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("refused"))
      .mockRejectedValueOnce(new Error("refused"))
      .mockResolvedValueOnce("ready");
    await expect(withRetries(check, { delays: [10, 20, 30], sleep })).resolves.toBe("ready");
    expect(check).toHaveBeenCalledTimes(3);
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([10, 20]);
  });

  it("gives up with the last error once the delays are spent", async () => {
    const check = vi.fn(async () => {
      throw new Error("still refused");
    });
    await expect(withRetries(check, { delays: [1, 2], sleep: noSleep })).rejects.toThrow(
      "still refused",
    );
    expect(check).toHaveBeenCalledTimes(3);
  });

  it("stops early when the caller has moved on", async () => {
    let active = true;
    const check = vi.fn(async () => {
      active = false;
      throw new Error("refused");
    });
    await expect(
      withRetries(check, { delays: [1, 2, 3], isActive: () => active, sleep: noSleep }),
    ).rejects.toThrow("refused");
    expect(check).toHaveBeenCalledTimes(1);
  });
});
