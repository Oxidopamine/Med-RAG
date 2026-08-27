import { z } from "zod";

import type {
  ClinicalContext,
  CorpusReadiness,
  ProgressEvent,
  QuestionAccepted,
  QuestionResult,
} from "@/lib/types";

const questionStatusSchema = z.enum([
  "QUEUED",
  "CONTEXT_EXTRACTED",
  "RETRIEVING",
  "RERANKING",
  "SEARCHING_COUNTER_EVIDENCE",
  "CHECKING_EVIDENCE_COMPLETENESS",
  "VERIFYING",
  "ANSWER_READY",
  "ABSTAINED",
  "FAILED",
]);

const uniqueStrings = z
  .array(z.string().trim().min(1))
  .refine((items) => new Set(items).size === items.length, "Values must be unique");

const boundedUniqueStrings = (minimum: number, maximum: number) =>
  z
    .array(z.string().trim().min(1))
    .min(minimum)
    .max(maximum)
    .refine((items) => new Set(items).size === items.length, "Values must be unique");

const measurementSchema = z.strictObject({
  concept: z.string().trim().min(1),
  value: z.number().finite(),
  unit: z.string().trim().min(1),
  provenance: z.string().trim().min(1),
});

export const clinicalContextSchema: z.ZodType<ClinicalContext> = z
  .strictObject({
    age: z.number().int().min(0).max(130).nullable(),
    sex: z.string().nullable(),
    conditions: uniqueStrings,
    known_absent_conditions: uniqueStrings,
    measurements: z.array(measurementSchema),
    special_populations: uniqueStrings,
    known_absent_special_populations: uniqueStrings,
    care_setting: z.string().nullable(),
    jurisdiction: z.string().nullable(),
    question_type: z.string().nullable(),
    topic: z.string().nullable(),
    // Which fields the extractor derived rather than read. Defaulted for payloads that
    // predate it: an unmarked field reads as stated, which is the safe direction only
    // because the panel's job is to draw the eye to guesses, not to certify the rest.
    inferred_fields: uniqueStrings.default([]),
  })
  .superRefine((context, issue) => {
    const presentConditions = new Set(context.conditions);
    for (const condition of context.known_absent_conditions) {
      if (presentConditions.has(condition)) {
        issue.addIssue({
          code: "custom",
          message: `${condition} cannot be both present and absent`,
          path: ["known_absent_conditions"],
        });
      }
    }

    const presentPopulations = new Set(context.special_populations);
    for (const population of context.known_absent_special_populations) {
      if (presentPopulations.has(population)) {
        issue.addIssue({
          code: "custom",
          message: `${population} cannot be both present and absent`,
          path: ["known_absent_special_populations"],
        });
      }
    }

    const concepts = context.measurements.map((measurement) => measurement.concept);
    if (new Set(concepts).size !== concepts.length) {
      issue.addIssue({
        code: "custom",
        message: "Measurement concepts must be unique",
        path: ["measurements"],
      });
    }
  });

export const questionAcceptedSchema: z.ZodType<QuestionAccepted> = z.strictObject({
  question_id: z.string().trim().min(1),
  status: questionStatusSchema,
});

export const progressEventSchema: z.ZodType<ProgressEvent> = z.strictObject({
  question_id: z.string().trim().min(1),
  sequence: z.number().int().nonnegative(),
  status: questionStatusSchema,
  occurred_at: z.string().datetime({ offset: true }),
});

export const corpusReadinessSchema: z.ZodType<CorpusReadiness> = z.strictObject({
  status: z.string().trim().min(1),
  corpus_registry_available: z.boolean(),
  approved_corpus_available: z.boolean(),
  corpus_release_id: z.string().trim().min(1).nullable(),
  // Null when nothing is being served. Distinguishes "answers will be withheld" from
  // "answers will come from a release that has not been clinically accepted" - one
  // message for both states is wrong about one of them.
  serving_mode: z.enum(["ACTIVATED", "RESEARCH_UNACTIVATED"]).nullable().default(null),
  clinical_use_allowed: z.boolean(),
});

const renderedClaimSchema = z.strictObject({
  claim_id: z.string().trim().min(1),
  text: z.string().trim().min(1),
  evidence_ids: boundedUniqueStrings(1, 100),
  verification_status: z.string().trim().min(1),
});

