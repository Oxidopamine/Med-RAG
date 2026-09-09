/**
 * The labelling census, as the pre-registered plan defines it.
 *
 * This module is a port of the selection and ordering logic of
 * `scripts/build_claim_audit_worksheet.py`: the same runs, the same seeded orderings,
 * the same label file. It exists so the three passes can be labelled in the browser
 * with the passage pinned beside the claim, and so the file that comes out is the one
 * `cm_statistics.py` reads. Nothing here leaves the browser: runs, bundle and labels
 * are read from files the annotator chooses, and the labels are written back as a file.
 *
 * The random source is Python's: a Mersenne Twister seeded the way `random.Random(int)`
 * seeds it, with `shuffle` and `sample` reproduced step for step, so the item numbers
 * here match the worksheets and key files the script writes for the same seed.
 */

export const ARMS = ["production", "closed_book", "naive"] as const;
export type Arm = (typeof ARMS)[number];

export const ATTRIBUTION_LABELS = [
  "ATTRIBUTABLE",
  "EXTRAPOLATORY",
  "CONTRADICTORY",
  "NO_SUPPORT",
  "UNLABELABLE",
] as const;
export const AGREEMENT_LABELS = [
  "AGREES",
  "PARTIAL",
  "DISAGREES",
  "NOT_ADJUDICABLE",
  "UNLABELABLE",
] as const;
export const ELIGIBILITY_CONDITIONS = [
  "pregnancy",
  "breastfeeding",
  "paediatric",
  "adolescent",
  "renal_impairment",
  "hepatic_impairment",
  "tb_coinfection",
  "hepatitis_b",
  "prior_treatment_failure",
  "advanced_disease",
  "setting_or_resource",
] as const;
export const MISLEAD_EXTENTS = ["NONE", "MILD", "MODERATE", "SEVERE"] as const;
export const MISLEAD_LIKELIHOODS = ["LOW", "MEDIUM", "HIGH"] as const;
const NON_ATTRIBUTABLE = new Set(["EXTRAPOLATORY", "CONTRADICTORY", "NO_SUPPORT"]);

export type AttributionLabel = (typeof ATTRIBUTION_LABELS)[number];
export type AgreementLabel = (typeof AGREEMENT_LABELS)[number];

// ---------------------------------------------------------------------------------------
// Python's random, reproduced
// ---------------------------------------------------------------------------------------

const N = 624;
const M = 397;

/** Mersenne Twister 19937, seeded and driven the way CPython's `random.Random` does it. */
export class PythonRandom {
  private readonly mt = new Uint32Array(N);
  private index = N + 1;

  constructor(seed: number) {
    if (!Number.isInteger(seed) || seed < 0 || seed > 0xffffffff) {
      throw new RangeError("seed must be an integer between 0 and 2^32 - 1");
    }
    this.initByArray([seed >>> 0]);
  }

