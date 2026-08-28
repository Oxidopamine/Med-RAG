import type {
  AbstentionDetail,
  GuidelineConflict,
  EvidenceDetail,
  QuestionResult,
  QuestionStatus,
} from "@/lib/types";

/**
 * Frozen payloads for the grounded-answer lane.
 *
 * The answer lane is not live yet, so the presentation surface is developed against
 * these instead. They are modelled on the WHO guideline assets in the active trust root
 * (`data/trust-roots/who-guidelines-ncd.json`), where every asset today carries
 * `render_allowed: false` pending a deployment-specific licence review. The restricted
 * fixture is therefore the shape the interface has to be correct for right now, and the
 * licensed fixture is the shape it has to stay correct for if that review lands the
 * other way.
 *
 * Each helper returns a fresh object so a test can amend one without disturbing another.
 */

/** Exactly what the PDF extractor joins a page's text blocks with. */
const BLOCK_JOIN = "\n\n";

const QUESTION =
  "For an adult with newly diagnosed hypertension, what do current guidelines recommend about starting pharmacological treatment?";

const CORPUS_RELEASE = {
  corpus_release_id: "who-ncd-2026-08",
  manifest_sha256: "b".repeat(64),
  qdrant_collection: "who_ncd_2026_08",
  activated_at: "2026-08-24T08:00:00+00:00",
  serving_mode: "ACTIVATED" as const,
};

const INTERPRETED_CONTEXT = {
  age: 58,
  sex: null,
  conditions: ["HYPERTENSION"],
  known_absent_conditions: ["CHRONIC_KIDNEY_DISEASE"],
  measurements: [
    {
      concept: "SYSTOLIC_BLOOD_PRESSURE",
      value: 152,
      unit: "mmHg",
      provenance: "USER_TEXT_EXPLICIT",
    },
  ],
  special_populations: [],
  known_absent_special_populations: ["PREGNANCY"],
  care_setting: "PRIMARY_CARE",
  jurisdiction: null,
  question_type: "treatment_guideline",
  topic: "hypertension",
  // Care setting was not stated in the question; the extractor concluded it.
  inferred_fields: ["care_setting", "topic"],
};

/**
 * A WHO evidence record as the release materialises it today.
 *
 * `render_allowed: false` forces `exact_text: null` and forbids an exact highlight, but
 * a bounding box may still be recorded: the region is known, and only the licence stops
 * it being drawn.
 */
export function restrictedWhoEvidence(
  overrides: Partial<EvidenceDetail> = {},
): EvidenceDetail {
  return {
    evidence_id: "EV_WHO_HTN_001",
    exact_text: null,
    evidence_type: "GUIDELINE_RECOMMENDATION",
    evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
    section_path: ["Pharmacological treatment", "Treatment initiation"],
    source_id: "SRC_WHO_HTN",
    source_version_id: "SV_WHO_HTN_2021",
    source_title: "Guideline for the pharmacological treatment of hypertension in adults",
    source_version_label: "2021",
    publisher_name: "World Health Organization",
    source_url: "https://iris.who.int/handle/10665/344424",
    source_class: "GUIDELINE",
    jurisdiction: "WORLD",
    language: "en",
    lifecycle_status: "CURRENT",
    effective_from: "2021-08-24",
    effective_to: null,
    approval_status: "APPROVED",
    render_allowed: false,
    locators: [
      {
        kind: "PDF_PAGE",
        source_uri: "source://SV_WHO_HTN_2021/page/19",
        pdf_page: 19,
        printed_page: "11",
        bbox: [0.12, 0.31, 0.88, 0.44],
        exact_highlight_available: false,
      },
    ],
    ...overrides,
  };
}

/** The same record if the licence review permits rendering. */
export function licensedWhoEvidence(overrides: Partial<EvidenceDetail> = {}): EvidenceDetail {
  return restrictedWhoEvidence({
    exact_text:
      "Pharmacological treatment is recommended for adults with confirmed hypertension and systolic blood pressure of 140 mmHg or greater.",
    render_allowed: true,
    locators: [
      // Deliberately listed with the coarse locator first: the presentation layer, not
      // the payload order, decides which anchor is the most precise one to show.
      {
        kind: "SECTION",
        source_uri: "source://SV_WHO_HTN_2021/section/treatment-initiation",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
      },
      {
        kind: "PDF_PAGE",
        source_uri: "source://SV_WHO_HTN_2021/page/19",
        pdf_page: 19,
        printed_page: "11",
        bbox: [0.12, 0.31, 0.88, 0.44],
        exact_highlight_available: true,
      },
    ],
    ...overrides,
  });
}

/**
 * A table-cell anchor as the corpus records one and the release now serves it.
 *
 * The XLSX extractor emits `TABLE_CELL` anchors carrying a worksheet, a zero-based row,
 * and a zero-based column, and the serving projection forwards all three.
 */
