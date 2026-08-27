# Frontend development

The web application lives in `apps/web` and uses Next.js 16 App Router, React 19,
and strict TypeScript. The application remains a client-driven evidence workspace
because its primary workflow depends on local interaction state and Server-Sent
Events. The page and layout remain server components; the client boundary begins at
`components/evidence-workspace.tsx`.

## Experience model

`EvidenceWorkspace` coordinates a progressive evidence-review workflow. It does not
show an empty dashboard or decorative document viewer before the API has returned
verified source data:

1. The initial view shows the question composer, example questions, research-use/PHI
   guidance, and the readiness state of the approved corpus.
2. A running review shows one progress card and the live verification audit. The user
   can stop browser monitoring without implying that the server job was cancelled.
3. An `ANSWER_READY` result reveals the provenance strip, rendered claims,
   claim-linked evidence, source inspector, interpreted context, conflict review, and
   completed verification summary.
4. An `ABSTAINED` or `FAILED` result shows the actionable reason, interpreted context,
   provenance, and verification outcome. It does not render claim, evidence, or source
   inspection UI.

Starting another question or rerunning an edited context moves the current terminal
result to `previousResult`. That result remains visible and is explicitly labelled as
the previous answer while the replacement runs or if its request fails. Stale SSE and
polling responses are generation-checked and cannot overwrite the active run.

## UI structure

The desktop interface uses a full-width composer followed by a two-column audit
workspace: the result and evidence panels on the left, and source inspection plus
verification on the right. The source inspector presents canonical passage text and
locator metadata supplied by the API; it is not a fabricated PDF page or a general PDF
renderer.

At 1080px and below, the result workspace becomes one column in this deliberate order:
Answer, Verification, Selected Evidence, Source Inspector, Context/Supporting Evidence,
then Conflict Review. At 720px and below, the audit timeline becomes vertical, source
metadata moves into a disclosure, controls become full-width where appropriate, and
the source-scope panel is kept in document flow. This order keeps the decision and its
gate outcome ahead of detailed provenance on touch and zoomed layouts.

The feature components in `components/evidence-workspace/` have focused roles:

- `app-header.tsx` renders product identity, primary navigation, and the prototype
  status.
- `question-composer.tsx` owns question entry and submission controls.
- `getting-started.tsx` explains corpus readiness and the fail-closed starting state.
- `run-progress.tsx` presents the current pipeline stage without exposing claim text in
  progress events.
- `answer-panel.tsx` renders answer-ready, abstained, failed, and preserved-prior
  terminal results.
- `evidence-details.tsx` renders licensed exact evidence or provenance, interpreted
  context, claim-linked supporting evidence, and conflict status.
- `source-verification-column.tsx` renders claim-linked source inspection and the
  five-step verification audit.
- `provenance-strip.tsx` exposes the pinned corpus release, manifest reference, and run
  identifier.
- `feedback-banner.tsx` provides calm inline status, success, warning, and error
  messages.
- `context-dialog.tsx` provides the interpreted-context editor.

`use-evidence-run.ts` owns the asynchronous state machine, including SSE progress,
terminal-result loading, stale-run cancellation, and polling fallback.

Component styling is scoped through `workspace.module.css`. Keep only application-wide
tokens, font inheritance, and element resets in `app/globals.css`. The context editor
uses Radix Dialog for dialog semantics, focus containment and restoration, and Escape
key handling. New interactive primitives should provide the same keyboard and screen
reader behavior.

The design never fills unavailable evidence fields with demonstration content. A
quotation, document title, page, highlight, verification check, or conflict outcome is
shown only when it is present in a validated API result. State-specific panels stay
collapsed until they are meaningful, and missing fields that belong in a visible
result are labelled as unavailable or not supplied.

## Question entry and source scope

