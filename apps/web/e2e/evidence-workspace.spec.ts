import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { QuestionResult } from "../lib/types";

const API_URL = "http://localhost:8000";
const QUESTION =
  "How often should viral load be monitored for an adult established on antiretroviral therapy?";

const context = {
  age: 34,
  sex: null,
  conditions: ["HIV_INFECTION"],
  known_absent_conditions: [],
  measurements: [
    {
      concept: "HIV_VIRAL_LOAD",
      value: 1200,
      unit: "copies/mL",
      provenance: "USER_TEXT_EXPLICIT",
    },
  ],
  special_populations: ["ON_ANTIRETROVIRAL_THERAPY"],
  known_absent_special_populations: [],
  care_setting: null,
  jurisdiction: null,
  question_type: "treatment_guideline",
  topic: "antiretroviral_therapy",
  inferred_fields: [],
};

const abstainedResult = {
  question_id: "question-abstained",
  question: QUESTION,
  status: "ABSTAINED",
  corpus_release: null,
  interpreted_context: context,
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
    reason_code: "NO_APPROVED_CORPUS",
    message: "No approved guideline corpus is configured.",
    missing_evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
    closest_evidence_ids: [],
    closest_evidence: [],
  },
  created_at: "2026-08-25T10:00:00+00:00",
  updated_at: "2026-08-25T10:00:02+00:00",
} satisfies QuestionResult;