const evidenceLocatorSchema = z
  .strictObject({
    kind: z.string().trim().min(1),
    source_uri: z.string().trim().min(1),
    pdf_page: z.number().int().min(1).nullable(),
    printed_page: z.string().nullable(),
    bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
    exact_highlight_available: z.boolean(),
    // Optional, not because the projection may omit them, but because only a
    // TABLE_CELL anchor has them to send. The object is strict, so these have to be
    // modelled or a cell address would fail to parse and take the result down with it.
    table_id: z.string().trim().min(1).nullable().optional(),
    row_index: z.number().int().nonnegative().nullable().optional(),
    column_index: z.number().int().nonnegative().nullable().optional(),
  })
  .superRefine((locator, issue) => {
    if (locator.bbox) {
      const [left, top, right, bottom] = locator.bbox;
      if (right <= left || bottom <= top) {
        issue.addIssue({
          code: "custom",
          message: "A locator bounding box must have positive dimensions",
          path: ["bbox"],
        });
      }
    }
    if (locator.exact_highlight_available && locator.bbox === null) {
      issue.addIssue({
        code: "custom",
        message: "An exact highlight requires a bounding box",
        path: ["exact_highlight_available"],
      });
    }
    // A cell is addressed by all three coordinates or by none. A partial address would
    // render as a cell reference with a fabricated row or column, which is worse than
    // reporting the address as absent.
    const cellFields = [locator.table_id, locator.row_index, locator.column_index];
    const supplied = cellFields.filter((field) => field !== undefined && field !== null);
    if (supplied.length > 0 && supplied.length < cellFields.length) {
      issue.addIssue({
        code: "custom",
        message: "A table-cell address requires a table, a row, and a column",
        path: ["table_id"],
      });
    }
  });

const evidenceDetailSchema = z
  .strictObject({
    evidence_id: z.string().trim().min(1),
    exact_text: z.string().trim().min(1).nullable(),
    evidence_type: z.string().trim().min(1).nullable(),
    evidence_roles: boundedUniqueStrings(1, 20),
    section_path: z.array(z.string().trim().min(1)),
    source_id: z.string().trim().min(1),
    source_version_id: z.string().trim().min(1),
    source_title: z.string().trim().min(1),
    source_version_label: z.string().trim().min(1),
    publisher_name: z.string().trim().min(1),
    source_url: z.string().trim().min(1),
    source_class: z.string().trim().min(1),
    jurisdiction: z.string().trim().min(1),
    language: z.string().trim().min(2),
    lifecycle_status: z.string().trim().min(1),
    effective_from: z.string().date().nullable(),
    effective_to: z.string().date().nullable(),
    approval_status: z.literal("APPROVED"),
    render_allowed: z.boolean(),
    locators: z.array(evidenceLocatorSchema).min(1).max(100),
  })
  .superRefine((detail, issue) => {
    if (!detail.render_allowed && detail.exact_text !== null) {
      issue.addIssue({
        code: "custom",
        message: "Restricted evidence cannot expose exact text",
        path: ["exact_text"],
      });
    }
    if (
      !detail.render_allowed &&
      detail.locators.some((locator) => locator.exact_highlight_available)
    ) {
      issue.addIssue({
        code: "custom",
        message: "Restricted evidence cannot expose exact highlights",
        path: ["locators"],
      });
    }
  });

const retrievalCandidateSchema = z.strictObject({
  evidence_id: z.string().trim().min(1),
  retrieval_rank: z.number().int().min(1),
});

const verificationSummarySchema = z.strictObject({
  rendered_claims: z.number().int().nonnegative(),
  supported_claims: z.number().int().nonnegative(),
  withheld_claims: z.number().int().nonnegative(),
});

const abstentionSchema = z
  .strictObject({
    reason_code: z.string().trim().min(1),
    message: z.string().trim().min(1),
    missing_evidence_roles: uniqueStrings,
    closest_evidence_ids: uniqueStrings,
    // Optional so a payload from an API that predates near-miss detail still parses; the
    // identifiers alone remain a usable answer to "what came closest".
    closest_evidence: z.array(evidenceDetailSchema).max(20).default([]),
  })
  .superRefine((abstention, issue) => {
    // Detail may be absent - resolution is best effort - but detail for a passage the
    // abstention never named would be a passage arriving through the back door, on the
    // one result type that is defined by having rendered nothing.
    const named = new Set(abstention.closest_evidence_ids);
    for (const detail of abstention.closest_evidence) {
      if (!named.has(detail.evidence_id)) {
        issue.addIssue({
          code: "custom",
          message: `${detail.evidence_id} is not named in closest_evidence_ids`,
          path: ["closest_evidence"],
        });
      }
    }
    const resolved = abstention.closest_evidence.map((detail) => detail.evidence_id);
    if (new Set(resolved).size !== resolved.length) {
      issue.addIssue({
        code: "custom",
        message: "closest evidence detail must not repeat an evidence ID",
        path: ["closest_evidence"],
      });
    }
  });

/*
 * A typed disagreement.
 *
 * `evidence_ids` used to arrive comma-joined inside an untyped record, which every client
 * re-parsed and none could rely on. Typed, the passages a conflict is *about* are
 * addressable - which is what lets the panel show them side by side instead of describing
 * them. Everything else stays optional so a record with an unrecognised type still renders
 * as unclassified rather than being rejected.
 */
const guidelineConflictSchema = z.strictObject({
  conflict_type: z.string().nullish(),
  summary: z.string().nullish(),
  evidence_ids: uniqueStrings.default([]),
  organization: z.string().nullish(),
  recommendation: z.string().nullish(),
  rationale: z.string().nullish(),
});

