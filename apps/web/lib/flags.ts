/**
 * Flags a reader raises on a claim while reading a review.
 *
 * A flag is the reader saying "this claim and this passage do not agree" at the moment
 * they see it, which is exactly the judgement the labelling census asks for later. Flags
 * stay in this browser, are listed on the Reviews page, and can be exported as JSON to sit
 * beside a label file. They never change the review itself.
 */

export const FLAG_REASONS = [
  ["wrong_passage", "The cited passage does not say this"],
  ["misread", "The claim misreads the passage"],
  ["missing_condition", "A condition in the guideline is missing"],
  ["out_of_date", "The edition is out of date"],
  ["other", "Something else"],
] as const;

export type FlagReason = (typeof FLAG_REASONS)[number][0];

export interface ClaimFlag {
  questionId: string;
  claimId: string;
  claimText: string;
  evidenceIds: string[];
  reason: FlagReason;
  note: string;
  raisedAt: string;
}

const STORAGE_KEY = "sentinel-rag.flags.v1";
const listeners = new Set<() => void>();
let cached: ClaimFlag[] | null = null;
const EMPTY: ClaimFlag[] = [];

function read(): ClaimFlag[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as ClaimFlag[]) : [];
  } catch {
    return [];
  }
}

function write(flags: ClaimFlag[]): void {
  cached = flags;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(flags));
  } catch {
    // The flag still shows for this page.
  }
  for (const listener of listeners) listener();
}

export function flagsSnapshot(): ClaimFlag[] {
  if (cached === null) cached = read();
  return cached;
}

export function serverFlagsSnapshot(): ClaimFlag[] {
  return EMPTY;
}

export function subscribeFlags(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function flagFor(questionId: string, claimId: string): ClaimFlag | null {
  return flagsSnapshot().find((flag) => flag.questionId === questionId && flag.claimId === claimId) ?? null;
}

export function raiseFlag(flag: Omit<ClaimFlag, "raisedAt">): void {
  const others = flagsSnapshot().filter(
    (item) => !(item.questionId === flag.questionId && item.claimId === flag.claimId),
  );
  write([{ ...flag, raisedAt: new Date().toISOString() }, ...others]);
}

export function withdrawFlag(questionId: string, claimId: string): void {
  write(flagsSnapshot().filter((item) => !(item.questionId === questionId && item.claimId === claimId)));
}

export function clearFlags(): void {
  write([]);
}

export function reasonLabel(reason: FlagReason): string {
  return FLAG_REASONS.find(([value]) => value === reason)?.[1] ?? reason;
}