  private initGenrand(s: number): void {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < N; i += 1) {
      const previous = mt[i - 1]! ^ (mt[i - 1]! >>> 30);
      mt[i] = (Math.imul(1812433253, previous) + i) >>> 0;
    }
    this.index = N;
  }

  private initByArray(key: number[]): void {
    const mt = this.mt;
    this.initGenrand(19650218);
    let i = 1;
    let j = 0;
    for (let k = Math.max(N, key.length); k > 0; k -= 1) {
      const previous = mt[i - 1]! ^ (mt[i - 1]! >>> 30);
      mt[i] = ((mt[i]! ^ Math.imul(previous, 1664525)) + key[j]! + j) >>> 0;
      i += 1;
      j += 1;
      if (i >= N) {
        mt[0] = mt[N - 1]!;
        i = 1;
      }
      if (j >= key.length) j = 0;
    }
    for (let k = N - 1; k > 0; k -= 1) {
      const previous = mt[i - 1]! ^ (mt[i - 1]! >>> 30);
      mt[i] = ((mt[i]! ^ Math.imul(previous, 1566083941)) - i) >>> 0;
      i += 1;
      if (i >= N) {
        mt[0] = mt[N - 1]!;
        i = 1;
      }
    }
    mt[0] = 0x80000000;
  }

  private twist(): void {
    const mt = this.mt;
    for (let kk = 0; kk < N; kk += 1) {
      const y = (mt[kk]! & 0x80000000) | (mt[(kk + 1) % N]! & 0x7fffffff);
      mt[kk] = (mt[(kk + M) % N]! ^ (y >>> 1) ^ (y & 1 ? 0x9908b0df : 0)) >>> 0;
    }
    this.index = 0;
  }

  genrandUint32(): number {
    if (this.index >= N) this.twist();
    let y = this.mt[this.index]!;
    this.index += 1;
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  /** `getrandbits(k)` for 0 < k <= 32. */
  getrandbits(k: number): number {
    if (k <= 0 || k > 32) throw new RangeError("getrandbits supports 1 to 32 bits here");
    return this.genrandUint32() >>> (32 - k);
  }

  /** `_randbelow(n)`: an integer in [0, n), by rejection on the next power of two. */
  randbelow(n: number): number {
    if (n <= 0) throw new RangeError("randbelow needs n > 0");
    const k = n.toString(2).length;
    let r = this.getrandbits(k);
    while (r >= n) r = this.getrandbits(k);
    return r;
  }

  /** `random.shuffle(x)`, in place. */
  shuffle<T>(items: T[]): T[] {
    for (let i = items.length - 1; i > 0; i -= 1) {
      const j = this.randbelow(i + 1);
      const swap = items[i]!;
      items[i] = items[j]!;
      items[j] = swap;
    }
    return items;
  }

  /** `random.sample(population, k)`, with CPython's choice of pool or set method. */
  sample<T>(population: readonly T[], k: number): T[] {
    const n = population.length;
    if (k < 0 || k > n) throw new RangeError("sample larger than population");
    let setsize = 21;
    if (k > 5) setsize += 4 ** Math.ceil(Math.log(k * 3) / Math.log(4));
    const result: T[] = new Array<T>(k);
    if (n <= setsize) {
      const pool = [...population];
      for (let i = 0; i < k; i += 1) {
        const j = this.randbelow(n - i);
        result[i] = pool[j]!;
        pool[j] = pool[n - i - 1]!;
      }
      return result;
    }
    const selected = new Set<number>();
    for (let i = 0; i < k; i += 1) {
      let j = this.randbelow(n);
      while (selected.has(j)) j = this.randbelow(n);
      selected.add(j);
      result[i] = population[j]!;
    }
    return result;
  }
}

// ---------------------------------------------------------------------------------------
// The files
// ---------------------------------------------------------------------------------------

export interface RunPassage {
  evidence_id: string;
  rendered_text?: string;
  rendered_text_truncated?: boolean;
  kind?: string;
  qualified_roles?: string[];
  evidence_roles?: string[];
  source_version_id?: string;
}

export interface RunClaim {
  text: string;
  evidence_ids?: string[];
}

export interface RunRecord {
  question_id: string;
  question: string;
  chapter_no?: number;
  chapter?: string;
  gate_reason?: string | null;
  retrieval?: { passages?: RunPassage[] } | null;
  generation?: {
    abstained?: boolean;
    claims?: RunClaim[];
    message?: string;
    error?: unknown;
    reason_code?: string;
  } | null;
}

export interface RunFile {
  run_label?: string;
  question_set?: { sha256?: string } | null;
  results: RunRecord[];
}

export interface QuestionSet {
  items: Array<{ question_id: string; source_statement?: string | null }>;
}

export interface Passage extends RunPassage {
  text_source: "bundle" | "run";
}

export type Outcome = "ANSWERED" | "ABSTAINED" | "ERROR";

