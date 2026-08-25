import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { QuestionResult } from "../lib/types";

const API_URL = "http://localhost:8000";
const QUESTION =
  "For an older adult with atrial fibrillation and renal impairment, what do current guidelines recommend about anticoagulation?";

const context = {
  age: 74,
  sex: null,
  conditions: ["ATRIAL_FIBRILLATION"],
  known_absent_conditions: [],
  measurements: [
    {
      concept: "EGFR",
      value: 28,
      unit: "mL/min/1.73m2",
      provenance: "USER_TEXT_EXPLICIT",
    },
  ],
  special_populations: ["RENAL_IMPAIRMENT"],
  known_absent_special_populations: [],
  care_setting: null,
  jurisdiction: null,
  question_type: "treatment_guideline",
  topic: "anticoagulation",
};

const abstainedResult = {
  question_id: "question-abstained",
  question: QUESTION,
  status: "ABSTAINED",
  corpus_release: null,
  interpreted_context: context,
  claims: [],
  evidence_details: [],
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
  },
  interpreted_context: context,
  claims: [
    {
      claim_id: "claim-anticoagulation",
      text: "Reduced renal function should be considered when selecting and dosing anticoagulant therapy.",
      evidence_ids: ["evidence-renderable", "evidence-restricted"],
      verification_status: "SUPPORTED",
    },
    {
      claim_id: "claim-monitoring",
      text: "Renal function should be reviewed as clinical status changes.",
      evidence_ids: ["evidence-restricted"],
      verification_status: "SUPPORTED",
    },
  ],
  evidence_details: [
    {
      evidence_id: "evidence-renderable",
      exact_text:
        "Reduced renal function should be considered when selecting and dosing anticoagulant therapy.",
      evidence_type: "GUIDELINE_RECOMMENDATION",
      evidence_roles: ["CURRENT_PRIMARY_GUIDELINE", "POPULATION_APPLICABILITY"],
      section_path: ["Anticoagulation", "Renal impairment"],
      source_id: "source-acc-aha",
      source_version_id: "source-acc-aha-2026",
      source_title: "Guideline for the Management of Atrial Fibrillation",
      source_version_label: "2026 edition",
      publisher_name: "ACC/AHA",
      source_url: "https://example.test/guideline",
      source_class: "E1",
      jurisdiction: "US",
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
      section_path: ["Anticoagulation", "Renal dosing"],
      source_id: "source-renal",
      source_version_id: "source-renal-2026",
      source_title: "Renal Dosing Addendum",
      source_version_label: "August 2026",
      publisher_name: "Guideline Committee",
      source_url: "https://example.test/renal-addendum",
      source_class: "E1",
      jurisdiction: "UK",
      language: "en",
      lifecycle_status: "CURRENT",
      effective_from: "2026-08-01",
      effective_to: null,
      approval_status: "APPROVED",
      render_allowed: false,
      locators: [
        {
          kind: "SECTION",
          source_uri: "https://example.test/renal-addendum",
          pdf_page: 49,
          printed_page: "233",
          bbox: null,
          exact_highlight_available: false,
        },
      ],
    },
  ],
  conflicts: [
    {
      organization: "Regional formulary",
      recommendation: "Use additional renal-function monitoring.",
      rationale: "Local dosing policy is more conservative.",
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

async function expectNoAxeViolations(page: Page) {
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
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

  await page.locator("summary").filter({ hasText: "Sources" }).click();
  await page.getByRole("checkbox", { name: "US, United States" }).uncheck();
  await page.getByRole("checkbox", { name: "EU, European Union" }).uncheck();
  await page.getByRole("checkbox", { name: "UK, United Kingdom" }).click();
  await expect(page.getByRole("checkbox", { name: "UK, United Kingdom" })).toBeChecked();
  await expect(
    page.getByText("Keep at least one jurisdiction selected.", {
      exact: true,
    }),
  ).toBeVisible();

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

  await page.getByText("Why was this withheld?", { exact: true }).click();
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
  await page.getByRole("checkbox", { name: "EU, European Union" }).uncheck();
  await page.getByRole("checkbox", { name: "UK, United Kingdom" }).uncheck();
  await page.getByRole("textbox", { name: "Organizations" }).fill("ACC, AHA, ACC");
  await page.getByRole("textbox", { name: "Organizations" }).blur();
  await ask(page);

  await expect(page.getByText("Evidence review ready", { exact: true })).toBeVisible();
  await expect(page.getByText("Evidence-gated result", { exact: true })).toBeVisible();
  await expect(page.getByText("Approved corpus release", { exact: true })).toBeVisible();
  await expect(page.getByText("guidelines-2026-08", { exact: false }).first()).toBeVisible();
  expect(run.submittedBodies).toEqual([
    expect.objectContaining({
      question: QUESTION,
      source_filters: {
        jurisdictions: ["US"],
        organizations: ["ACC", "AHA"],
      },
    }),
  ]);

  await expect(
    page.getByRole("heading", { name: "Exact guideline quotation" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Source inspector" })).toBeVisible();
  await expect(page.locator("mark")).toHaveText(
    "Reduced renal function should be considered when selecting and dosing anticoagulant therapy.",
  );
  await expect(page.getByText("Evidence 1 of 2", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open publisher source" })).toHaveAttribute(
    "href",
    "https://example.test/guideline",
  );

  await page.getByRole("button", { name: "Next evidence reference" }).click();
  await expect(page.getByText("Evidence 2 of 2", { exact: true })).toBeVisible();
  await expect(page.getByText("Exact text is not licensed for display")).toBeVisible();
  await expect(page.getByText("Source text cannot be displayed")).toBeVisible();
  await expect(page.getByText("Renal Dosing Addendum", { exact: true }).first()).toBeVisible();

  const secondClaim = page
    .getByRole("listitem")
    .filter({ hasText: "Renal function should be reviewed as clinical status changes." });
  await secondClaim.getByRole("button", { name: "1 source" }).click();
  await expect(secondClaim).toHaveClass(/selected-claim/);
  await expect(page.getByText("1 for review", { exact: true })).toBeVisible();
  await expect(page.getByText("Local dosing policy is more conservative.")).toBeVisible();

  const auditDetails = page.locator("details").filter({ hasText: "View audit steps" });
  await expect(auditDetails).not.toHaveAttribute("open", "");
  await auditDetails.locator("summary").click();
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

  const headings = [
    page.getByRole("heading", { name: "Answer" }),
    page.getByRole("heading", { name: "Verification" }),
    page.getByRole("heading", { name: "Exact guideline quotation" }),
    page.getByRole("heading", { name: "Source inspector" }),
  ];
  await expect(headings[3]).toBeVisible();
  const positions = await Promise.all(
    headings.map(async (heading) => (await heading.boundingBox())?.y ?? Number.POSITIVE_INFINITY),
  );
  expect(positions).toEqual([...positions].sort((left, right) => left - right));

  await page.getByText("Source details", { exact: true }).click();
  const mobileMetadata = page.locator("details").filter({ hasText: "Source details" });
  await expect(mobileMetadata.getByText("ACC/AHA", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  await expectNoAxeViolations(page);
});