export function tableCellEvidence(overrides: Partial<EvidenceDetail> = {}): EvidenceDetail {
  return restrictedWhoEvidence({
    evidence_id: "EV_WHO_HTN_020",
    evidence_type: "DOSING_TABLE",
    evidence_roles: ["DOSE_OR_THRESHOLD"],
    section_path: ["Annex 2", "Dosing table"],
    locators: [
      {
        kind: "TABLE_CELL",
        source_uri: "source://SV_WHO_HTN_2021/annex/2",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
        table_id: "Annex2Dosing",
        row_index: 3,
        column_index: 2,
      },
    ],
    ...overrides,
  });
}

/**
 * A workbook row as the DAK annexes actually materialise.
 *
 * This is the shape most of the release is in, and the one the interface was worst at:
 * `content_exact` is the extractor's serialisation of a whole row - one `E146=value` line
 * per populated cell - with a `TABLE_CELL` anchor for each. Printed verbatim it shows the
 * reader the pipeline; parsed back it is the publisher's row.
 */
export function spreadsheetRowEvidence(
  overrides: Partial<EvidenceDetail> = {},
): EvidenceDetail {
  return licensedWhoEvidence({
    evidence_id: "EV_WHO_HIV_DAK_146",
    source_id: "WHO_HIV_DAK_2_ANNEX_B",
    exact_text: [
      "A146=HIV.D.DE12",
      "C146=Viral load test result",
      "E146=Detectable (>= 1000 copies/mL)",
      "F146=Repeat viral load after enhanced adherence counselling",
    ].join("\n"),
    locators: [0, 2, 4, 5].map((columnIndex) => ({
      kind: "TABLE_CELL",
      source_uri: "source://SV_WHO_HIV_DAK_2/annex/B",
      pdf_page: null,
      printed_page: null,
      bbox: null,
      exact_highlight_available: false,
      table_id: "HIV.D",
      row_index: 145,
      column_index: columnIndex,
    })),
    ...overrides,
  });
}

/**
 * A PDF page as the extractor materialises one.
 *
 * The unit is the whole page: `content_exact` is its text blocks joined by a blank line,
 * with one anchor per block carrying that block's box. Both halves are needed for the
 * passage and the figure to point at each other, and a record where they disagree in count
 * is the case where they must not.
 */
export function pageBlocksEvidence(
  overrides: Partial<EvidenceDetail> = {},
): EvidenceDetail {
  return licensedWhoEvidence({
    evidence_id: "EV_WHO_HTN_PAGE_19",
    exact_text: [
      "Recommendation 3. Pharmacological treatment is recommended for adults with confirmed hypertension.",
      "Remarks. The threshold applies to repeated office measurements taken on separate days.",
      "Evidence certainty was rated moderate for this recommendation.",
    ].join(BLOCK_JOIN),
    locators: [
      [0.1, 0.12, 0.9, 0.24],
      [0.1, 0.3, 0.9, 0.41],
      [0.1, 0.47, 0.62, 0.55],
    ].map((bbox) => ({
      kind: "PDF",
      source_uri: "source://SV_WHO_HTN_2021/page/19",
      pdf_page: 19,
      printed_page: "11",
      bbox: bbox as [number, number, number, number],
      exact_highlight_available: true,
    })),
    ...overrides,
  });
}

/**
 * A cell anchor with no address, which a correct pipeline no longer produces.
 *
 * The corpus validator requires a table, a row, and a column on every `TABLE_CELL`
 * anchor, and the projection forwards them, so this shape should not arrive. It is kept
 * because the fields are optional on the wire and the viewer's contract is to report
 * what it was given: an unaddressed cell has to read as a cell whose address is missing,
 * never as a document-scope anchor, which would quietly overstate what was verified.
 */
export function unaddressedTableCellEvidence(
  overrides: Partial<EvidenceDetail> = {},
): EvidenceDetail {
  return tableCellEvidence({
    locators: [
      {
        kind: "TABLE_CELL",
        source_uri: "source://SV_WHO_HTN_2021/annex/2",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
      },
    ],
    ...overrides,
  });
}

/**
 * A near-identical passage from the preceding edition.
 *
 * This is the case the anchor display has to survive: same publisher, same section,
 * same printed page, a bounding box a few millimetres away, and text a reader would
 * not tell apart at a glance. Only `source_version_id`, the version label, and the
 * lifecycle separate it from `restrictedWhoEvidence`, so any surface that shows a page
 * and a rectangle without naming the edition is showing an ambiguous location.
 */
export function priorEditionTwinEvidence(
  overrides: Partial<EvidenceDetail> = {},
): EvidenceDetail {
  return restrictedWhoEvidence({
    evidence_id: "EV_WHO_HTN_001_PRIOR",
    source_version_id: "SV_WHO_HTN_2013",
    source_version_label: "2013",
    lifecycle_status: "SUPERSEDED",
    effective_from: "2013-05-01",
    effective_to: "2021-08-23",
    locators: [
      {
        kind: "PDF_PAGE",
        source_uri: "source://SV_WHO_HTN_2013/page/19",
        pdf_page: 19,
        printed_page: "11",
        bbox: [0.12, 0.32, 0.88, 0.45],
        exact_highlight_available: false,
      },
    ],
    ...overrides,
  });
}