export function recordOutcome(record: RunRecord): Outcome {
  const generation = record.generation;
  if (!generation) return "ABSTAINED";
  if ("error" in generation || generation.reason_code === "GENERATION_UNAVAILABLE") return "ERROR";
  return generation.abstained ? "ABSTAINED" : "ANSWERED";
}

export function labelKey(questionId: string, arm: Arm): string {
  return arm === "production" ? questionId : `${questionId}:${arm}`;
}

export function seededOrder<T>(items: Iterable<T>, seed: number | null, key: (item: T) => string): T[] {
  const ordered = [...items].sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  if (seed === null) return ordered;
  return new PythonRandom(seed).shuffle(ordered);
}

export function abstentionReviewSelection(
  records: RunRecord[],
  sampleSize: number,
  seed: number | null,
): { gateBlocked: string[]; sampled: string[]; modelDeclaredTotal: number } {
  const gateBlocked = records
    .filter((record) => recordOutcome(record) === "ABSTAINED" && !record.generation)
    .map((record) => record.question_id)
    .sort();
  const modelDeclared = records
    .filter((record) => recordOutcome(record) === "ABSTAINED" && Boolean(record.generation))
    .map((record) => record.question_id)
    .sort();
  const size = Math.min(sampleSize, modelDeclared.length);
  const sampled =
    size && seed !== null ? new PythonRandom(seed).sample(modelDeclared, size).sort() : [];
  return { gateBlocked, sampled, modelDeclaredTotal: modelDeclared.length };
}

export function answeredRecordSample(
  records: RunRecord[],
  sampleSize: number | null,
  seed: number | null,
): RunRecord[] {
  const answered = records.filter((record) => recordOutcome(record) === "ANSWERED");
  if (sampleSize === null || sampleSize >= answered.length || seed === null) return answered;
  const ids = answered.map((record) => record.question_id).sort();
  const chosen = new Set(new PythonRandom(seed).sample(ids, sampleSize));
  return answered.filter((record) => chosen.has(record.question_id));
}

// ---------------------------------------------------------------------------------------
// The census
// ---------------------------------------------------------------------------------------

export interface ClaimItem {
  arm: Arm;
  questionId: string;
  key: string;
  index: number;
  record: RunRecord;
  claim: RunClaim;
}

export class Census {
  readonly runs: Partial<Record<Arm, RunFile>>;
  readonly gold: Map<string, string>;
  readonly renderFull: ((evidenceId: string) => string | null) | null;
  readonly seed: number | null;
  readonly production: Map<string, RunRecord>;
  readonly gateBlocked: string[];
  readonly sampled: string[];
  readonly modelDeclaredTotal: number;
  readonly reviewIds: Set<string>;
  readonly answered: RunRecord[];
  readonly answeredIds: Set<string>;
  readonly abstentionSampleSize: number;

  constructor(
    runs: Partial<Record<Arm, RunFile>>,
    options: {
      questions?: QuestionSet | null;
      renderFull?: ((evidenceId: string) => string | null) | null;
      shuffleSeed: number | null;
      abstentionSample?: number;
      recordSample?: number | null;
    },
  ) {
    const production = runs.production;
    if (!production) throw new Error("the census needs a production run");
    this.runs = runs;
    this.gold = new Map(
      (options.questions?.items ?? []).map((item) => [item.question_id, item.source_statement ?? ""]),
    );
    this.renderFull = options.renderFull ?? null;
    this.seed = options.shuffleSeed;
    this.abstentionSampleSize = options.abstentionSample ?? 20;
    this.production = new Map(production.results.map((record) => [record.question_id, record]));
    const selection = abstentionReviewSelection(production.results, this.abstentionSampleSize, this.seed);
    this.gateBlocked = selection.gateBlocked;
    this.sampled = selection.sampled;
    this.modelDeclaredTotal = selection.modelDeclaredTotal;
    this.reviewIds = new Set([...this.gateBlocked, ...this.sampled]);
    this.answered = answeredRecordSample(production.results, options.recordSample ?? null, this.seed);
    this.answeredIds = new Set(this.answered.map((record) => record.question_id));
  }

