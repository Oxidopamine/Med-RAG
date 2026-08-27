import { describe, expect, it } from "vitest";

import { parseContract, questionResultSchema } from "./contracts";
import {
  ABSTENTION_REASON_CODES,
  buildCitations,
  claimCitations,
  isFullyLicenceRestricted,
  presentAbstention,
  presentConflicts,
  primaryAnchor,
  renderPolicy,
  reviewableConflicts,
  sourceAnchors,
} from "./evidence-presentation";
import {
  abstainedResult,
  answerReadyResult,
  licensedWhoEvidence,
  restrictedWhoEvidence,
} from "./fixtures/answer-lane";
import type { EvidenceDetail } from "./types";

describe("frozen answer-lane fixtures", () => {
  it("satisfies the runtime contract the interface enforces", () => {
    expect(() =>
      parseContract(questionResultSchema, answerReadyResult(), "test fixture"),
    ).not.toThrow();
    expect(() =>
      parseContract(questionResultSchema, answerReadyResult({ licensed: true }), "test fixture"),
    ).not.toThrow();
    expect(() =>
      parseContract(
        questionResultSchema,
        abstainedResult({ reason_code: "NO_EVIDENCE_RETRIEVED" }),
        "test fixture",
      ),
    ).not.toThrow();
  });
});

describe("render policy", () => {
  it("withholds text and highlighting whenever the licence does", () => {
    const policy = renderPolicy(restrictedWhoEvidence());

    expect(policy.canQuote).toBe(false);
    expect(policy.canHighlightExactly).toBe(false);
    expect(policy.licenceRestricted).toBe(true);
    expect(policy.restriction).toBe("LICENCE_WITHHOLDS_TEXT");
  });

  it("keeps the licence decision independent of a payload that carried no text", () => {
    const policy = renderPolicy(
      licensedWhoEvidence({ exact_text: null }),
    );

    expect(policy.canQuote).toBe(false);
    expect(policy.licenceRestricted).toBe(false);
    expect(policy.restriction).toBe("TEXT_NOT_SUPPLIED");
  });

  it("permits the passage only when the licence does", () => {
    const policy = renderPolicy(licensedWhoEvidence());

    expect(policy.canQuote).toBe(true);
    expect(policy.canHighlightExactly).toBe(true);
    expect(policy.restriction).toBe("LICENSED");
  });

  it("refuses an exact highlight a restricted record should never have claimed", () => {
    // The API contract forbids this combination. If one ever arrives, the licence flag
    // decides, not the highlight flag.
    const contradictory = restrictedWhoEvidence({
      locators: [
        {
          kind: "PDF_PAGE",
          source_uri: "source://SV_WHO_HTN_2021/page/19",
          pdf_page: 19,
          printed_page: "11",
          bbox: [0.1, 0.2, 0.9, 0.4],
          exact_highlight_available: true,
        },
      ],
    });

    expect(renderPolicy(contradictory).canHighlightExactly).toBe(false);
    expect(sourceAnchors(contradictory)[0]?.highlightAvailable).toBe(false);
  });
});

describe("source anchors", () => {
  it("presents the most precise locator first regardless of payload order", () => {
    const anchors = sourceAnchors(licensedWhoEvidence());

    expect(anchors.map((anchor) => anchor.precision)).toEqual(["EXACT_REGION", "DOCUMENT"]);
    expect(anchors[0]?.label).toBe("Printed page 11 (PDF page 19), exact region");
    expect(anchors[0]?.highlightAvailable).toBe(true);
  });

  it("keeps a recorded region visible as withheld rather than absent", () => {
    const anchor = primaryAnchor(restrictedWhoEvidence());

    expect(anchor?.precision).toBe("PAGE");
    expect(anchor?.label).toBe("Printed page 11 (PDF page 19)");
    expect(anchor?.bbox).toEqual([0.12, 0.31, 0.88, 0.44]);
    expect(anchor?.highlightAvailable).toBe(false);
    expect(anchor?.highlightSuppressedByLicence).toBe(true);
  });

  it("falls back to the locator kind when no page is supplied", () => {
    const anchor = primaryAnchor(
      restrictedWhoEvidence({
        locators: [
          {
            kind: "SECTION",
            source_uri: "source://SV_WHO_HTN_2021/section/treatment",
            pdf_page: null,
            printed_page: null,
            bbox: null,
            exact_highlight_available: false,
          },
        ],
      }),
    );

    expect(anchor?.precision).toBe("DOCUMENT");
    expect(anchor?.label).toBe("Section");
    expect(anchor?.highlightSuppressedByLicence).toBe(false);
  });
});