const verifiedResult = {
  question_id: "question-verified",
  question: QUESTION,
  status: "ANSWER_READY",
  corpus_release: {
    corpus_release_id: "guidelines-2026-08",
    manifest_sha256: "a".repeat(64),
    qdrant_collection: "guidelines_2026_08",
    activated_at: "2026-08-24T08:00:00+00:00",
    serving_mode: "ACTIVATED" as const,
  },
  interpreted_context: context,
  claims: [
    {
      claim_id: "claim-viral-load",
      text: "Viral load should be measured at six months and twelve months after starting antiretroviral therapy, then every twelve months once suppressed.",
      evidence_ids: ["evidence-renderable", "evidence-restricted"],
      verification_status: "SUPPORTED",
    },
    {
      claim_id: "claim-monitoring",
      text: "A confirmed viral load above 1000 copies/mL indicates treatment failure and requires a regimen review.",
      evidence_ids: ["evidence-restricted"],
      verification_status: "SUPPORTED",
    },
  ],
  evidence_details: [
    {
      evidence_id: "evidence-renderable",
      exact_text:
        "Viral load should be measured at six months and twelve months after starting antiretroviral therapy, and every twelve months thereafter once suppressed.",
      evidence_type: "GUIDELINE_RECOMMENDATION",
      evidence_roles: ["CURRENT_PRIMARY_GUIDELINE", "POPULATION_APPLICABILITY"],
      section_path: ["Antiretroviral therapy", "Monitoring"],
      source_id: "source-who-hiv",
      source_version_id: "source-who-hiv-2021",
      source_title: "Consolidated guidelines on HIV prevention, testing, treatment and service delivery",
      source_version_label: "2021 edition",
      publisher_name: "World Health Organization",
      source_url: "https://example.test/guideline",
      source_class: "E1",
      jurisdiction: "WORLD",
      language: "en",
      lifecycle_status: "CURRENT",
      effective_from: "2026-01-01",
      effective_to: null,
      approval_status: "APPROVED",
      render_allowed: true,
      locators: [
        {
          kind: "PDF_PAGE",
          source_uri: "https://example.test/guideline.pdf",
          pdf_page: 47,
          printed_page: "231",
          bbox: [0.1, 0.2, 0.9, 0.35],
          exact_highlight_available: true,
        },
      ],
    },
    {
      evidence_id: "evidence-restricted",
      exact_text: null,
      evidence_type: "GUIDELINE_EXCEPTION",
      evidence_roles: ["EXCEPTION", "DOSING_MODIFIER"],
      section_path: ["Antiretroviral therapy", "Treatment failure"],
      source_id: "source-who-testing",
      source_version_id: "source-who-testing-2019",
      source_title: "Consolidated guidelines on HIV testing services",
      source_version_label: "2019 edition",
      publisher_name: "World Health Organization",
      source_url: "https://example.test/who-testing-services",
      source_class: "E1",
      jurisdiction: "WORLD",
      language: "en",
      lifecycle_status: "CURRENT",
      effective_from: "2026-08-01",
      effective_to: null,
      approval_status: "APPROVED",
      render_allowed: false,
      locators: [
        {
          kind: "SECTION",
          source_uri: "https://example.test/who-testing-services",
          pdf_page: 49,
          printed_page: "233",
          bbox: null,
          exact_highlight_available: false,
        },
      ],
    },
    {
      evidence_id: "evidence-candidate",
      exact_text:
        "Implementation of routine viral-load monitoring depends on laboratory capacity and sample transport.",
      evidence_type: "GUIDELINE_RECOMMENDATION",
      evidence_roles: ["CURRENT_PRIMARY_GUIDELINE"],
      section_path: ["Implementation considerations"],
      source_id: "source-who-dak",
      source_version_id: "source-who-dak-2022",
      source_title: "WHO SMART Guidelines: HIV Digital Adaptation Kit",
      source_version_label: "2026",
      publisher_name: "World Health Organization",
      source_url: "https://example.test/consensus",
      source_class: "E1",
      jurisdiction: "WORLD",
      language: "en",
      lifecycle_status: "CURRENT",
      effective_from: "2026-03-01",
      effective_to: null,
      approval_status: "APPROVED",
      render_allowed: true,
      locators: [
        {
          kind: "SECTION",
          source_uri: "https://example.test/consensus",
          pdf_page: null,
          printed_page: null,
          bbox: null,
          exact_highlight_available: false,
        },
        // A cell anchor carrying the address the corpus records for it, so the drawn
        // table form is exercised alongside the two page forms above.
        {
          kind: "TABLE_CELL",
          source_uri: "https://example.test/consensus",
          pdf_page: null,
          printed_page: null,
          bbox: null,
          exact_highlight_available: false,
          table_id: "FollowUpSchedule",
          row_index: 3,
          column_index: 2,
        },
      ],
    },
  ],
  retrieval_candidates: [{ evidence_id: "evidence-candidate", retrieval_rank: 4 }],
  conflicts: [
    {
      conflict_type: "CONFLICTING_RECOMMENDATIONS",
      summary:
        "The testing guideline states a confirmation interval the treatment guideline does not repeat.",
      evidence_ids: ["evidence-renderable", "evidence-restricted"],
    },
    {
      organization: "National ART programme",
      recommendation: "Confirm with a second sample before switching regimen.",
      rationale: "Local programme policy is more conservative.",
    },
  ],
  verification_summary: {
    rendered_claims: 2,
    supported_claims: 2,
    withheld_claims: 0,
  },
  abstention: null,
  created_at: "2026-08-25T10:00:00+00:00",
  updated_at: "2026-08-25T10:00:08+00:00",
} satisfies QuestionResult;

async function mockReadiness(page: Page, approved: boolean) {
  await page.route(`${API_URL}/health/ready`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify({
        status: "ready",
        corpus_registry_available: true,
        approved_corpus_available: approved,
        corpus_release_id: approved ? "guidelines-2026-08" : null,
        clinical_use_allowed: false,
      }),
    });
  });
}

interface MockRunOptions {
  failFirstSubmission?: boolean;
  streamUnavailable?: boolean;
}