  goldStatement(questionId: string): string {
    return this.gold.get(questionId) || "(no gold statement: load the question set)";
  }

  /** Answered records of an arm that enter the census, as the plan's Section 3.3 draws them. */
  armRecords(arm: Arm): RunRecord[] {
    const run = this.runs[arm];
    if (!run) return [];
    const records = run.results.filter((record) => recordOutcome(record) === "ANSWERED");
    if (arm === "production") return records.filter((record) => this.answeredIds.has(record.question_id));
    if (arm === "naive") return records.filter((record) => this.reviewIds.has(record.question_id));
    return records;
  }

  passages(record: RunRecord): Map<string, Passage> {
    const passages = new Map<string, Passage>();
    for (const passage of record.retrieval?.passages ?? []) {
      const full = this.renderFull ? this.renderFull(passage.evidence_id) : null;
      passages.set(
        passage.evidence_id,
        full !== null
          ? { ...passage, rendered_text: full, rendered_text_truncated: false, text_source: "bundle" }
          : { ...passage, text_source: "run" },
      );
    }
    return passages;
  }

  claimItems(): ClaimItem[] {
    const items: ClaimItem[] = [];
    for (const arm of ARMS) {
      for (const record of this.armRecords(arm)) {
        (record.generation?.claims ?? []).forEach((claim, position) => {
          items.push({
            arm,
            questionId: record.question_id,
            key: labelKey(record.question_id, arm),
            index: position + 1,
            record,
            claim,
          });
        });
      }
    }
    return items;
  }
}

// ---------------------------------------------------------------------------------------
// The passes
// ---------------------------------------------------------------------------------------

export interface AgreementItem {
  id: string;
  key: string;
  arm: Arm;
  index: number;
  questionId: string;
  question: string;
  gold: string;
  claimText: string;
}

/** Pass A: claim, question and gold statement only; arms interleaved and concealed. */
export function agreementItems(census: Census): AgreementItem[] {
  const items = seededOrder(
    census.claimItems(),
    census.seed,
    (item) => `${item.key}#${String(item.index).padStart(3, "0")}`,
  );
  return items.map((item, position) => ({
    id: `A-${String(position + 1).padStart(3, "0")}`,
    key: item.key,
    arm: item.arm,
    index: item.index,
    questionId: item.questionId,
    question: item.record.question,
    gold: census.goldStatement(item.questionId),
    claimText: item.claim.text,
  }));
}

/** The question-validity judgements of Pass A: one per question, whatever arms answered it. */
export function questionValidityItems(census: Census): Array<{ questionId: string; question: string; gold: string; keys: string[] }> {
  const byQuestion = new Map<string, { question: string; keys: Set<string> }>();
  for (const item of census.claimItems()) {
    const entry = byQuestion.get(item.questionId) ?? { question: item.record.question, keys: new Set<string>() };
    entry.keys.add(item.key);
    byQuestion.set(item.questionId, entry);
  }
  return [...byQuestion.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([questionId, entry]) => ({
      questionId,
      question: entry.question,
      gold: census.goldStatement(questionId),
      keys: [...entry.keys].sort(),
    }));
}

export interface AttributionRecord {
  key: string;
  arm: Arm;
  questionId: string;
  chapter: string;
  question: string;
  passages: Map<string, Passage>;
  claims: Array<{ index: number; text: string; cited: string[] }>;
}

