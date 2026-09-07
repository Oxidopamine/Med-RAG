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
verification on the right. Above 1080px both columns are sticky panes that scroll within
the viewport rather than the page growing to hold them, because a claim and the passage it
rests on have to be readable against each other. Scroll chaining is left on so the page can
still tuck the composer away, and the panes are capped in `dvh` so mobile browser chrome
does not hide their last rows. Below 1080px the columns become `display: contents` and the
sticky positioning is explicitly reset - the grid track sticky was measured against no
longer exists.

Every panel that is a direct child of the workspace grid needs an explicit `order` in the
1080px block. A panel without one inherits `order: 0` and jumps ahead of everything that
has one, which is how uncited passages once rendered above the answer. The source inspector presents canonical passage text and
locator metadata supplied by the API; it is not a fabricated PDF page or a general PDF
renderer.

At 1080px and below, the result workspace becomes one column in this deliberate order:
Answer, Verification, Selected Evidence, Source Inspector, Context/Supporting Evidence,
then Conflict Review. At 720px and below, the audit timeline becomes vertical, source
metadata moves into a disclosure, controls become full-width where appropriate, and
the source-scope panel is kept in document flow. This order keeps the decision and its
gate outcome ahead of detailed provenance on touch and zoomed layouts.

The feature components in `components/evidence-workspace/` have focused roles:

- `app-header.tsx` renders product identity, the active release, and the prototype
  status. The release chip replaces a primary navigation that had one destination: which
  approved release a question will be checked against is the product's boundary, and it
  was previously discoverable only after a run.
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
  messages. Transient messages sit in a slot above the workspace grid whose height is
  reserved while a result is on screen, so an arriving or auto-dismissing message neither
  moves the result nor covers the source inspector.
- `context-dialog.tsx` provides the interpreted-context editor.

`use-evidence-run.ts` owns the asynchronous state machine, including SSE progress,
terminal-result loading, stale-run cancellation, and polling fallback.

In the source inspector, rank is the identity of every rail entry and the locator is
secondary text beneath it — three label vocabularies in a 66px column was a puzzle rather
than a wayfinder. `j` and `k` walk the ranking, bound to the rail rather than the document
so they cannot swallow a keystroke while someone is typing a question, and focus follows
the selection. The text-size choice persists in `localStorage`: it is an accessibility
preference, not a per-run whim.

Component styling is scoped through `workspace.module.css`; `app/globals.css` holds the
token layer, font inheritance, and element resets. Components reference the semantic
tokens only - `--bg-*`, `--text-*`, `--border-*`, `--ink-*`, `--accent-*`,
`--ok/--warn/--danger-*` - and no component stylesheet carries a literal colour.

**One light theme, declared.** `color-scheme: light` on `:root` so the browser paints its
own scrollbars, canvas and native selects in the scheme the stylesheet designed for. There
is no dark theme; the second palette was removed rather than left half-considered.

**The black action.** `--ink` (near-black) is the only high-contrast fill in the
interface, and exactly one control per surface may carry it: `Review evidence` on the
composer, `Run again` in the context editor. Everything else is a hairline-bordered
surface button. `--shadow-ink` carries an inset top highlight, which is what stops a flat
black rectangle reading as a disabled block. Blue is reserved for links and the anchor
viewer's status line; it is never used for emphasis.

**Colour carries meaning or is not used.** Green means a gate passed, amber means
something is withheld, red means it failed. Selection is black - a cursor, not a status -
so a selected claim, citation chip or rail item takes an ink border rather than a tint.

**Type.** Public Sans for the interface, JetBrains Mono for anything a reader compares: a
page number, an evidence ID, a run identifier, a manifest hash, a locator. The interface
face is a text cut rather than a display one: the largest tier by count is `--t-xs` (13px)
metadata, and a tightened cut serves that size worst. The scale floors at `--t-2xs`
(12px) and that tier is chrome - uppercase kickers, status pills, keycaps. Values a reader
acts on sit at `--t-xs` or above. Inside the source sheet, `em` is used only for the
passage and its immediate framing, because that is what the `Text size` control exists to
scale; nested metadata uses fixed tokens, since compounding `em` had put anchor
fields at 7.5px.

`--control-min` (44px) is the smallest an interactive control may be. `--control-sm`
(36px) is the compact pointer variant, and it resolves to `--control-min` below 860px, so
every control takes a full target on touch without a rule per control. The context editor
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
focus to the end of the inserted question, and never submit without user action. The
examples are the featured entries of the question bank in `lib/question-bank.ts` and are
drawn from what the active release can answer - testing services, PrEP, same-day
initiation, viral-load monitoring, advanced disease - because an example the corpus is
guaranteed to abstain on teaches the reader the product is broken rather than out of
scope.

The question bank itself is a disclosure beside the source control. It lists the full
bank grouped by clinical area, with a text search and an area filter, and marks areas
whose release is still planned so that an abstention there reads as expected rather than
as a fault. Choosing an entry fills the question field and closes the panel; it never
submits, because the reader may want to edit the population or the decision first, and
because a bank that fires reviews on click would make a mis-click cost a run. The panel
follows the source control's behaviour: a native `details` element, closed by Escape or
by a click outside it, with focus returned to its summary. Both panels close on the
outside click rather than on pointerdown: closing early shrinks the page while the
control under the pointer is still waiting for its click, and a submit pressed with a
panel open was lost that way.

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

