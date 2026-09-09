/**
 * Retry a readiness check before calling the service unreachable.
 *
 * One failed request used to pin "unavailable" on the page until a reload. An API still
 * starting, a laptop waking, a dropped connection: each read as a broken corpus, and the
 * reader was told the gate would fail closed when nothing had been asked yet. The check is
 * now retried on a short backoff, and the caller shows "checking" for as long as retries
 * are running, so the unreachable state is reached only after the last attempt.
 */

/** Waits between attempts: about half a minute in total before giving up. */
export const READINESS_RETRY_DELAYS_MS = [1_000, 3_000, 8_000, 15_000];

export interface RetryOptions {
  delays?: readonly number[];
  /** Returns false once the caller has moved on, which ends the retries early. */
  isActive?: () => boolean;
  sleep?: (ms: number) => Promise<void>;
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export async function withRetries<T>(
  check: () => Promise<T>,
  { delays = READINESS_RETRY_DELAYS_MS, isActive = () => true, sleep = defaultSleep }: RetryOptions = {},
): Promise<T> {
  let lastError: unknown = new Error("The readiness check was cancelled.");
  for (let attempt = 0; attempt <= delays.length; attempt += 1) {
    if (!isActive()) throw lastError;
    try {
      return await check();
    } catch (error) {
      lastError = error;
    }
    if (attempt < delays.length) await sleep(delays[attempt]!);
  }
  throw lastError;
}