describe("citations", () => {
  it("numbers cited evidence once, in claim order, and shares it across claims", () => {
    const result = answerReadyResult();
    const index = buildCitations(result);

    expect(index.ordered.map((citation) => citation.detail.evidence_id)).toEqual([
      "EV_WHO_HTN_001",
      "EV_WHO_HTN_003",
      "EV_WHO_HTN_002",
    ]);
    expect(index.byEvidenceId.get("EV_WHO_HTN_002")?.number).toBe(3);
    expect(claimCitations(result.claims[1]!, index).map((citation) => citation.number)).toEqual([
      3,
    ]);
  });

  it("never numbers an uncited retrieval candidate", () => {
    const index = buildCitations(answerReadyResult());

    expect(index.byEvidenceId.has("EV_WHO_HTN_014")).toBe(false);
  });

  it("drops a cited evidence ID it cannot attribute to a source", () => {
    const result = answerReadyResult();
    result.evidence_details = result.evidence_details.filter(
      (detail) => detail.evidence_id !== "EV_WHO_HTN_003",
    );
    const index = buildCitations(result);

    expect(claimCitations(result.claims[0]!, index).map((citation) => citation.number)).toEqual([
      1,
    ]);
  });

  it("reports when every cited source withholds its text", () => {
    expect(isFullyLicenceRestricted(buildCitations(answerReadyResult()))).toBe(true);
    expect(
      isFullyLicenceRestricted(buildCitations(answerReadyResult({ licensed: true }))),
    ).toBe(false);
    expect(isFullyLicenceRestricted(buildCitations(null))).toBe(false);
  });
});

describe("conflict taxonomy", () => {
  const citations = buildCitations(answerReadyResult());

  it("types a conflict and resolves the passages it names", () => {
    const [conflict] = presentConflicts(answerReadyResult().conflicts, citations);

    expect(conflict?.type).toBe("OUTDATED_INFORMATION");
    expect(conflict?.descriptor.label).toBe("Superseded information");
    expect(conflict?.descriptor.tone).toBe("caution");
    expect(conflict?.evidenceIds).toEqual(["EV_WHO_HTN_001", "EV_WHO_HTN_003"]);
    expect(conflict?.citations.map((citation) => citation.number)).toEqual([1, 2]);
    expect(conflict?.unresolvedEvidenceIds).toEqual([]);
  });

  it("does not present an explicit no-conflict record as something to review", () => {
    const conflicts = presentConflicts(
      [
        {
          conflict_type: "NO_CONFLICT",
          summary: "The cited passages agree.",
          evidence_ids: "EV_WHO_HTN_001, EV_WHO_HTN_002",
        },
      ],
      citations,
    );

    expect(conflicts[0]?.requiresReview).toBe(false);
    expect(reviewableConflicts(conflicts)).toEqual([]);
  });

  it("marks an unrecognised type as unclassified instead of inferring one", () => {
    const [conflict] = presentConflicts([
      { conflict_type: "DOSE_DISAGREEMENT", summary: "Doses differ." },
    ]);

    expect(conflict?.type).toBeNull();
    expect(conflict?.unrecognizedType).toBe("DOSE_DISAGREEMENT");
    expect(conflict?.descriptor.label).toBe("Unclassified disagreement");
    expect(conflict?.requiresReview).toBe(true);
  });

  it("names evidence the answer did not cite rather than dropping it", () => {
    const [conflict] = presentConflicts(
      [{ conflict_type: "CONTRADICTORY_SOURCE", evidence_ids: "EV_WHO_HTN_001, EV_UNKNOWN" }],
      citations,
    );

    expect(conflict?.citations.map((citation) => citation.number)).toEqual([1]);
    expect(conflict?.unresolvedEvidenceIds).toEqual(["EV_UNKNOWN"]);
    expect(conflict?.summary).toBeNull();
  });

  it("keeps fields outside the typed contract instead of discarding them", () => {
    const [conflict] = presentConflicts([
      { organization: "Regional formulary", rationale: "Local policy is stricter.", note: "  " },
    ]);

    expect(conflict?.extraFields).toEqual([
      ["organization", "Regional formulary"],
      ["rationale", "Local policy is stricter."],
    ]);
  });
});