### The identifier guard

`lib/identifiers.ts` scans the draft question, in the browser, for shapes that identify a
person: a name, a date of birth, a record number, an email, a phone number. Nothing it
finds leaves the page — the point is to stop the text being submitted, not to record that
it existed. Matches are marked in the warning so the reader can check the judgement rather
than trust it, with a one-click removal beside a way to overrule it.

It never blocks submission. The reader may legitimately be testing with synthetic text, and
a guard that cannot be overruled becomes one people route around. It is deliberately
conservative in the other direction too: a false positive costs a dismissal, a false
negative sends a name to a research pipeline, so `NOT_A_NAME` and the unit-aware
record-number rule exist to keep ordinary clinical prose — "viral load 1200 copies/mL",
"World Health Organization" — from tripping it. `lib/identifiers.test.ts` asserts both
halves, and the silence half is the one that matters.

### Source scope

The `Sources` disclosure states the release's coverage and controls the request's
`source_filters`:

- **No jurisdiction control.** Every record in the active WHO SMART HIV release is scoped
  `WORLD`, and `serving_pipeline._jurisdictions` widens any client selection to include
  `WORLD`, so a country filter could never narrow a result - it only claimed to. The
  request carries `jurisdictions: ["WORLD"]`.
- **The guideline bodies are listed with their status.** WHO is `Active`; CDC, US DHHS/NIH,
  EACS and BHIVA are marked `Coming soon`. None of them is selectable - availability
  follows an approved corpus release, not a preference. They are listed rather than hidden
  because the first thing a clinician asks of an evidence tool is what it has read, and an
  empty answer from a corpus whose boundary was never stated reads as a defect.
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

Each rendered claim shows the evidence roles its cited evidence carries. The completeness
gate decides whether a claim may be rendered by reading those roles, so showing them on the
claim is the difference between "supported" as a badge and "supported" as a statement a
reader can weigh — a claim resting only on `BACKGROUND` reads differently from one resting
on a current primary guideline. Roles are deduplicated across the claim's evidence and
ordered strongest first.

Cited evidence and uncited candidates render as **one ranking** with a labelled boundary
where citation stops, rather than two separately-numbered panels. The boundary is the
informative part: it says how far down the ranking the gate went before it stopped citing,
which two lists with independent numbering cannot express. Everything below it stays
dashed, amber, and captioned with the roles it carried.

Copying an answer includes its claim-linked source links and research-use notice. The
provenance strip carries **Copy link** — the run's address at `/r/[questionId]`, which a
reviewer can open, rather than an identifier they can only search for — and audit export,
which belongs with run identity rather than with the answer. The run identifier stays
visible for a support conversation. The strip is sticky under the header, because which
release produced the passage on screen should not require a scroll to recover.

## Run and verification semantics

`use-evidence-run.ts` owns submission, context replacement, ordered SSE progress,
polling fallback, retry, timeout, stop-monitoring, stale-run cancellation, and prior
result retention. Progress messages describe pipeline state only; clinical claims are
accepted only from a validated terminal result.

The verification audit is open on every state, answer-ready included: five named gates and
what each concluded is what distinguishes this from a general assistant, and collapsing it
by default on the one result where all of them passed was the interface being modest about
exactly that. Each stage reports its duration, derived from the gap between the previous
stage's last observed event and its own; the first stage reports none, because the queue is
not the stage.

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

## Focus, and failing without a blank page

A review can take minutes, so the result is not merely announced. The submit button is
marked with `aria-disabled` rather than `disabled` - the same reasoning as `readOnly` on
the busy textarea - so it keeps focus through the run instead of handing it back to
`<body>`; `handleSubmit` rejects an empty or in-flight submission. When the run reaches a
terminal state, focus moves to the answer heading, which carries `tabIndex={-1}` and a
suppressed focus ring for this. Only idle focus is taken over: focus on the submit button
that started the run, or dropped to `<body>`. A reader who has deliberately moved on -
typing a follow-up, reading a source - keeps their place.

`app/error.tsx` and `app/global-error.tsx` cover the one remaining way this interface can
go silent, a throw while rendering. Both carry the research-use notice and say plainly
that no clinical claim was rendered; the route boundary recovers the run identifier from
`/r/[questionId]` so the failure is reportable against a specific review. The asynchronous
paths are handled separately and already fail closed.

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
component interaction. `expectNoAxeViolations` asserts axe's `incomplete` bucket as well
as `violations`, with `color-contrast` allowlisted and its reason recorded at the helper:
axe cannot resolve a background it did not compute, and the header gradient and
translucent surfaces defeat it. Contrast for those nodes is measured directly instead.
Asserting only on `violations` is how four dropped ARIA labels stayed green through a
stylesheet rewrite that renamed one of them. Playwright exercises the complete mocked question workflow in
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
