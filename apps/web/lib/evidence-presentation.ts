import type {
  AbstentionDetail,
  EvidenceDetail,
  EvidenceLocator,
  QuestionResult,
  QuestionStatus,
  RenderedClaim,
} from "./types";

/**
 * Presentation rules for the grounded-answer lane.
 *
 * The API hands the interface loosely typed conflict records, free-form abstention
 * reason codes, and evidence whose licence may forbid showing its text. None of those
 * shapes are safe to render directly: an unrecognised conflict type rendered as a bare
 * key/value table reads as though the system had classified it, an unmapped reason code
 * leaves the reader with no next step, and a licence flag that only the quotation
 * branch consults leaks a passage the moment another surface is added.
 *
 * Every rule below is a pure function over a validated payload, so the answer lane can
 * be exercised against a frozen fixture release before it is live.
 */

/** The conflict taxonomy the answer layer is allowed to report. */
export const CONFLICT_TYPES = [
  "NO_CONFLICT",
  "COMPLEMENTARY",
  "CONFLICTING_RECOMMENDATIONS",
  "OUTDATED_INFORMATION",
  "CONTRADICTORY_SOURCE",
] as const;

export type ConflictType = (typeof CONFLICT_TYPES)[number];

export type ConflictTone = "cleared" | "informational" | "caution" | "critical";

export interface ConflictDescriptor {
  /** What kind of disagreement this is, in the reader's language. */
  label: string;
  /** What the label means for the passages involved. */
  meaning: string;
  /** What the reader is expected to do about it. */
  guidance: string;
  requiresReview: boolean;
  tone: ConflictTone;
}

const CONFLICT_DESCRIPTORS: Record<ConflictType, ConflictDescriptor> = {
  NO_CONFLICT: {
    label: "No conflict",
    meaning: "The cited passages agree on this point.",
    guidance: "No reconciliation is needed for the passages named here.",
    requiresReview: false,
    tone: "cleared",
  },
  COMPLEMENTARY: {
    label: "Complementary guidance",
    meaning:
      "The passages cover different parts of the same decision rather than disagreeing.",
    guidance: "Read them together. Neither one replaces the other.",
    requiresReview: true,
    tone: "informational",
  },
  CONFLICTING_RECOMMENDATIONS: {
    label: "Conflicting recommendations",
    meaning: "The passages recommend different actions for the same situation.",
    guidance:
      "Both recommendations are shown as published. Choosing between them is a clinical judgement, not a system output.",
    requiresReview: true,
    tone: "critical",
  },
  OUTDATED_INFORMATION: {
    label: "Superseded information",
    meaning: "One passage states guidance that a later passage revises.",
    guidance:
      "Check the effective dates and version labels before relying on either passage.",
    requiresReview: true,
    tone: "caution",
  },
  CONTRADICTORY_SOURCE: {
    label: "Contradictory sources",
    meaning: "Two sources state things that cannot both hold.",
    guidance: "Open both publisher sources before acting on either.",
    requiresReview: true,
    tone: "critical",
  },
};

const UNCLASSIFIED_CONFLICT: ConflictDescriptor = {
  label: "Unclassified disagreement",
  meaning:
    "The answer lane reported a disagreement without a type this interface recognises.",
  guidance:
    "Treat it as unreviewed. The record is shown as returned, with nothing inferred about its kind.",
  requiresReview: true,
  tone: "caution",
};

export function conflictDescriptor(type: ConflictType | null): ConflictDescriptor {
  return type === null ? UNCLASSIFIED_CONFLICT : CONFLICT_DESCRIPTORS[type];
}

export interface PresentedConflict {
  key: string;
  /** The taxonomy member, or `null` when the record did not name a recognised one. */
  type: ConflictType | null;
  /** An unrecognised value, kept verbatim so it is never silently dropped. */
  unrecognizedType: string | null;
  descriptor: ConflictDescriptor;
  summary: string | null;
  evidenceIds: string[];
  citations: Citation[];
  /** Evidence IDs the conflict named that the result carries no cited detail for. */
  unresolvedEvidenceIds: string[];
  /** Any further fields the record carried, kept rather than discarded. */
  extraFields: Array<[string, string]>;
  requiresReview: boolean;
}