const activeCorpusReleaseSchema = z
  .strictObject({
    corpus_release_id: z.string().trim().min(1),
    manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
    qdrant_collection: z.string().trim().min(1),
    activated_at: z.string().datetime({ offset: true }).nullable(),
    // Defaulted so a payload from an older API still parses; the default is the
    // governed value, which is the safe direction only because the API cannot serve a
    // research release without saying so explicitly.
    serving_mode: z.enum(["ACTIVATED", "RESEARCH_UNACTIVATED"]).default("ACTIVATED"),
  })
  // The cross-field invariant the API enforces, restated rather than trusted. A release
  // claiming activation without an instant, or carrying one while calling itself
  // unactivated, is a payload this interface must not render: both would let the
  // provenance strip assert a governance state that does not exist.
  .refine(
    (release) =>
      release.serving_mode === "ACTIVATED"
        ? release.activated_at !== null
        : release.activated_at === null,
    { message: "activated_at must be present exactly when serving_mode is ACTIVATED" },
  );

export const questionResultSchema: z.ZodType<QuestionResult> = z
  .strictObject({
    question_id: z.string().trim().min(1),
    question: z.string().trim().min(1),
    status: questionStatusSchema,
    corpus_release: activeCorpusReleaseSchema.nullable(),
    interpreted_context: clinicalContextSchema.nullable(),
    claims: z.array(renderedClaimSchema).max(100),
    evidence_details: z.array(evidenceDetailSchema).max(100),
    retrieval_candidates: z.array(retrievalCandidateSchema).max(100),
    conflicts: z.array(guidelineConflictSchema).max(50),
    verification_summary: verificationSummarySchema,
    abstention: abstentionSchema.nullable(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .superRefine((result, issue) => {
    const claimEvidenceIds = new Set(result.claims.flatMap((claim) => claim.evidence_ids));
    const candidateIds = result.retrieval_candidates.map((candidate) => candidate.evidence_id);
    const candidateRanks = result.retrieval_candidates.map(
      (candidate) => candidate.retrieval_rank,
    );

    if (candidateIds.length !== new Set(candidateIds).size) {
      issue.addIssue({
        code: "custom",
        message: "Retrieval candidate evidence IDs must be unique",
        path: ["retrieval_candidates"],
      });
    }
    if (candidateIds.some((evidenceId) => claimEvidenceIds.has(evidenceId))) {
      issue.addIssue({
        code: "custom",
        message: "A cited evidence ID cannot also be an uncited retrieval candidate",
        path: ["retrieval_candidates"],
      });
    }
    if (candidateRanks.some((rank, index) => index > 0 && rank <= candidateRanks[index - 1]!)) {
      issue.addIssue({
        code: "custom",
        message: "Retrieval candidates must be listed in ascending, unique rank order",
        path: ["retrieval_candidates"],
      });
    }
    if (result.retrieval_candidates.length > 0 && result.status !== "ANSWER_READY") {
      issue.addIssue({
        code: "custom",
        message: "Only an answer-ready result can expose retrieval candidates",
        path: ["retrieval_candidates"],
      });
    }

    if (result.status === "ANSWER_READY" && result.abstention !== null) {
      issue.addIssue({
        code: "custom",
        message: "An answer-ready result cannot include an abstention",
        path: ["abstention"],
      });
    }
    if (result.status === "ANSWER_READY") {
      if (result.corpus_release === null) {
        issue.addIssue({
          code: "custom",
          message: "An answer-ready result requires an active corpus release",
          path: ["corpus_release"],
        });
      }
      if (result.claims.length === 0) {
        issue.addIssue({
          code: "custom",
          message: "An answer-ready result requires at least one rendered claim",
          path: ["claims"],
        });
      }
      const referencedIds = new Set([...claimEvidenceIds, ...candidateIds]);
      const detailIds = result.evidence_details.map((detail) => detail.evidence_id);
      if (
        detailIds.length !== new Set(detailIds).size ||
        detailIds.length !== referencedIds.size ||
        detailIds.some((evidenceId) => !referencedIds.has(evidenceId))
      ) {
        issue.addIssue({
          code: "custom",
          message: "Evidence details must exactly cover cited and candidate evidence IDs",
          path: ["evidence_details"],
        });
      }
    }
    if (["ABSTAINED", "FAILED"].includes(result.status)) {
      if (result.claims.length > 0) {
        issue.addIssue({
          code: "custom",
          message: "Abstained and failed results cannot render claims",
          path: ["claims"],
        });
      }
      if (result.abstention === null) {
        issue.addIssue({
          code: "custom",
          message: "Abstained and failed results require abstention details",
          path: ["abstention"],
        });
      }
      if (result.evidence_details.length > 0) {
        issue.addIssue({
          code: "custom",
          message: "Abstained and failed results cannot expose evidence details",
          path: ["evidence_details"],
        });
      }
    }
  });

export function parseContract<T>(schema: z.ZodType<T>, payload: unknown, label: string): T {
  const parsed = schema.safeParse(payload);
  if (parsed.success) return parsed.data;

  const firstIssue = parsed.error.issues[0];
  const field = firstIssue?.path.length ? ` at ${firstIssue.path.join(".")}` : "";
  const detail = firstIssue?.message ?? "unknown validation error";
  throw new Error(`The API returned an invalid ${label}${field}: ${detail}.`);
}
