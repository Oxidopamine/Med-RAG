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

const verificationSummarySchema = z.strictObject({
  rendered_claims: z.number().int().nonnegative(),
  supported_claims: z.number().int().nonnegative(),
  withheld_claims: z.number().int().nonnegative(),
});

const abstentionSchema = z.strictObject({
  reason_code: z.string().trim().min(1),
  message: z.string().trim().min(1),
  missing_evidence_roles: uniqueStrings,
  closest_evidence_ids: uniqueStrings,
});

const activeCorpusReleaseSchema = z.strictObject({
  corpus_release_id: z.string().trim().min(1),
  manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  qdrant_collection: z.string().trim().min(1),
  activated_at: z.string().datetime({ offset: true }),
});

export const questionResultSchema: z.ZodType<QuestionResult> = z
  .strictObject({
    question_id: z.string().trim().min(1),
    question: z.string().trim().min(1),
    status: questionStatusSchema,
    corpus_release: activeCorpusReleaseSchema.nullable(),
    interpreted_context: clinicalContextSchema.nullable(),
    claims: z.array(renderedClaimSchema).max(100),
    evidence_details: z.array(evidenceDetailSchema).max(100),
    conflicts: z.array(z.record(z.string(), z.string())),
    verification_summary: verificationSummarySchema,
    abstention: abstentionSchema.nullable(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .superRefine((result, issue) => {
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
      const claimEvidenceIds = new Set(result.claims.flatMap((claim) => claim.evidence_ids));
      const detailIds = result.evidence_details.map((detail) => detail.evidence_id);
      if (
        detailIds.length !== new Set(detailIds).size ||
        detailIds.length !== claimEvidenceIds.size ||
        detailIds.some((evidenceId) => !claimEvidenceIds.has(evidenceId))
      ) {
        issue.addIssue({
          code: "custom",
          message: "Evidence details must exactly cover rendered claim evidence IDs",
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