The entry point is a compact evidence-query console, not a generic chat box. Its
heading names the task, visible Population/Condition/Decision cues teach the expected
question structure, and the primary action is explicitly labelled `Review evidence`.
The auto-growing textarea has a 4,000-character limit and grows from 60px to 144px
before scrolling. `Ctrl+Enter` or `Command+Enter` submits; ordinary Enter remains
available for multiline questions. Compact topic examples fill the field, return
focus to the end of the inserted question, and never submit without user action.

The composer follows this quality bar:

- One obvious primary action and no chat-like controls that do not belong to evidence
  review.
- A focused clinical question can be entered quickly, while the expected population,
  condition, and decision are explained without relying on placeholder text.
- Active source scope, research-use boundaries, and the no-identifiers reminder remain
  visible without competing with question entry.
- Submission has immediate duplicate protection. The question and scope are locked to
  the active review, the primary-action slot remains stable, and stopping browser
  monitoring is a separate quiet action.
- Validation is inline, specific, and focus-directed. It preserves the user's input and
  does not use interruptive dialogs or routine success toasts.
- Keyboard operation, visible focus, 44px touch targets, 16px mobile form text, zoom,
  reflow, and a 320px viewport are acceptance requirements.

The `Sources` disclosure controls the request's `source_filters` independently of the
clinical context inferred from the question:

- At least one of US, EU, or UK must be selected. All three are selected by default.
- The final selected jurisdiction cannot be removed accidentally; the control keeps it
  selected and explains what is required.
- Organization filters are optional tokens. Enter, comma, blur, or `Add` commits a
  token; whitespace is trimmed and matching is deduplicated case-insensitively.
- `Reset` restores all jurisdictions and clears organizations. `Done`, Escape, or
  clicking away closes the disclosure; a successful submission closes it as well.
- The exact scoped request is retained for retry.

These filters narrow the requested search; they never approve a source, expand the
active release, or override backend applicability and verification policy. The corpus
readiness check is explanatory preflight information. The terminal API result remains
authoritative if readiness changes between page load and submission.

## Result and evidence semantics

An answer-ready payload must include an active `corpus_release`, at least one rendered
claim, and `evidence_details` that exactly cover every evidence ID referenced by those
claims or listed in `retrieval_candidates`. The strict runtime contract rejects
duplicate, unrelated, incomplete, malformed, or internally contradictory evidence
details before they reach component state.

Each `EvidenceDetail` carries canonical source/version identity, source title,
publisher, jurisdiction, lifecycle and effective dates, evidence roles, optional
evidence type/section path, and one or more source locators. Selecting a claim limits
the supporting-evidence list to that claim's evidence IDs; selecting a source updates
the quotation, metadata, location, and highlight status together. A selected candidate
survives a claim change so that reading down the ranking is not interrupted.

`retrieval_candidates` is the only route by which a retrieved passage no claim cited
reaches the interface. Each entry names an evidence ID and the rank it held in
retrieval, so ranks are sparse - the cited passages are the gaps. The contract keeps
this door narrow: a candidate may not repeat a cited evidence ID, candidates must be
listed in ascending unique rank order, every candidate needs canonical evidence detail,
and only an `ANSWER_READY` result may carry them. An abstained or failed result exposes
nothing, candidates included.

The UI never lets a candidate read as support. The source inspector appends candidates
after the selected claim's cited evidence, so the next/previous controls walk the whole
ranking, but a candidate is marked "Retrieved, not cited" in the title bar, carries its
rank and a notice on the page itself, and is listed separately under "Other ranked
passages" rather than among supporting evidence. Copying an answer and the claim-linked
citation trail stay limited to cited evidence.

Licensing is part of the render contract:

- `render_allowed: true` may include `exact_text` and an exact locator highlight.
- `render_allowed: false` must include `exact_text: null` and cannot claim an exact
  highlight. The UI shows verified metadata, a licensing explanation, and a publisher
  link instead of reconstructing or paraphrasing the passage.
- Missing evidence type, section hierarchy, page, printed page, dates, or highlight
  remains visibly unavailable. Evidence roles are not relabelled as evidence types.