const TYPED_CONFLICT_KEYS = new Set(["conflict_type", "summary", "evidence_ids"]);

function isConflictType(value: string): value is ConflictType {
  return (CONFLICT_TYPES as readonly string[]).includes(value);
}

/**
 * Split the evidence-ID field of a conflict record.
 *
 * The composer joins the IDs into one comma-separated string because the transport
 * types a conflict as `dict[str, str]`. Splitting here lets the conflict display and
 * the claim list work from the same identifiers, and so share one citation numbering.
 */
function parseEvidenceIds(value: string | undefined): string[] {
  if (!value) return [];
  const seen = new Set<string>();
  for (const part of value.split(",")) {
    const trimmed = part.trim();
    if (trimmed) seen.add(trimmed);
  }
  return [...seen];
}

export function presentConflicts(
  conflicts: ReadonlyArray<Record<string, string>>,
  citations: CitationIndex = emptyCitationIndex(),
): PresentedConflict[] {
  return conflicts.map((conflict, index) => {
    const rawType = conflict.conflict_type?.trim() ?? "";
    const type = isConflictType(rawType) ? rawType : null;
    const descriptor = conflictDescriptor(type);
    const evidenceIds = parseEvidenceIds(conflict.evidence_ids);
    const summary = conflict.summary?.trim();

    return {
      key: `conflict-${index}`,
      type,
      unrecognizedType: type === null && rawType ? rawType : null,
      descriptor,
      summary: summary ? summary : null,
      evidenceIds,
      citations: evidenceIds.flatMap((evidenceId) => {
        const citation = citations.byEvidenceId.get(evidenceId);
        return citation ? [citation] : [];
      }),
      unresolvedEvidenceIds: evidenceIds.filter(
        (evidenceId) => !citations.byEvidenceId.has(evidenceId),
      ),
      extraFields: Object.entries(conflict).filter(
        ([field, value]) => !TYPED_CONFLICT_KEYS.has(field) && value.trim().length > 0,
      ),
      requiresReview: descriptor.requiresReview,
    };
  });
}

export function reviewableConflicts(conflicts: PresentedConflict[]): PresentedConflict[] {
  return conflicts.filter((conflict) => conflict.requiresReview);
}

/** Reason codes the answer lane and the question pipeline can abstain with. */
export const ABSTENTION_REASON_CODES = [
  "NO_EVIDENCE_RETRIEVED",
  "NO_ACTIVE_RELEASE",
  "MODEL_DECLARED_INSUFFICIENT",
  "NO_CLAIM_SURVIVED_GROUNDING",
  "GENERATION_UNAVAILABLE",
  "NO_APPROVED_CORPUS",
  "RETRIEVAL_PIPELINE_NOT_CONFIGURED",
  "PIPELINE_FAILURE",
] as const;

export type AbstentionReasonCode = (typeof ABSTENTION_REASON_CODES)[number];

export type AbstentionCause = "coverage" | "grounding" | "service";

export interface AbstentionPresentation {
  /** The recognised code, or `null` when the service sent one this build does not know. */
  code: AbstentionReasonCode | null;
  /** The code exactly as the service sent it, always available for a support report. */
  rawCode: string | null;
  title: string;
  /**
   * The service explanation when it sent one, and this build's wording otherwise. The
   * service message is authoritative: it knows which release and evidence set ran.
   */
  message: string;
  /** What the reader can do next. */
  nextStep: string;
  /** Whether the same question could plausibly succeed on a retry. */
  retryable: boolean;
  cause: AbstentionCause;
  missingEvidenceRoles: string[];
  closestEvidenceIds: string[];
}

interface ReasonGuidance {
  title: string;
  message: string;
  nextStep: string;
  retryable: boolean;
  cause: AbstentionCause;
}