async function mockRun(
  page: Page,
  result: QuestionResult,
  options: MockRunOptions = {},
) {
  let attempts = 0;
  const submittedBodies: unknown[] = [];

  // The inspector asks for page images and table rows as the reader moves through the
  // ranking. Left unmocked, the refused connection lands as an error state, and how soon
  // it lands depends on the host: fast enough on Linux to beat the assertions, slow
  // enough on Windows to lose to them. A 404 is the API's stable answer for a page that
  // has no image, so the frames settle the same way everywhere.
  await page.route(`${API_URL}/v1/sources/**`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: JSON.stringify({ detail: "No rendering for this source page" }),
    });
  });

  await page.route(`${API_URL}/v1/questions`, async (route) => {
    attempts += 1;
    submittedBodies.push(route.request().postDataJSON());
    if (options.failFirstSubmission && attempts === 1) {
      await route.fulfill({
        contentType: "application/json",
        status: 503,
        body: JSON.stringify({ detail: "Service temporarily unavailable" }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      status: 202,
      body: JSON.stringify({ question_id: result.question_id, status: "QUEUED" }),
    });
  });

  await page.route(`${API_URL}/v1/questions/${result.question_id}/events`, async (route) => {
    if (options.streamUnavailable) {
      await route.fulfill({ status: 503, body: "Progress stream unavailable" });
      return;
    }
    const statuses =
      result.status === "ANSWER_READY"
        ? [
            "CONTEXT_EXTRACTED",
            "RETRIEVING",
            "RERANKING",
            "SEARCHING_COUNTER_EVIDENCE",
            "CHECKING_EVIDENCE_COMPLETENESS",
            "VERIFYING",
            "ANSWER_READY",
          ]
        : ["CONTEXT_EXTRACTED", result.status];
    const body = statuses
      .flatMap((status, index) => [
        "event: progress",
        `data: ${JSON.stringify({
          question_id: result.question_id,
          sequence: index + 1,
          status,
          occurred_at: `2026-08-25T10:00:${String(index + 1).padStart(2, "0")}+00:00`,
        })}`,
        "",
      ])
      .concat("")
      .join("\n");
    await route.fulfill({ contentType: "text/event-stream", status: 200, body });
  });

  await page.route(`${API_URL}/v1/questions/${result.question_id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify(result),
    });
  });

  return {
    attempts: () => attempts,
    submittedBodies,
  };
}

async function ask(page: Page, question = QUESTION) {
  await expect(
    page.getByText(/^(Approved corpus available|No active corpus)$/),
  ).toBeVisible();
  const textbox = page.getByRole("textbox", { name: "Guideline question" });
  const submit = page.getByRole("button", { name: "Review evidence" });
  await textbox.fill(question);
  await expect(textbox).toHaveValue(question);
  await expect(submit).toBeEnabled();
  await submit.click();
}

/**
 * Rules whose `incomplete` results are accepted, each with the reason it cannot resolve.
 *
 * `color-contrast` is here because axe cannot read a background it did not compute: the
 * header sits on a gradient and several surfaces are translucent tokens, so axe reports
 * "undeterminable" rather than a ratio. Contrast for those nodes is measured directly
 * against the resolved ancestor background instead - see `docs/frontend-audit.md`.
 *
 * Nothing else belongs here. An `incomplete` result axe rates "serious" is a real defect
 * it merely could not confirm, and asserting only on `violations` is how four dropped
 * ARIA labels stayed green through a stylesheet rewrite that renamed one of them.
 */
const AXE_INCOMPLETE_ALLOWLIST = new Set(["color-contrast"]);

/**
 * Repair `crypto.getRandomValues` where the browser build ships it broken.
 *
 * Playwright's bundled Firefox throws `OperationError` from `getRandomValues` on every
 * call - secure context or not, sixteen bytes or sixty-four kilobytes - while
 * `crypto.randomUUID` beside it works. axe-core generates internal element identifiers
 * through that call, so `AxeBuilder.analyze` dies before it produces a single result and
 * every accessibility assertion in this file fails on Firefox for a reason that has
 * nothing to do with the page.
 *
 * The patch is deliberately conditional: it probes the native implementation first and
 * installs nothing when that works, so Chromium is untouched and Firefox reverts to its
 * own RNG the moment the upstream build is fixed. The replacement is seeded from
 * `crypto.randomUUID` because that is the strongest source this build actually honours,
 * falling back to `Math.random` only when even that is absent. Neither is a security
 * claim and neither needs to be - the bytes name DOM nodes inside a test-only scan and
 * reach no assertion, no fixture, and no shipped code.
 *
 * It installs on the browser *context*, not the page, because `AxeBuilder.analyze`
 * assembles its final report by opening a second, blank page of its own through
 * `context.newPage()`. A page-scoped init script never reaches that page, so the scan
 * collects every partial result and then dies at the last step.
 */
async function repairBrokenBrowserRandomness(page: Page) {
  await page.context().addInitScript(() => {
    const target = globalThis.crypto;
    if (!target || typeof target.getRandomValues !== "function") return;
    try {
      target.getRandomValues(new Uint8Array(1));
      return;
    } catch {
      // Falls through to the replacement below.
    }

    const uuid =
      typeof target.randomUUID === "function" ? () => target.randomUUID() : null;
    const nextByte = (() => {
      let pending: number[] = [];
      return () => {
        if (pending.length === 0) {
          pending = uuid
            ? (uuid().replace(/-/g, "").match(/../g) ?? []).map((pair) =>
                Number.parseInt(pair, 16),
              )
            : Array.from({ length: 16 }, () => Math.floor(Math.random() * 256));
        }
        return pending.pop() ?? 0;
      };
    })();

    Object.defineProperty(target, "getRandomValues", {
      configurable: true,
      writable: true,
      value: <T extends ArrayBufferView | null>(buffer: T): T => {
        if (buffer == null) return buffer;
        const bytes = new Uint8Array(
          buffer.buffer,
          buffer.byteOffset,
          buffer.byteLength,
        );
        for (let index = 0; index < bytes.length; index += 1) {
          bytes[index] = nextByte();
        }
        return buffer;
      },
    });
  });
}

test.beforeEach(async ({ page }) => {
  await repairBrokenBrowserRandomness(page);
});

async function expectNoAxeViolations(page: Page) {
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);

  const unresolved = accessibility.incomplete
    .filter((result) => !AXE_INCOMPLETE_ALLOWLIST.has(result.id))
    .map((result) => ({
      id: result.id,
      impact: result.impact,
      targets: result.nodes.map((node) => node.target.join(" ")),
    }));
  expect(unresolved).toEqual([]);
}

test("starts empty and progressively discloses guidance, examples, scope, and safety", async ({
  page,
}) => {
  await mockReadiness(page, true);
  await page.goto("/");

  const question = page.getByRole("textbox", { name: "Guideline question" });
  await expect(question).toHaveValue("");
  await expect(page.getByRole("button", { name: "Review evidence" })).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "What guideline decision are you reviewing?" }),
  ).toBeVisible();
  await expect(page.getByText("Population", { exact: true })).toBeVisible();
  await expect(page.getByText("Condition", { exact: true })).toBeVisible();
  await expect(page.getByText("Decision", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("note", { name: "Research use notice" }),
  ).toContainText("Not authorized for patient care");
  await expect(page.getByRole("note", { name: "Research use notice" })).toContainText(
    "Do not enter names, identifiers, or other protected health information",
  );
  await expect(
    page.getByRole("heading", { name: "Evidence-gated guideline review" }),
  ).toBeVisible();
  await expect(page.getByText("Approved corpus available", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Answer" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Verification" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Source inspector" })).toHaveCount(0);

  await page.getByRole("button", { name: QUESTION }).click();
  await expect(question).toHaveValue(QUESTION);
  await page.getByRole("button", { name: "Clear question" }).click();
  await expect(question).toBeFocused();
  await expect(question).toHaveValue("");

  // The scope disclosure states the release's coverage rather than offering a country
  // filter: every record is scoped WORLD and the serving path widens any selection to
  // include it, so a jurisdiction control could only claim to narrow.
  await page.locator("summary").filter({ hasText: "Sources" }).click();
  const bodies = page.getByRole("list", { name: "Guideline sources" });
  await expect(bodies.getByText("WHO HIV guidelines")).toBeVisible();
  await expect(bodies.getByText("Active")).toBeVisible();
  await expect(bodies.getByText("Coming soon")).toHaveCount(3);
  await expect(page.getByRole("checkbox")).toHaveCount(0);

  await expectNoAxeViolations(page);
});

test("renders an actionable fail-closed abstention without an empty source viewer", async ({
  page,
}) => {
  await mockReadiness(page, false);
  await mockRun(page, abstainedResult);
  await page.goto("/");
  await ask(page);

  await expect(page.getByRole("heading", { name: "Answer" })).toBeVisible();
  await expect(page.getByText("Answer withheld", { exact: true })).toBeVisible();
  await expect(page.getByText("No approved guideline corpus is active")).toBeVisible();
  await expect(
    page.getByLabel("Answer").getByText("No approved guideline corpus is configured."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Interpreted patient context" }),
  ).toBeVisible();
  await expect(page.getByText("Extracted, not clinically validated")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Source inspector" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Exact guideline|Evidence provenance/ })).toHaveCount(
    0,
  );
  await expect(page.getByText("Answer gate blocked", { exact: true })).toBeVisible();

  await expect(page.getByText("NO_APPROVED_CORPUS", { exact: true })).toBeVisible();
  await expect(page.getByText("Retrying alone will not change this.")).toBeVisible();

  await page.getByText("Which evidence was missing?", { exact: true }).click();
  await expect(page.getByText("Current primary guideline", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Edit context" }).click();
  await expect(
    page.getByRole("dialog", { name: "Edit interpreted context" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await expectNoAxeViolations(page);
});

test("connects verified claims to exact and restricted source evidence", async ({ page }) => {
  await mockReadiness(page, true);
  const run = await mockRun(page, verifiedResult);
  await page.goto("/");

  await page.locator("summary").filter({ hasText: "Sources" }).click();
  await page.getByRole("textbox", { name: "Organizations" }).fill("ACC, AHA, ACC");
  await page.getByRole("textbox", { name: "Organizations" }).blur();
  await ask(page);

  // The result is persistent; the completion notice is transient, so it is asserted
  // second and within the window it stays on screen.
  await expect(page.getByText("Evidence-gated result", { exact: true })).toBeVisible();
  await expect(page.getByText("Evidence review ready", { exact: true })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByText("Approved corpus release", { exact: true })).toBeVisible();
  await expect(page.getByText("guidelines-2026-08", { exact: false }).first()).toBeVisible();
  expect(run.submittedBodies).toEqual([
    expect.objectContaining({
      question: QUESTION,
      source_filters: {
        jurisdictions: ["WORLD"],
        organizations: ["ACC", "AHA"],
      },
    }),
  ]);

  const sourceInspector = page.getByRole("region", { name: "Source inspector" });

  // The passage is read in the inspector alone; the claim-linked quotation panel is gone.
  await expect(page.getByRole("heading", { name: "Source inspector" })).toBeVisible();
  await expect(page.locator("mark")).toHaveText(
    "Viral load should be measured at six months and twelve months after starting antiretroviral therapy, and every twelve months thereafter once suppressed.",
  );
  await expect(page.getByText("Record 1 of 3", { exact: true })).toBeVisible();
  // A licensed record states its anchor precision, which exact highlighting will use.
  await expect(page.getByText("Exact region").first()).toBeVisible();
  await expect(
    page.getByText("Printed page 231 (PDF page 47), exact region").first(),
  ).toBeVisible();
  await expect(sourceInspector.getByText("Exact location verified").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Open publisher source" })).toHaveAttribute(
    "href",
    "https://example.test/guideline",
  );

  // The anchor viewer places the recorded region and names the edition it belongs to.
  // Two editions of one guideline can carry the same page and near-identical text, so
  // the version identity is part of the location, not a footnote beside it.
  const anchorViewer = sourceInspector.getByRole("region", { name: "Location in source" });
  await expect(anchorViewer.getByText("source-who-hiv-2021")).toBeVisible();
  await expect(
    anchorViewer.getByText("Printed page 231 · PDF page 47 of 2021 edition").first(),
  ).toBeVisible();
  await expect(
    anchorViewer.getByText("10.0%, 20.0% to 90.0%, 35.0% of the page"),
  ).toBeVisible();
  await expect(
    anchorViewer.getByRole("figure", { name: "Printed page 231 · PDF page 47 of 2021 edition" }),
  ).toBeVisible();
  await expect(
    anchorViewer.getByRole("group", {
      name: /Printed page 231 \(PDF page 47\), exact region — Consolidated guidelines on HIV prevention, testing, treatment and service delivery, 2021 edition \(source-who-hiv-2021\)/,
    }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Next ranked result" }).click();
  await expect(page.getByText("Record 2 of 3", { exact: true })).toBeVisible();
  // The licence decision is stated where the passage would have been read.
  await expect(
    sourceInspector.getByText("Licence does not permit showing this passage"),
  ).toBeVisible();
  await expect(
    sourceInspector.getByText("Not available", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("Consolidated guidelines on HIV testing services", { exact: true }).first()).toBeVisible();
  // Restricted, and the release carried no region: the page is stated, nothing is drawn,
  // and the reason no publisher content appears is named.
  await expect(anchorViewer.getByText("No region recorded on this page")).toBeVisible();
  await expect(anchorViewer.getByText("source-who-testing-2019")).toBeVisible();
  await expect(
    anchorViewer.getByText(/withheld under licence and is not reproduced here/),
  ).toBeVisible();

  await page.getByRole("button", { name: "Next ranked result" }).click();
  await expect(page.getByText("Record 3 of 3", { exact: true })).toBeVisible();
  await expect(page.getByText("Retrieved, not cited", { exact: true })).toBeVisible();
  await expect(page.getByText("Retrieved at rank 4. No claim cites it.")).toBeVisible();
  // A table-cell anchor resolves to the address the source document itself uses.
  await expect(
    anchorViewer.getByText("Cell C4 of table FollowUpSchedule in 2026"),
  ).toBeVisible();
  await expect(anchorViewer.getByText("FollowUpSchedule!C4")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next ranked result" })).toBeDisabled();
  // Cited and uncited passages are one ranking, split by a labelled boundary rather than
  // by two separately-numbered panels - so where the gate stopped citing is visible.
  await expect(sourceInspector.getByText("Not cited", { exact: true })).toBeVisible();

  const secondClaim = page
    .getByRole("listitem")
    .filter({ hasText: "A confirmed viral load above 1000 copies/mL indicates treatment failure" });
  await secondClaim.getByRole("button", { name: /^Reference 2:/ }).click();
  await expect(secondClaim).toHaveClass(/selected-claim/);

  // A typed conflict is named and left unresolved; an untyped record is shown as
  // unclassified with its fields intact rather than presented as a classified finding.
  await expect(page.getByText("2 for review", { exact: true })).toBeVisible();
  await expect(page.getByText("Conflicting recommendations")).toBeVisible();
  await expect(
    page.getByText(
      "Both recommendations are shown as published. Choosing between them is a clinical judgement, not a system output.",
    ),
  ).toBeVisible();
  await expect(page.getByText("Unclassified disagreement")).toBeVisible();
  await expect(page.getByText("Local programme policy is more conservative.")).toBeVisible();

  // The audit is open on every state, answer-ready included: five named gates and what
  // each concluded is the product's differentiator, and it was collapsed by default on
  // precisely the result where all of them passed.
  const auditDetails = page.locator("details").filter({ hasText: "Audit steps" });
  await expect(auditDetails).toHaveAttribute("open", "");
  await expect(auditDetails.getByText("Answer gate passed", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Dismiss success message" }).click();
  await expect(page.getByText("Evidence review ready", { exact: true })).toHaveCount(0);
  await expectNoAxeViolations(page);
});

test("presents a calm request error and retries the same request", async ({ page }) => {
  await mockReadiness(page, true);
  const run = await mockRun(page, abstainedResult, { failFirstSubmission: true });
  await page.goto("/");
  await ask(page);

  const error = page
    .getByRole("alert")
    .filter({ hasText: "The review could not be started" });
  await expect(error).toContainText("The review could not be started");
  await expect(error).toContainText("Nothing unsafe was displayed");
  await expect(page.getByRole("heading", { name: "Answer" })).toHaveCount(0);
  await expect(page.getByText("Technical details", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Dismiss error message" })).toBeVisible();

  await error.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("Answer withheld", { exact: true })).toBeVisible();
  await expect(error).toHaveCount(0);
  expect(run.attempts()).toBe(2);
  expect(run.submittedBodies[1]).toEqual(run.submittedBodies[0]);
});

test("falls back to polling when the progress stream is unavailable", async ({ page }) => {
  await mockReadiness(page, false);
  await mockRun(page, abstainedResult, { streamUnavailable: true });
  await page.goto("/");
  await ask(page);

  await expect(page.getByText("Answer withheld", { exact: true })).toBeVisible();
  await expect(page.getByText("Answer gate blocked", { exact: true })).toBeVisible();
});

test("fills the composer from the question bank without submitting", async ({ page }) => {
  await mockReadiness(page, true);
  const run = await mockRun(page, verifiedResult);
  await page.goto("/");

  const bank = page.locator("summary").filter({ hasText: "Question bank" });
  await bank.click();
  const search = page.getByRole("searchbox", { name: "Search questions" });
  await search.fill("straight away");
  const choice = page.getByRole("list", { name: "Questions" }).getByRole("button").first();
  await expect(choice).toContainText("diagnosed this morning");
  await choice.click();

  const textbox = page.getByRole("textbox", { name: "Guideline question" });
  await expect(textbox).toHaveValue(/diagnosed this morning/);
  await expect(textbox).toBeFocused();
  await expect(page.locator("details").filter({ hasText: "Question bank" })).not.toHaveAttribute(
    "open",
    "",
  );
  expect(run.attempts()).toBe(0);

  await bank.click();
  await page.keyboard.press("Escape");
  await expect(bank).toBeFocused();
  await expectNoAxeViolations(page);
});

test("keeps the evidence composer usable at a 320px viewport", async ({
  browserName,
  page,
}) => {
  test.skip(browserName !== "chromium", "One narrow-layout accessibility pass avoids redundant engines.");
  await page.setViewportSize({ width: 320, height: 720 });
  await mockReadiness(page, true);
  await page.goto("/");

  const question = page.getByRole("textbox", { name: "Guideline question" });
  const submit = page.getByRole("button", { name: "Review evidence" });
  const sources = page.locator("summary").filter({ hasText: "Sources" });
  const example = page.getByRole("button", { name: QUESTION });

  await expect(question).toBeVisible();
  await expect(submit).toBeVisible();
  await expect(sources).toBeVisible();
  await expect(example).toBeVisible();

  const measurements = await page.evaluate(() => {
    const questionField = document.querySelector<HTMLTextAreaElement>("textarea");
    const controls = [
      document.querySelector<HTMLButtonElement>('button[type="submit"]'),
      document.querySelector<HTMLElement>("summary"),
    ];
    return {
      fontSize: questionField ? Number.parseFloat(getComputedStyle(questionField).fontSize) : 0,
      controlHeights: controls.map((control) => control?.getBoundingClientRect().height ?? 0),
      overflow:
        document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });

  expect(measurements.fontSize).toBeGreaterThanOrEqual(16);
  expect(measurements.controlHeights.every((height) => height >= 44)).toBe(true);
  expect(measurements.overflow).toBeLessThanOrEqual(1);

  await sources.click();
  await expect(page.getByText("Source coverage", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sources).toBeFocused();
  await expectNoAxeViolations(page);
});

test("keeps the verified workflow ordered, operable, and accessible on mobile", async ({
  browserName,
  page,
}) => {
  test.skip(browserName !== "chromium", "One mobile accessibility pass avoids redundant engines.");
  await page.setViewportSize({ width: 390, height: 844 });
  await mockReadiness(page, true);
  await mockRun(page, verifiedResult);
  await page.goto("/");
  await ask(page);

  // The exact-quotation panel was removed: the passage is read in the inspector alone.
  // What has to stay ordered on a narrow screen is answer, verification, inspector.
  const headings = [
    page.getByRole("heading", { name: "Answer" }),
    page.getByRole("heading", { name: "Verification" }),
    page.getByRole("heading", { name: "Source inspector" }),
  ];
  await expect(headings[2]).toBeVisible();
  const positions = await Promise.all(
    headings.map(async (heading) => (await heading.boundingBox())?.y ?? Number.POSITIVE_INFINITY),
  );
  expect(positions).toEqual([...positions].sort((left, right) => left - right));

  // The record's provenance is part of the record now, at the foot of the sheet, rather
  // than a separate narrow-screen disclosure repeating the column beside the document.
  const record = page.getByRole("complementary", { name: "Record provenance" });
  await expect(record.getByRole("heading", { name: "About this record" })).toBeVisible();
  await expect(record.getByText("World Health Organization", { exact: true }).first()).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  await expectNoAxeViolations(page);
});