describe("abstention reason codes", () => {
  it("gives every reason code its own title and next step", () => {
    const presentations = ABSTENTION_REASON_CODES.map((reason_code) =>
      presentAbstention({ reason_code, message: "", missing_evidence_roles: [], closest_evidence_ids: [] }, "ABSTAINED"),
    );

    expect(presentations.every((presentation) => presentation.code !== null)).toBe(true);
    expect(new Set(presentations.map((presentation) => presentation.title)).size).toBe(
      ABSTENTION_REASON_CODES.length,
    );
    expect(new Set(presentations.map((presentation) => presentation.nextStep)).size).toBe(
      ABSTENTION_REASON_CODES.length,
    );
  });

  it("does not offer a retry for a state a retry cannot change", () => {
    const noRelease = presentAbstention(
      abstainedResult({ reason_code: "NO_ACTIVE_RELEASE" }).abstention,
      "ABSTAINED",
    );
    const noClaim = presentAbstention(
      abstainedResult({ reason_code: "NO_CLAIM_SURVIVED_GROUNDING" }).abstention,
      "ABSTAINED",
    );

    expect(noRelease.retryable).toBe(false);
    expect(noRelease.cause).toBe("service");
    expect(noClaim.retryable).toBe(true);
    expect(noClaim.cause).toBe("grounding");
  });

  it("prefers the service explanation over this build's wording", () => {
    const presentation = presentAbstention(
      abstainedResult({
        reason_code: "MODEL_DECLARED_INSUFFICIENT",
        message: "The retrieved guideline passages do not answer this question.",
      }).abstention,
      "ABSTAINED",
    );

    expect(presentation.message).toBe(
      "The retrieved guideline passages do not answer this question.",
    );
  });

  it("keeps an unrecognised code visible instead of hiding it behind generic wording", () => {
    const presentation = presentAbstention(
      abstainedResult({
        reason_code: "SOME_FUTURE_CODE",
        message: "A future gate withheld the answer.",
      }).abstention,
      "ABSTAINED",
    );

    expect(presentation.code).toBeNull();
    expect(presentation.rawCode).toBe("SOME_FUTURE_CODE");
    expect(presentation.message).toBe("A future gate withheld the answer.");
    expect(presentation.title).toBe("The evidence gate withheld the answer");
  });

  it("describes a failed run as a failure, not as missing evidence", () => {
    const presentation = presentAbstention(null, "FAILED");

    expect(presentation.title).toBe("The review failed safely");
    expect(presentation.rawCode).toBeNull();
    expect(presentation.cause).toBe("service");
  });

  it("carries the missing roles and closest evidence the service reported", () => {
    const presentation = presentAbstention(
      abstainedResult({
        reason_code: "NO_CLAIM_SURVIVED_GROUNDING",
        missing_evidence_roles: ["EXCEPTION_OR_CONTRAINDICATION"],
        closest_evidence_ids: ["EV_WHO_HTN_014"],
      }).abstention,
      "ABSTAINED",
    );

    expect(presentation.missingEvidenceRoles).toEqual(["EXCEPTION_OR_CONTRAINDICATION"]);
    expect(presentation.closestEvidenceIds).toEqual(["EV_WHO_HTN_014"]);
  });
});

describe("fixture shape", () => {
  it("keeps every WHO record restricted, as the active trust root has them", () => {
    const details: EvidenceDetail[] = answerReadyResult().evidence_details;

    expect(details.every((detail) => !detail.render_allowed)).toBe(true);
    expect(details.every((detail) => detail.exact_text === null)).toBe(true);
  });
});