/** Pass B: production (and naive) claims with their full cited passages, no gold statement. */
export function attributionRecords(census: Census): AttributionRecord[] {
  const records: AttributionRecord[] = [];
  for (const arm of ["production", "naive"] as const) {
    const ordered = seededOrder(census.armRecords(arm), census.seed, (record) => record.question_id);
    for (const record of ordered) {
      records.push({
        key: labelKey(record.question_id, arm),
        arm,
        questionId: record.question_id,
        chapter: record.chapter_no !== undefined ? `ch${record.chapter_no} ${record.chapter ?? ""}`.trim() : "",
        question: record.question,
        passages: census.passages(record),
        claims: (record.generation?.claims ?? []).map((claim, position) => ({
          index: position + 1,
          text: claim.text,
          cited: [...new Set(claim.evidence_ids ?? [])],
        })),
      });
    }
  }
  return records;
}

export interface AbstentionItem {
  questionId: string;
  gateBlocked: boolean;
  chapter: string;
  question: string;
  gold: string;
  gateReason: string | null;
  modelSaid: string;
  passages: Passage[];
}

/** The abstention adjudications: every gate-blocked record and the seeded sample, reshuffled. */
export function abstentionItems(census: Census): AbstentionItem[] {
  const ordered = seededOrder(
    [...census.gateBlocked, ...census.sampled],
    census.seed === null ? null : census.seed + 1,
    (questionId) => questionId,
  );
  return ordered.flatMap((questionId) => {
    const record = census.production.get(questionId);
    if (!record) return [];
    const gateBlocked = census.gateBlocked.includes(questionId);
    return [
      {
        questionId,
        gateBlocked,
        chapter: record.chapter_no !== undefined ? `ch${record.chapter_no} ${record.chapter ?? ""}`.trim() : "",
        question: record.question,
        gold: census.goldStatement(questionId),
        gateReason: gateBlocked ? (record.gate_reason ?? null) : null,
        modelSaid: gateBlocked ? "" : (record.generation?.message ?? "").trim(),
        passages: [...census.passages(record).values()],
      },
    ];
  });
}

// ---------------------------------------------------------------------------------------
// The label file
// ---------------------------------------------------------------------------------------

export interface ClaimLabel {
  index: number;
  pair_attribution: Record<string, AttributionLabel | null>;
  pair_text_source: Record<string, "bundle" | "run">;
  joint_attribution: AttributionLabel | null;
  agreement: AgreementLabel | null;
  eligibility_drop: boolean | null;
  eligibility_condition: string | null;
  mislead: { extent: string; likelihood: string; reason: string } | null;
  note: string;
}

export interface RecordLabel {
  arm: Arm;
  claims: ClaimLabel[];
  presentation_defect: boolean | null;
  question_valid: boolean | null;
  note: string;
}

export interface PassStamp {
  started_at: string | null;
  finished_at: string | null;
}

export interface LabelFile {
  schema_version: 1;
  run: { local: string | null; published: string | null; question_set_sha256: string | null; run_label: string | null };
  arms: Partial<Record<Arm, string>>;
  annotator: string | null;
  rubric_sha256: string | null;
  shuffle_seed: number | null;
  passes: { agreement: PassStamp; attribution: PassStamp; mislead: PassStamp };
  records: Record<string, RecordLabel>;
  gate_blocked: Record<string, { gate_right: boolean | null; dak_has_answer: boolean | null; note: string }>;
  abstained_sample: { size: number; seed: number | null; model_declared_total: number } & Record<
    string,
    { any_retrieved_passage_answers: boolean | null; question_valid: boolean | null } | number | null
  >;
  pair_text_sources: { bundle: number; run: number };
  census_valid: boolean;
}