Copying an answer includes its claim-linked source links and research-use notice.
Audit export downloads the complete validated terminal result and export timestamp as
JSON; it does not imply clinical approval.

## Run and verification semantics

`use-evidence-run.ts` owns submission, context replacement, ordered SSE progress,
polling fallback, retry, timeout, stop-monitoring, stale-run cancellation, and prior
result retention. Progress messages describe pipeline state only; clinical claims are
accepted only from a validated terminal result.

The verification audit represents five observable stages: clinical context, evidence
retrieval/ranking, counter-evidence search, evidence completeness, and the final answer
gate. A step can be:

- `complete` only when its progress status was observed, or when an answer-ready final
  gate is returned;
- `current` while its status is active;
- `blocked` when the final gate abstains or fails;
- `skipped` when a run ends before that status was observed; or
- `pending` before it begins.

Timestamps appear only for observed events. "Automated checks passed" means the
configured source and evidence gates passed for the rendered claims. Abstention, failure,
monitoring timeout, and user stop are distinct states and are never presented as successful
completion.

## Feedback and error handling

Feedback stays inline near the composer instead of using stacked or interruptive toast
notifications. The UI reserves messages for meaningful outcomes:

- Field validation is shown beside the question or context field that needs attention.
- Completion, copy, export, and accepted context updates use polite, dismissible status
  messages that disappear after roughly three to six seconds.
- Errors remain until dismissed or resolved, explain that no unsafe claim was shown,
  and provide `Try again` only when the last request is retryable.
- Raw service text is kept in a collapsed `Technical details` disclosure; the primary
  message uses actionable language for connection, contract, timeout, and generic
  failures.

Non-error feedback uses `role="status"` with a polite live region. Errors use
`role="alert"`; they are not repeatedly re-announced during normal progress. Context
editing validates contradictions and numeric fields inline, previews before/after
changes, and submits only a valid context. A successful context submission closes the
dialog and leaves the previous result visible while the new review runs.

## API contracts

FastAPI is the source of truth for HTTP request and response shapes. The following
checked-in files are generated and must not be edited directly:

- `apps/web/openapi.json`
- `apps/web/lib/generated/api-schema.d.ts`

Activate the Python environment and regenerate both files after changing an API
schema or route:

```powershell
.\.venv\Scripts\Activate.ps1
npm run generate:api-types
```

On Unix-like systems, activate `.venv/bin/activate` instead. CI regenerates these
artifacts and fails when the checked-in contract has drifted.

The generated OpenAPI paths type every HTTP call made through `openapi-fetch`.
TypeScript alone cannot validate network data at runtime, so `lib/contracts.ts` also
uses strict Zod schemas for accepted-question, terminal-result, clinical-context, and
SSE progress payloads. Invalid or internally contradictory data is rejected before it
can enter React state.

## Testing and quality checks

Run the normal frontend checks from the repository root:

```powershell
npm run lint:web
npm run typecheck:web
npm run test:web
npm run build:web
```

Vitest and React Testing Library cover presentation helpers, runtime contracts, and
component interaction. Playwright exercises the complete mocked question workflow in
Chromium, Firefox, and WebKit, including an axe-core accessibility scan:

```powershell
npx playwright install
npm run test:e2e:web
```

Playwright builds into `.next-e2e` and serves the test application on port 3127 so it
does not interfere with an active local Next.js development server.

## Dependency policy

The current interface is small enough that it does not need Redux or another global
client-state store. The SSE workflow is intentionally represented by a focused custom
hook. Add TanStack Query only when shared server state such as saved searches,
pagination, or cross-page result caching appears.

The visual system remains plain CSS rather than Tailwind. Prefer CSS Modules, the
shared color tokens in `app/globals.css`, and Lucide icons for new work. Preserve the
current restrained navy, royal-blue, green, and amber language and the dense
evidence-review layout. Revisit a broader component system only if the product
develops enough repeated interactive patterns to justify the migration.