function whoThresholdEvidence(): EvidenceDetail {
  return restrictedWhoEvidence({
    evidence_id: "EV_WHO_HTN_002",
    evidence_type: "GUIDELINE_EXCEPTION",
    evidence_roles: ["EXCEPTION_OR_CONTRAINDICATION", "POPULATION_APPLICABILITY"],
    section_path: ["Pharmacological treatment", "Comorbid conditions"],
    locators: [
      {
        kind: "SECTION",
        source_uri: "source://SV_WHO_HTN_2021/section/comorbid-conditions",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
      },
    ],
  });
}

function supersededEvidence(): EvidenceDetail {
  return restrictedWhoEvidence({
    evidence_id: "EV_WHO_HTN_003",
    source_version_id: "SV_WHO_HTN_2013",
    source_version_label: "2013",
    section_path: ["Treatment thresholds"],
    lifecycle_status: "SUPERSEDED",
    effective_from: "2013-05-01",
    effective_to: "2021-08-23",
    locators: [
      {
        kind: "PDF_PAGE",
        source_uri: "source://SV_WHO_HTN_2013/page/8",
        pdf_page: 8,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
      },
    ],
  });
}

function uncitedCandidateEvidence(): EvidenceDetail {
  return restrictedWhoEvidence({
    evidence_id: "EV_WHO_HTN_014",
    evidence_roles: ["BACKGROUND"],
    section_path: ["Implementation considerations"],
    locators: [
      {
        kind: "SECTION",
        source_uri: "source://SV_WHO_HTN_2021/section/implementation",
        pdf_page: null,
        printed_page: null,
        bbox: null,
        exact_highlight_available: false,
      },
    ],
  });
}

interface AnswerFixtureOptions {
  /** Swap the cited records for licence-permitting ones. */
  licensed?: boolean;
  /** Partial by design: an unclassified record is a shape the interface must survive. */
  conflicts?: Array<Partial<GuidelineConflict>>;
}

/**
 * An answer-ready result carrying two claims over three cited passages, one uncited
 * ranked candidate, and one typed conflict.
 */
export function answerReadyResult(options: AnswerFixtureOptions = {}): QuestionResult {
  const primary = options.licensed ? licensedWhoEvidence() : restrictedWhoEvidence();
  const conflicts = options.conflicts ?? [
    {
      conflict_type: "OUTDATED_INFORMATION",
      summary:
        "The 2013 edition states a higher treatment threshold than the 2021 edition for the same population.",
      evidence_ids: ["EV_WHO_HTN_001", "EV_WHO_HTN_003"],
    },
  ];

  return {
    question_id: "question-who-ready",
    question: QUESTION,
    status: "ANSWER_READY",
    corpus_release: CORPUS_RELEASE,
    interpreted_context: INTERPRETED_CONTEXT,
    claims: [
      {
        claim_id: "CL_001",
        text: "Pharmacological treatment is recommended for adults with confirmed hypertension at or above the stated systolic threshold.",
        evidence_ids: ["EV_WHO_HTN_001", "EV_WHO_HTN_003"],
        verification_status: "SUPPORTED",
      },
      {
        claim_id: "CL_002",
        text: "Comorbid conditions change which agent is selected and are addressed separately from the decision to treat.",
        evidence_ids: ["EV_WHO_HTN_002"],
        verification_status: "SUPPORTED",
      },
    ],
    evidence_details: [
      primary,
      whoThresholdEvidence(),
      supersededEvidence(),
      uncitedCandidateEvidence(),
    ],
    retrieval_candidates: [{ evidence_id: "EV_WHO_HTN_014", retrieval_rank: 6 }],
    conflicts,
    verification_summary: {
      rendered_claims: 3,
      supported_claims: 2,
      withheld_claims: 1,
    },
    abstention: null,
    created_at: "2026-08-25T10:00:00+00:00",
    updated_at: "2026-08-25T10:00:08+00:00",
  };
}

/** A terminal result that rendered nothing, for one reason code. */
export function abstainedResult(
  abstention: Partial<AbstentionDetail> & Pick<AbstentionDetail, "reason_code">,
  status: Extract<QuestionStatus, "ABSTAINED" | "FAILED"> = "ABSTAINED",
): QuestionResult {
  return {
    question_id: "question-who-abstained",
    question: QUESTION,
    status,
    corpus_release: null,
    interpreted_context: INTERPRETED_CONTEXT,
    claims: [],
    evidence_details: [],
    retrieval_candidates: [],
    conflicts: [],
    verification_summary: {
      rendered_claims: 0,
      supported_claims: 0,
      withheld_claims: 0,
    },
    abstention: {
      message: "No verified answer was produced for this question.",
      missing_evidence_roles: [],
      closest_evidence_ids: [],
      closest_evidence: [],
      ...abstention,
    },
    created_at: "2026-08-25T10:00:00+00:00",
    updated_at: "2026-08-25T10:00:02+00:00",
  };
}