/** The label skeleton of plan Section 4.3, every label null, every text source recorded. */
export function emptyLabelFile(
  census: Census,
  meta: { runPaths: Partial<Record<Arm, string>>; rubricSha256: string | null; annotator?: string | null },
): LabelFile {
  const records: Record<string, RecordLabel> = {};
  for (const arm of ARMS) {
    for (const record of census.armRecords(arm)) {
      const passages = census.passages(record);
      const claims: ClaimLabel[] = (record.generation?.claims ?? []).map((claim, position) => {
        const cited = [...new Set(claim.evidence_ids ?? [])];
        return {
          index: position + 1,
          pair_attribution: Object.fromEntries(cited.map((id) => [id, null])),
          pair_text_source: Object.fromEntries(
            cited.map((id) => [id, passages.get(id)?.text_source ?? "run"]),
          ),
          joint_attribution: null,
          agreement: null,
          eligibility_drop: null,
          eligibility_condition: null,
          mislead: null,
          note: "",
        };
      });
      records[labelKey(record.question_id, arm)] = {
        arm,
        claims,
        presentation_defect: null,
        question_valid: null,
        note: "",
      };
    }
  }
  const production = census.runs.production!;
  const file: LabelFile = {
    schema_version: 1,
    run: {
      local: meta.runPaths.production ?? null,
      published: null,
      question_set_sha256: production.question_set?.sha256 ?? null,
      run_label: production.run_label ?? null,
    },
    arms: Object.fromEntries(
      Object.entries(meta.runPaths).filter(([arm]) => arm !== "production"),
    ) as Partial<Record<Arm, string>>,
    annotator: meta.annotator ?? null,
    rubric_sha256: meta.rubricSha256,
    shuffle_seed: census.seed,
    passes: {
      agreement: { started_at: null, finished_at: null },
      attribution: { started_at: null, finished_at: null },
      mislead: { started_at: null, finished_at: null },
    },
    records,
    gate_blocked: Object.fromEntries(
      census.gateBlocked.map((id) => [id, { gate_right: null, dak_has_answer: null, note: "" }]),
    ),
    abstained_sample: {
      size: census.sampled.length,
      seed: census.seed,
      model_declared_total: census.modelDeclaredTotal,
      ...Object.fromEntries(
        census.sampled.map((id) => [id, { any_retrieved_passage_answers: null, question_valid: null }]),
      ),
    },
    pair_text_sources: { bundle: 0, run: 0 },
    census_valid: true,
  };
  return withTextSourceCounts(file);
}

export function withTextSourceCounts(file: LabelFile): LabelFile {
  const sources = { bundle: 0, run: 0 };
  for (const record of Object.values(file.records)) {
    for (const claim of record.claims) {
      for (const source of Object.values(claim.pair_text_source)) sources[source] += 1;
    }
  }
  return { ...file, pair_text_sources: sources, census_valid: sources.run === 0 };
}

/** The five trigger classes of Section 1.1 that hold for a claim, in a fixed order. */
export function claimTriggers(claim: ClaimLabel, record: RecordLabel): string[] {
  const triggers: string[] = [];
  if (claim.joint_attribution && NON_ATTRIBUTABLE.has(claim.joint_attribution)) triggers.push("joint_non_attributable");
  if (Object.values(claim.pair_attribution).some((label) => label && NON_ATTRIBUTABLE.has(label))) {
    triggers.push("pair_non_attributable");
  }
  if (claim.agreement === "DISAGREES" || claim.agreement === "PARTIAL") triggers.push("disagreement");
  if (claim.eligibility_drop) triggers.push("eligibility_drop");
  if (record.presentation_defect) triggers.push("presentation_defect");
  return triggers;
}

export interface MisleadItem {
  id: string;
  key: string;
  index: number;
  status: "flagged" | "control";
  questionId: string;
  question: string;
  gold: string;
  claimText: string;
}

