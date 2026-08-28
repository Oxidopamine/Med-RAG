/**
 * The reviews this browser has opened, so a reader can get back to one.
 *
 * A run already has an address - `/r/{questionId}` - and until now nothing wrote it
 * down. Keeping the tab open was the only way back to a review that took minutes to
 * produce, which made the tool feel like a demo rather than something to work in.
 *
 * Deliberately local, and deliberately thin. The question text and its identifier are
 * what a reader needs to recognise a run; nothing about the answer, the context, or the
 * evidence is stored, because none of it is needed to *find* the run and all of it would
 * be a copy of clinical material sitting in browser storage outside the licence and
 * retention story the API is responsible for. The server remains the only place a result
 * lives.
 */

const STORAGE_KEY = "evidence-workspace.recent-runs";
const MAXIMUM_RUNS = 8;
/** Long enough to hold a real clinical question, short enough not to hold a document. */
const MAXIMUM_QUESTION_LENGTH = 400;

export interface RecordedRun {
  questionId: string;
  question: string;
  /** ISO 8601, in UTC. */
  openedAt: string;
}

function isRecordedRun(value: unknown): value is RecordedRun {
  if (typeof value !== "object" || value === null) return false;
  const run = value as Partial<RecordedRun>;
  return (
    typeof run.questionId === "string" &&
    run.questionId.length > 0 &&
    typeof run.question === "string" &&
    typeof run.openedAt === "string"
  );
}

/**
 * The runs this browser has opened, newest first.
 *
 * Every failure answers with an empty list: storage a browser refuses, a value another
 * version of this code wrote, a payload someone edited by hand. A history is a
 * convenience, and a convenience that can break the workspace is not one.
 */
export function recentRuns(): RecordedRun[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecordedRun).slice(0, MAXIMUM_RUNS);
  } catch {
    return [];
  }
}

/**
 * Record a run, or move it to the front if it is already known.
 *
 * Keyed by identifier rather than appended, so re-opening a review does not fill the
 * list with one review. The question is stored as it was asked, because that is what the
 * reader will recognise; an empty one is kept rather than rejected, since a run adopted
 * from a shared link is worth listing before its question has been fetched.
 */
export function recordRun(questionId: string, question: string): RecordedRun[] {
  if (typeof window === "undefined" || !questionId) return [];
  const entry: RecordedRun = {
    questionId,
    question: question.trim().slice(0, MAXIMUM_QUESTION_LENGTH),
    openedAt: new Date().toISOString(),
  };
  const kept = [entry, ...recentRuns().filter((run) => run.questionId !== questionId)].slice(
    0,
    MAXIMUM_RUNS,
  );
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // A browser that refuses storage still gets the workspace; it just has no history.
  }
  invalidate();
  return kept;
}

export function forgetRuns(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do: there was nothing readable to forget.
  }
  invalidate();
}

/* ------------------------------------------------------------------- as a store -- */

/**
 * The history as something a component can subscribe to.
 *
 * This exists because of where the value lives. The list is in one browser, so a server
 * render cannot know it; reading it in an effect and calling `setState` would work but is
 * the cascading-render pattern React asks callers not to write, and reading it during
 * render would make the client's first paint disagree with the server's markup. An
 * external store is the shape React provides for exactly this: an empty list is the server
 * snapshot, storage is the client snapshot, and hydration crosses between them once.
 *
 * The snapshot is cached rather than re-read, because `useSyncExternalStore` compares
 * snapshots by identity and a fresh array every call would never stop re-rendering.
 */
let cached: RecordedRun[] | null = null;
const listeners = new Set<() => void>();

function invalidate(): void {
  cached = null;
  for (const listener of listeners) listener();
}

function onStorageEvent(event: StorageEvent): void {
  // A null key is a `clear()`, which takes this list with it.
  if (event.key === null || event.key === STORAGE_KEY) invalidate();
}

export function runsSnapshot(): RecordedRun[] {
  if (cached === null) cached = recentRuns();
  return cached;
}

/** A stable empty list: the server has no browser to read, and says so consistently. */
const NO_RUNS: RecordedRun[] = [];

export function serverRunsSnapshot(): RecordedRun[] {
  return NO_RUNS;
}

/**
 * Watch the history, including changes another tab makes.
 *
 * Two tabs open on this workspace is the ordinary case for someone comparing reviews, and
 * a run started in one of them belongs in the other's list.
 */
export function subscribeRuns(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1 && typeof window !== "undefined") {
    window.addEventListener("storage", onStorageEvent);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined") {
      window.removeEventListener("storage", onStorageEvent);
    }
  };
}