const REASON_GUIDANCE: Record<AbstentionReasonCode, ReasonGuidance> = {
  NO_EVIDENCE_RETRIEVED: {
    title: "No approved guideline evidence matched this question",
    message: "The active release was searched and returned no passage for this question.",
    nextStep:
      "Widen the source scope, or restate the population and decision in guideline terms.",
    retryable: true,
    cause: "coverage",
  },
  NO_ACTIVE_RELEASE: {
    title: "No corpus release is active",
    message: "No approved release was pinned for this run, so no passage could be cited.",
    nextStep: "Activate an approved corpus release, then run the question again.",
    retryable: false,
    cause: "service",
  },
  MODEL_DECLARED_INSUFFICIENT: {
    title: "The retrieved passages do not answer this question",
    message:
      "Evidence was retrieved, but it does not cover the decision that was asked about.",
    nextStep:
      "Narrow the question to a decision the guideline addresses, or widen the source scope.",
    retryable: true,
    cause: "coverage",
  },
  NO_CLAIM_SURVIVED_GROUNDING: {
    title: "No proposed claim was fully supported",
    message:
      "Every proposed claim failed the check that ties it to the retrieved passages, so none was rendered.",
    nextStep: "Inspect the closest evidence below, then ask about the point it covers.",
    retryable: true,
    cause: "grounding",
  },
  GENERATION_UNAVAILABLE: {
    title: "The answer service could not produce a verifiable answer",
    message: "The generation lane returned no checkable answer, so nothing was rendered.",
    nextStep: "Retry once. If it continues, share the run ID with support.",
    retryable: true,
    cause: "service",
  },
  NO_APPROVED_CORPUS: {
    title: "No approved guideline corpus is active",
    message: "The evidence gate had no approved source collection to search.",
    nextStep: "Activate an approved corpus release, then retry this question.",
    retryable: false,
    cause: "service",
  },
  RETRIEVAL_PIPELINE_NOT_CONFIGURED: {
    title: "Guideline retrieval is not available",
    message: "An approved corpus exists, but the retrieval pipeline is not ready to serve it.",
    nextStep: "Check retrieval configuration, or try again after service restoration.",
    retryable: false,
    cause: "service",
  },
  PIPELINE_FAILURE: {
    title: "The review could not be completed",
    message: "The pipeline failed closed and did not render a clinical claim.",
    nextStep: "Retry once. If the problem continues, share the run ID with support.",
    retryable: true,
    cause: "service",
  },
};

const UNKNOWN_REASON: ReasonGuidance = {
  title: "The evidence gate withheld the answer",
  message: "The available evidence was not sufficient to support a clinical claim.",
  nextStep: "Refine the population or decision in the question, then try again.",
  retryable: true,
  cause: "coverage",
};

const FAILED_WITHOUT_REASON: ReasonGuidance = {
  title: "The review failed safely",
  message: "The run ended without rendering a clinical claim.",
  nextStep: "Retry once. If the problem continues, share the run ID with support.",
  retryable: true,
  cause: "service",
};

function isReasonCode(value: string): value is AbstentionReasonCode {
  return (ABSTENTION_REASON_CODES as readonly string[]).includes(value);
}

/**
 * Turn an abstention into something a reader can act on.
 *
 * An unmapped reason code still produces a complete state: the service message and the
 * raw code are surfaced rather than replaced by a generic apology, because the code is
 * what a support conversation needs and this build cannot know every future one.
 */
export function presentAbstention(
  abstention: AbstentionDetail | null,
  status: QuestionStatus,
): AbstentionPresentation {
  const rawCode = abstention?.reason_code.trim() ?? "";
  const code = isReasonCode(rawCode) ? rawCode : null;
  const fallback = status === "FAILED" ? FAILED_WITHOUT_REASON : UNKNOWN_REASON;
  const guidance = code === null ? fallback : REASON_GUIDANCE[code];
  const serviceMessage = abstention?.message.trim();

  return {
    code,
    rawCode: rawCode ? rawCode : null,
    title: guidance.title,
    message: serviceMessage ? serviceMessage : guidance.message,
    nextStep: guidance.nextStep,
    retryable: guidance.retryable,
    cause: guidance.cause,
    missingEvidenceRoles: abstention?.missing_evidence_roles ?? [],
    closestEvidenceIds: abstention?.closest_evidence_ids ?? [],
  };
}