/** Pass C: every flagged claim and seeded controls, trigger status and earlier labels hidden. */
export function misleadItems(census: Census, labels: LabelFile, controls = 30): MisleadItem[] {
  const flagged: Array<[string, number]> = [];
  const clean: Array<[string, number]> = [];
  for (const [key, record] of Object.entries(labels.records)) {
    for (const claim of record.claims) {
      (claimTriggers(claim, record).length ? flagged : clean).push([key, claim.index]);
    }
  }
  const sortPairs = (pairs: Array<[string, number]>) =>
    [...pairs].sort(([ka, ia], [kb, ib]) => (ka < kb ? -1 : ka > kb ? 1 : ia - ib));
  const chosenControls =
    census.seed === null
      ? sortPairs(clean).slice(0, controls)
      : sortPairs(new PythonRandom(census.seed).sample(sortPairs(clean), Math.min(controls, clean.length)));
  const selected = seededOrder(
    [
      ...flagged.map(([key, index]) => ({ key, index, status: "flagged" as const })),
      ...chosenControls.map(([key, index]) => ({ key, index, status: "control" as const })),
    ],
    census.seed,
    (item) => `${item.key}#${String(item.index).padStart(3, "0")}`,
  );
  const byKey = new Map<string, RunRecord>();
  for (const arm of ARMS) {
    for (const record of census.runs[arm]?.results ?? []) byKey.set(labelKey(record.question_id, arm), record);
  }
  return selected.map((item, position) => {
    const record = byKey.get(item.key);
    const claims = record?.generation?.claims ?? [];
    return {
      id: `C-${String(position + 1).padStart(3, "0")}`,
      key: item.key,
      index: item.index,
      status: item.status,
      questionId: record?.question_id ?? item.key.split(":")[0]!,
      question: record?.question ?? "(record not in the loaded runs)",
      gold: record ? census.goldStatement(record.question_id) : "",
      claimText: claims[item.index - 1]?.text ?? "(claim not found)",
    };
  });
}

// ---------------------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------------------

export function passProgress(labels: LabelFile): {
  agreement: { done: number; total: number };
  attribution: { done: number; total: number };
  abstention: { done: number; total: number };
  mislead: { done: number; total: number };
} {
  let agreementDone = 0;
  let agreementTotal = 0;
  let attributionDone = 0;
  let attributionTotal = 0;
  let misleadDone = 0;
  let misleadTotal = 0;
  for (const record of Object.values(labels.records)) {
    for (const claim of record.claims) {
      agreementTotal += 1;
      if (claim.agreement !== null) agreementDone += 1;
      if (record.arm === "production" || record.arm === "naive") {
        attributionTotal += 1;
        if (claim.joint_attribution !== null) attributionDone += 1;
      }
      if (claim.mislead !== null) misleadDone += 1;
      if (claimTriggers(claim, record).length) misleadTotal += 1;
    }
  }
  const gate = Object.values(labels.gate_blocked);
  const sampled = Object.entries(labels.abstained_sample).filter(
    ([, value]) => value !== null && typeof value === "object",
  ) as Array<[string, { any_retrieved_passage_answers: boolean | null; question_valid: boolean | null }]>;
  return {
    agreement: { done: agreementDone, total: agreementTotal },
    attribution: { done: attributionDone, total: attributionTotal },
    abstention: {
      done: gate.filter((item) => item.gate_right !== null).length + sampled.filter(([, item]) => item.any_retrieved_passage_answers !== null).length,
      total: gate.length + sampled.length,
    },
    mislead: { done: misleadDone, total: misleadTotal },
  };
}

// ---------------------------------------------------------------------------------------
// Persistence, in this browser
// ---------------------------------------------------------------------------------------

const LABELS_KEY = "sentinel-rag.labelling.labels.v1";

export function saveLabels(labels: LabelFile): void {
  try {
    window.localStorage.setItem(LABELS_KEY, JSON.stringify(labels));
  } catch {
    // Quota or private mode: the download is the record of truth either way.
  }
}

export function loadLabels(): LabelFile | null {
  try {
    const raw = window.localStorage.getItem(LABELS_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || (parsed as LabelFile).schema_version !== 1) return null;
    return parsed as LabelFile;
  } catch {
    return null;
  }
}

export function clearLabels(): void {
  try {
    window.localStorage.removeItem(LABELS_KEY);
  } catch {
    // Nothing to clear.
  }
}