/**
 * Why a passage is or is not shown in full.
 *
 * `LICENCE_WITHHOLDS_TEXT` and `TEXT_NOT_SUPPLIED` are deliberately separate. Both end
 * in no quotation, but only the first is a licensing statement, and telling a reader
 * that a licence forbids text when the release simply carried none is a claim about a
 * publisher that this interface has no basis to make.
 */
export type RenderRestriction = "LICENSED" | "LICENCE_WITHHOLDS_TEXT" | "TEXT_NOT_SUPPLIED";

export interface RenderPolicy {
  /** Whether the exact passage text may be displayed. */
  canQuote: boolean;
  /** Whether an exact region of the source page may be highlighted. */
  canHighlightExactly: boolean;
  /** True when the licence, not the payload, is why nothing is quoted. */
  licenceRestricted: boolean;
  restriction: RenderRestriction;
  headline: string;
  explanation: string;
}

/**
 * Decide what may be shown for one evidence record.
 *
 * `render_allowed` is consulted first and on its own. Every WHO asset in the current
 * trust root carries `render_allowed: false` pending a licence review, so this is the
 * live path rather than a defensive branch - and it must not be inferred from
 * `exact_text` being absent, which is a different fact with a different remedy.
 */
export function renderPolicy(detail: EvidenceDetail): RenderPolicy {
  if (!detail.render_allowed) {
    return {
      canQuote: false,
      canHighlightExactly: false,
      licenceRestricted: true,
      restriction: "LICENCE_WITHHOLDS_TEXT",
      headline: "Licence does not permit showing this passage",
      explanation:
        "Provenance, location, and version were verified. Open the publisher source to read the passage itself.",
    };
  }
  if (detail.exact_text === null) {
    return {
      canQuote: false,
      canHighlightExactly: hasExactRegion(detail),
      licenceRestricted: false,
      restriction: "TEXT_NOT_SUPPLIED",
      headline: "No passage text was supplied",
      explanation:
        "The licence permits display, but this release carried no exact text for this evidence record.",
    };
  }
  return {
    canQuote: true,
    canHighlightExactly: hasExactRegion(detail),
    licenceRestricted: false,
    restriction: "LICENSED",
    headline: "Verified source passage",
    explanation: "The publisher licence permits showing this passage as published.",
  };
}

/** How precisely a locator pins the passage inside its source document. */
export type AnchorPrecision = "EXACT_REGION" | "CELL" | "PAGE" | "DOCUMENT";

/**
 * Which edition an anchor points into.
 *
 * Carried on the anchor itself rather than looked up beside it. Two versions of the
 * same guideline routinely carry near-identical text at similar page numbers, so
 * "page 19, this rectangle" identifies a passage only once the edition is named. An
 * anchor that travels without its version is a coordinate without a document, and any
 * surface that renders one would be free to pair it with the wrong edition.
 */
export interface SourceVersionIdentity {
  sourceId: string;
  /** The opaque key that separates two editions whose labels may read alike. */
  sourceVersionId: string;
  title: string;
  versionLabel: string;
  publisherName: string;
  lifecycleStatus: string;
  effectiveFrom: string | null;
  effectiveTo: string | null;
  /** `World Health Organization 2021` - enough to name the edition in a sentence. */
  label: string;
  /** True when this edition is no longer the current one. */
  superseded: boolean;
}

export function sourceVersionIdentity(detail: EvidenceDetail): SourceVersionIdentity {
  return {
    sourceId: detail.source_id,
    sourceVersionId: detail.source_version_id,
    title: detail.source_title,
    versionLabel: detail.source_version_label,
    publisherName: detail.publisher_name,
    lifecycleStatus: detail.lifecycle_status,
    effectiveFrom: detail.effective_from,
    effectiveTo: detail.effective_to,
    label: `${detail.publisher_name} ${detail.source_version_label}`.trim(),
    superseded: detail.lifecycle_status !== "CURRENT",
  };
}

/**
 * Whether a recorded rectangle can be drawn over a page.
 *
 * `PROPORTIONAL` regions are fractions of the page box and place directly. `UNPLACEABLE`
 * regions are in the source document's own units - the PDF extractor records PyMuPDF
 * block boxes in points - and the serving contract carries no page dimensions to divide
 * by. Such a region is reported numerically and never drawn: inventing a scale would put
 * a provenance marker somewhere the publisher did not put it, which is a worse failure
 * than showing no marker at all.
 */
export type RegionPlacement = "PROPORTIONAL" | "UNPLACEABLE";

export interface AnchorRegion {
  placement: RegionPlacement;
  left: number;
  top: number;
  right: number;
  bottom: number;
  /** Fractions of the page box. Meaningful only when `placement` is `PROPORTIONAL`. */
  width: number;
  height: number;
  /** The coordinates as recorded, for a reader who wants the numbers themselves. */
  readout: string;
}

function anchorRegion(
  bbox: readonly [number, number, number, number] | null,
): AnchorRegion | null {
  if (bbox === null) return null;
  const [left, top, right, bottom] = bbox;
  const proportional = bbox.every((value) => value >= 0 && value <= 1);
  return {
    placement: proportional ? "PROPORTIONAL" : "UNPLACEABLE",
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
    readout: proportional
      ? `${percent(left)}, ${percent(top)} to ${percent(right)}, ${percent(bottom)} of the page`
      : `${decimal(left)}, ${decimal(top)} to ${decimal(right)}, ${decimal(bottom)} in source units`,
  };
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/**
 * The cell a table-cell anchor addresses.
 *
 * `rowIndex` and `columnIndex` are zero-based, the way the corpus extractor records
 * them, while `reference` is the one-based address the publisher's own table uses. Both
 * are kept: the indices are what the record says, the reference is what a reader can
 * find in the document, and collapsing them would make an off-by-one invisible.
 */
export interface TableCellAddress {
  tableId: string;
  rowIndex: number;
  columnIndex: number;
  rowNumber: number;
  columnLetter: string;
  /** `Sheet1!C4` - the address as the source document numbers it. */
  reference: string;
}

/** Spreadsheet column letters, matching how the corpus extractor names them. */
export function columnLetter(columnIndex: number): string {
  let remaining = columnIndex + 1;
  let letters = "";
  while (remaining > 0) {
    const digit = (remaining - 1) % 26;
    letters = String.fromCharCode(65 + digit) + letters;
    remaining = Math.floor((remaining - 1) / 26);
  }
  return letters;
}

function tableCellAddress(locator: EvidenceLocator): TableCellAddress | null {
  const tableId = locator.table_id;
  const rowIndex = locator.row_index;
  const columnIndex = locator.column_index;
  if (
    tableId === undefined ||
    tableId === null ||
    rowIndex === undefined ||
    rowIndex === null ||
    columnIndex === undefined ||
    columnIndex === null
  ) {
    return null;
  }
  const letter = columnLetter(columnIndex);
  const rowNumber = rowIndex + 1;
  return {
    tableId,
    rowIndex,
    columnIndex,
    rowNumber,
    columnLetter: letter,
    reference: `${tableId}!${letter}${rowNumber}`,
  };
}

/**
 * What kind of place in a document an anchor names, and the coordinates of that place.
 *
 * The form is decided by what the locator carries rather than by its `kind` string,
 * which the serving contract types as free text and which the canonical corpus
 * vocabulary and the release fixtures spell differently - `PDF` and `PDF_PAGE` both
 * occur. A locator with a page is a page locator whatever it calls itself. `TABLE_CELL`
 * is the one kind read directly, because a cell anchor that lost its address carries
 * nothing else to recognise it by.
 */
export type AnchorPlacement =
  | {
      form: "PAGE";
      pdfPage: number | null;
      printedPage: string | null;
      region: AnchorRegion | null;
    }
  | {
      form: "TABLE_CELL";
      /** `null` when the release named a cell anchor without carrying its address. */
      cell: TableCellAddress | null;
    }
  | { form: "DOCUMENT" };

function anchorPlacement(locator: EvidenceLocator): AnchorPlacement {
  if (locator.kind === "TABLE_CELL") {
    return { form: "TABLE_CELL", cell: tableCellAddress(locator) };
  }
  if (locator.pdf_page !== null || locator.printed_page !== null || locator.bbox !== null) {
    return {
      form: "PAGE",
      pdfPage: locator.pdf_page,
      printedPage: locator.printed_page,
      region: anchorRegion(locator.bbox),
    };
  }
  return { form: "DOCUMENT" };
}

export interface SourceAnchor {
  key: string;
  kind: string;
  precision: AnchorPrecision;
  /** Human-readable location, for example `Printed page 231 (PDF page 47)`. */
  label: string;
  /** The same location with its edition named, so it cannot be read against another. */
  qualifiedLabel: string;
  /** Where in the document this anchor points, and by what coordinates. */
  placement: AnchorPlacement;
  /** The edition the coordinates above are coordinates *of*. */
  version: SourceVersionIdentity;
  pdfPage: number | null;
  printedPage: string | null;
  /**
   * The recorded region on the page, when the release carried one. Read
   * `placement.region` to draw it; this stays the raw payload value.
   */
  bbox: readonly [number, number, number, number] | null;
  sourceUri: string;
  /** Whether this region may actually be drawn over the source. */
  highlightAvailable: boolean;
  /** A region exists, but the licence forbids rendering it. */
  highlightSuppressedByLicence: boolean;
}

const PRECISION_RANK: Record<AnchorPrecision, number> = {
  EXACT_REGION: 0,
  CELL: 1,
  PAGE: 2,
  DOCUMENT: 3,
};

function hasExactRegion(detail: EvidenceDetail): boolean {
  return detail.locators.some(
    (locator) => locator.exact_highlight_available && locator.bbox !== null,
  );
}

function locatorPrecision(
  locator: EvidenceLocator,
  placement: AnchorPlacement,
): AnchorPrecision {
  if (locator.exact_highlight_available && locator.bbox !== null) return "EXACT_REGION";
  if (placement.form === "TABLE_CELL") return "CELL";
  if (placement.form === "PAGE") return "PAGE";
  return "DOCUMENT";
}

function placementLabel(
  locator: EvidenceLocator,
  placement: AnchorPlacement,
  precision: AnchorPrecision,
): string {
  if (placement.form === "TABLE_CELL") {
    return placement.cell === null
      ? "Table cell, address not carried"
      : `Table ${placement.cell.tableId}, cell ${placement.cell.columnLetter}${placement.cell.rowNumber}`;
  }
  if (placement.form === "PAGE") {
    const pages: string[] = [];
    if (placement.printedPage !== null) pages.push(`Printed page ${placement.printedPage}`);
    if (placement.pdfPage !== null) pages.push(`PDF page ${placement.pdfPage}`);
    if (pages.length) {
      const location = pages.length === 2 ? `${pages[0]} (${pages[1]})` : pages[0]!;
      return precision === "EXACT_REGION" ? `${location}, exact region` : location;
    }
  }
  return humanizeCode(locator.kind);
}

/**
 * What the interface can do with an anchor's recorded location.
 *
 * A region the release recorded but the licence withholds is reported as withheld, not
 * as absent, and a cell anchor whose address the release did not carry is reported as
 * missing rather than as no region at all. The three read alike in a summary and mean
 * quite different things about what was verified.
 */
export function anchorRegionStatus(anchor: SourceAnchor): string {
  if (anchor.placement.form === "TABLE_CELL") {
    return anchor.placement.cell === null
      ? "Cell address not carried"
      : "Cell address recorded";
  }
  if (anchor.highlightAvailable) return "Region verified";
  if (anchor.highlightSuppressedByLicence) return "Region withheld by licence";
  if (anchor.bbox !== null) return "Region not verified";
  return "No region recorded";
}

/**
 * Every locator on an evidence record, most precise first.
 *
 * The contract allows up to a hundred locators, and reading only the first discards the
 * exact region whenever a page-level locator happens to be listed ahead of it. Order is
 * stable within a precision band, so one payload always presents the same anchor.
 */
export function sourceAnchors(detail: EvidenceDetail): SourceAnchor[] {
  const renderAllowed = detail.render_allowed;
  const version = sourceVersionIdentity(detail);
  return detail.locators
    .map((locator, index) => {
      const placement = anchorPlacement(locator);
      const precision = locatorPrecision(locator, placement);
      const label = placementLabel(locator, placement, precision);
      const anchor: SourceAnchor = {
        key: `${detail.evidence_id}-locator-${index}`,
        kind: locator.kind,
        precision,
        label,
        qualifiedLabel: `${label} — ${version.title}, ${version.versionLabel} (${version.sourceVersionId})`,
        placement,
        version,
        pdfPage: locator.pdf_page,
        printedPage: locator.printed_page,
        bbox: locator.bbox,
        sourceUri: locator.source_uri,
        // A restricted asset can still carry a region. It is kept for provenance and
        // never drawn: the licence decides, not the presence of coordinates.
        highlightAvailable: renderAllowed && precision === "EXACT_REGION",
        highlightSuppressedByLicence: !renderAllowed && locator.bbox !== null,
      };
      return { anchor, index };
    })
    .sort((left, right) => {
      const byPrecision =
        PRECISION_RANK[left.anchor.precision] - PRECISION_RANK[right.anchor.precision];
      return byPrecision === 0 ? left.index - right.index : byPrecision;
    })
    .map(({ anchor }) => anchor);
}

export function primaryAnchor(detail: EvidenceDetail): SourceAnchor | null {
  return sourceAnchors(detail)[0] ?? null;
}

/** The location line shown wherever an evidence record is summarised in one row. */
export function locationSummary(detail: EvidenceDetail): string {
  return primaryAnchor(detail)?.label ?? "Location not supplied";
}

export interface Citation {
  /** 1-based, stable for the whole answer, assigned in claim order. */
  number: number;
  detail: EvidenceDetail;
  /** Short attribution for an inline chip, for example `WHO 2021`. */
  shortLabel: string;
  /** Where the passage sits in its document. */
  locationLabel: string;
  policy: RenderPolicy;
  anchor: SourceAnchor | null;
}

export interface CitationIndex {
  byEvidenceId: Map<string, Citation>;
  ordered: Citation[];
}

function emptyCitationIndex(): CitationIndex {
  return { byEvidenceId: new Map(), ordered: [] };
}

/**
 * Number the cited evidence once for the whole answer.
 *
 * Numbering follows first citation in claim order, so a reference keeps its number
 * wherever it appears: in a claim, in a conflict, or in the source inspector. Uncited
 * retrieval candidates are deliberately excluded. They carry a retrieval rank instead,
 * and a citation number would let them read as support.
 */
export function buildCitations(result: QuestionResult | null): CitationIndex {
  if (!result) return emptyCitationIndex();
  const detailById = new Map(
    result.evidence_details.map((detail) => [detail.evidence_id, detail]),
  );
  const index = emptyCitationIndex();

  for (const claim of result.claims) {
    for (const evidenceId of claim.evidence_ids) {
      if (index.byEvidenceId.has(evidenceId)) continue;
      const detail = detailById.get(evidenceId);
      if (!detail) continue;
      const citation: Citation = {
        number: index.ordered.length + 1,
        detail,
        shortLabel: shortAttribution(detail),
        locationLabel: locationSummary(detail),
        policy: renderPolicy(detail),
        anchor: primaryAnchor(detail),
      };
      index.byEvidenceId.set(evidenceId, citation);
      index.ordered.push(citation);
    }
  }
  return index;
}

/**
 * The citations for one claim, in the order the claim cites them.
 *
 * An evidence ID with no canonical detail is dropped rather than rendered as a bare
 * identifier: an answer-ready payload cannot contain one, so a gap means the payload is
 * not what it claims to be, and an unattributable citation is worse than none.
 */
export function claimCitations(claim: RenderedClaim, index: CitationIndex): Citation[] {
  return claim.evidence_ids.flatMap((evidenceId) => {
    const citation = index.byEvidenceId.get(evidenceId);
    return citation ? [citation] : [];
  });
}

export function shortAttribution(detail: EvidenceDetail): string {
  return `${detail.publisher_name} ${detail.source_version_label}`.trim();
}

/** Whether every cited source in this answer withholds its text under licence. */
export function isFullyLicenceRestricted(index: CitationIndex): boolean {
  return (
    index.ordered.length > 0 &&
    index.ordered.every((citation) => citation.policy.licenceRestricted)
  );
}

export function humanizeCode(value: string): string {
  const words = value.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
