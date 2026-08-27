# Frontend audit: engineering, design, and accessibility

Status: design lane resolved; engineering lane part-resolved (see "Resolved" below)
Audited: 2026-08-27, branch `feat/hiv-mvp-serving-path` at `c1b9e6c`
Last updated: 2026-08-27, after the design-token migration and the remediation pass
Scope: `apps/web` — the evidence workspace as it renders today, judged against the
standard the rest of this repository already sets
Audience: the frontend engineer and the UI/UX designer, split into lanes below

[frontend.md](frontend.md) states how the web application is meant to work. This document
states where it does not, and separates the findings by who can fix them. It is a
point-in-time record, not a spec: when a finding is resolved, delete it here and — where
the resolution changes intended behaviour — record the new behaviour in `frontend.md`
instead.

Every number below was **measured**, not inferred. Build, typecheck, lint and the unit
suite were run locally. Contrast ratios, font sizes, axe results and bundle sizes were
taken from a production build served at `127.0.0.1`. The reproduction steps are in the
last section so nobody has to take this document's word for any of it.

## The finding in one paragraph

The engineering underneath this application is disciplined in a way most projects are not.
The contract layer encodes cross-field invariants, the run hook handles async races
correctly, and the e2e suite asserts constraints — touch-target size, horizontal overflow
at 320px — that most suites never check. The interface those systems feed did not meet the
same standard, and now largely does: the type scale, the token layer, the contrast
failures, the dropped focus, the missing error boundary and the blind spot in the
accessibility gate are all closed and re-measured.

What remains is one decision the design owes (D4), and an engineering lane that is
maintainability rather than correctness: a 4,145-line stylesheet, one client island, two
derivations of the same value, index-keyed measurement rows, and an untuned polling
cadence. None of it is deep, and none of it is what a reader of this interface would
notice.

## Resolved

Recorded so the sequence below is not re-run against findings that are already closed.

| Finding | Resolution | Verified by |
| ------- | ---------- | ----------- |
| D1 type scale | 9px and 10px tiers gone; hard 12px floor including the source sheet's own `em` ratios. Text at ≤12px went from 88% of elements to 10%. | Computed `font-size` over every text-bearing element, three viewports |
| D2 contrast | All failures closed, in both themes. `.page-frame-number` moved off `--text-faint`, the rest cleared by the size migration. | Computed contrast against the resolved ancestor background |
| D3 tokens | 376 hardcoded hex values to 0; 955 token references; semantic layer over raw ramps. | `grep` over both component stylesheets |
| D4 dark mode | Full dark theme, plus `color-scheme` on `:root` and per-scheme `themeColor` in a `viewport` export, so the browser's own widgets follow. | Rendered and measured in both schemes |
| E1 axe gate | `expectNoAxeViolations` now asserts `incomplete` too, with `color-contrast` allowlisted and its reason written down. The four `aria-label`-on-`div` nodes have real roles. | axe reports 0 violations and 0 non-allowlisted incomplete in three states |
| E2 focus | Submit uses `aria-disabled` so it keeps focus through the run; terminal lifecycle moves focus to the answer heading, taking over only idle focus. | Unit test asserts focus is retained; browser check asserts it lands on `#answer-heading` |
| E3 error boundary | `app/error.tsx` and `app/global-error.tsx`, both carrying the research-use notice; the route boundary recovers the run ID from `/r/[questionId]`. | `next build` |
| E10 label in name | Example buttons' accessible name now leads with the visible topic. | Unit test |
| Touch targets | A `--control-min` token at 44px, applied across the workspace and the context editor. Mobile controls under 44×44 went from 20 to 0. | Measured at 390px |
| One primary per surface | The context editor's four solid "Add" buttons are now quiet outlines, leaving "Run again" as its only accent; a retry the reason code says cannot help drops the accent too. | 11 dialog buttons, 1 solid accent |

## What holds up

Recorded so that a later refactor does not quietly discard it.

- **`lib/contracts.ts`.** Strict schemas with real cross-field invariants — an answer-ready
  result must carry a release, at least one claim, and evidence details that exactly cover
  its cited and candidate IDs. Malformed payloads die before a component sees them. This is
  the strongest file in the web application.
- **`use-evidence-run.ts`.** A generation counter invalidates stale work, SSE sequence
  numbers dedup, the stream degrades to polling, and unmount tears everything down. This is
  the hardest thing in the app to get right and it is right.
- **`lib/evidence-presentation.ts`.** 818 lines of pure functions over validated payloads,
  with 32 tests. Licence policy, anchor precision and citation numbering are each decided in
  exactly one place.
- **The e2e suite.** axe passes at three viewports, plus assertions on touch-target height,
  question font size, source-panel order at 390px, and horizontal overflow at 320px.
- **Reading measure.** Claim text, quotations and passages are capped at 75–82`ch`. Someone
  deliberately protected line length — which is what makes the type-size finding surprising
  rather than merely careless.
- **`readOnly` over `disabled`** on the busy textarea, so focus is not dropped. Exactly the
  right call. It simply was not applied to the submit button beside it (see E2).

Gate status at time of audit: build passes in 4.6s, `tsc --noEmit` reports zero errors,
`eslint .` reports zero problems, and 116 of 116 unit tests pass across 12 files.

---

# Lane one — design

One finding left, and it is a set of decisions rather than defects.

## D4. Design decisions with no defined behaviour

- **No `forced-colors` handling.** Windows High Contrast mode is undefined for this
  interface, which matters more than usual for a clinical tool on managed hospital desktops.
- **No favicon.** The `viewport` export now carries `colorScheme` and per-scheme
  `themeColor`; the icon is still missing.
- **The page `h1` is a form prompt**, not the page's identity: "What guideline decision are
  you reviewing?" in `question-composer.tsx`. A screen reader user jumping to the first
  heading lands on a question rather than on what this application is.
- **`SourceMetadata` is rendered twice** and one copy is `display: none`'d per breakpoint
  (`source-verification-column.tsx:248` and `:253`). Duplicated DOM for a purely responsive
  concern; worth deciding whether the two presentations really need to differ.

---

# Lane two — engineering

## E4. One client island, one chunk, ~245 KB gzip first load

`app/page.tsx` renders a `"use client"` root, so all fifteen components ship to the browser
whether or not they need to.

| Chunk group | gzip | Contents |
| ----------- | ---- | -------- |
| `rootMainFiles` | ~130 KB | React 19 + Next 16 runtime |
| page chunk | **~115 KB** | zod, Radix Dialog, Lucide icons, the entire workspace |
| polyfills | ~39 KB | legacy browsers only |

Total emitted JS is 1.0 MB raw / 285 KB gzip for a single route.

The cheapest win is `ContextDialog`: it is statically imported at
`evidence-workspace.tsx:7` but only reachable behind an "Edit context" click, and it drags in
Radix Dialog plus `clinicalContextSchema`. A `next/dynamic` boundary costs one line.

Beyond that, `AppHeader`, `GettingStarted`, `ProvenanceStrip` and `SourceAnchorList` are pure
presentational and could render on the server. `frontend.md` already records the intent that
"the page and layout remain server components; the client boundary begins at
`components/evidence-workspace.tsx`" — the boundary is simply higher than it needs to be.

**Fix.** Lazy-load `ContextDialog`; push `"use client"` down from the page root to the
components that actually hold state; add a bundle budget to CI so the number cannot drift
back.

## E5. `workspace.module.css` is 4,145 lines in one module

The duplicate-declaration pass is done: the six selectors that were genuinely declared
twice - `.composer-shell`, `.composer-run-note`, `.feedback-dismiss`, `.no-conflict`,
`.secondary-button`, `.toolbar-center` - are folded, verified behaviour-neutral by a
computed-style diff over 639 elements at three viewports in both themes. The two that
remain (`.paper-footer`, `.source-column`) are a grouped base rule plus a member-specific
override, which is correct structure rather than duplication.

What is still open is the file size. 4,145 lines in one module is why the duplication
happened, and it will happen again. Splitting it by component is the fix; it is deferred
because it is a large mechanical diff with real regression surface and no user-visible
return, and it wants a reviewer with time rather than the end of a session.

**A note on how to do it safely.** The merge above changed rendering twice before it was
right, both times by treating a member of a grouped selector as a standalone rule. The
computed-style snapshot caught both. Take the same snapshot before splitting this file:
`scripts` are not checked in for it, but it is thirty lines of Playwright reading
`getComputedStyle` for every element and diffing before against after.

## E6. Derived state computed twice, and nothing memoized below the root

`EvidenceWorkspace` memoizes `buildCitations(result)` at line 93 and passes it into
`EvidenceDetails`. `AnswerPanel` then calls `buildCitations(result)` again, unmemoized, at
`answer-panel.tsx:41`. Same pattern for `selectedClaimEvidence`: memoized at
`evidence-workspace.tsx:81`, recomputed independently at `evidence-details.tsx:55`. Two
derivations of the same value that must agree by convention rather than by construction.

Underneath that, no component below the root uses `memo`, `useMemo` or `useCallback`, so
every SSE progress event re-renders the entire tree. Each of those renders:

- constructs **six `new Intl.DateTimeFormat()` instances** from helper functions called in the
  render path — `anchor-viewer.tsx:415`, `evidence-details.tsx:396`, `provenance-strip.tsx:67`
  and `:71`, `source-verification-column.tsx:525` and `:533`;
- copies `progressEvents` five times via `[...progressEvents].reverse().find(...)` inside a
  five-iteration map at `source-verification-column.tsx:371`.

**Fix.** Pass the memoized `citations` into `AnswerPanel` rather than recomputing. Hoist the
four near-identical private `formatDate` helpers into one module with module-level `Intl`
instances. Replace reverse-and-find with `findLast`.

## E7. Measurement rows keyed by index across three parallel arrays

`ContextDialog` holds one list in three index-aligned places — `context.measurements`,
`measurementConcepts` and `measurementValues` — and renders the rows with `key={index}` at
`context-dialog.tsx:655`. Removing a middle row shifts every index at once. The data stays
consistent because all three arrays are filtered together, but DOM identity, focus, in-flight
IME composition and the index-derived `aria-describedby` IDs all follow the wrong row.

Separately, `clinicalContextSchema.safeParse()` and `changeSummary()` run unmemoized on every
keystroke, directly below a `useMemo` that already computes their input.

**Fix.** Give each measurement a stable client-side ID and key on that. Collapse the three
arrays into one list of row objects holding the raw input strings. Wrap the validation in a
`useMemo` on `candidateContext`.

## E8. Polling has no backoff and no visibility awareness

When the progress stream is unavailable, `pollUntilTerminal` polls every 1.5s for up to five
minutes — **up to 200 requests per run**, at full rate whether or not the tab is visible. The
fallback itself is well built; only the cadence is untuned.

**Fix.** Back off from 1.5s toward roughly 10s, and pause or slow while
`document.visibilityState` is `"hidden"`.

## E9. WebKit is tested nowhere, and the config says otherwise

`playwright.config.ts` comments out the WebKit project with "Safari coverage is provided by CI
on macOS". `.github/workflows/ci.yml` has one web job and it runs on `ubuntu-latest`. There is
no macOS job. Safari is untested, and the comment reads as though it were covered — which is
worse than an acknowledged gap.

**Fix.** Either add the macOS job the comment promises, or correct the comment. The second is a
legitimate choice; the stale claim is the problem.

## E10. Smaller engineering items

| Location | Finding |
| -------- | ------- |
| `question-composer.tsx` | Parallel arrays: `EXAMPLE_QUESTIONS` and `EXAMPLE_LABELS` are still index-coupled. The Label-in-Name half of this finding is resolved — the accessible name now leads with the visible topic. |
| `question-composer.tsx:94` | `focusAfterQuestionChangeRef` never clears when the same example is picked twice, because the effect does not run on an unchanged value. The stale flag then yanks the caret to the end during a later mid-text edit. |
| `context-dialog.tsx:775` | `<ArrowRight aria-label="changed to" />` — an SVG carrying a label without `role="img"`, so the label is not reliably exposed. |
| `answer-panel.tsx:115,123,130` | Three static badges carry `role="status"`. They never change, so this creates live regions that announce on unrelated re-renders. |
| 4 components | Four private `formatDate` helpers with near-identical bodies, in `anchor-viewer`, `evidence-details`, `provenance-strip` and `source-verification-column`. |
| `eslint.config.mjs:8` | `globalIgnores` hardcodes `.next/**` and `.next-e2e/**` while `next.config.ts` accepts any `NEXT_DIST_DIR`. Any other value produces a build directory ESLint then lints — encountered within a minute of trying one during this audit. |
| `app/globals.css:29` | `overflow-x: hidden` on `html, body` hides layout overflow rather than preventing it, masking the exact condition the 320px e2e assertion exists to catch. |
| `lib/api.ts:18` | The API is a separate origin with no `preconnect`, so the first readiness call pays full connection setup. |
| CI | No bundle budget, no coverage thresholds, no Core Web Vitals gate. `evidence-workspace.tsx` — 504 lines including `copyAnswer`, `exportAudit` and `friendlyError` — has no unit test. |

---

# Suggested sequence

The correctness and design work is done. What is left is maintainability, and it is
ordered by how much it reduces the chance of the next regression rather than by severity.

1. **Split `workspace.module.css` (E5).** The largest remaining item and the one that
   caused the duplication in the first place. Snapshot computed styles before and after —
   the merge that closed the duplicate declarations changed rendering twice before it was
   right, and only the snapshot caught it.
2. **Single derivation of citations and claim evidence (E6).** Two components deriving the
   same value independently is the wrong coupling in a citation UI, regardless of cost.
3. **Stable keys for measurement rows (E7).** A real defect today: removing a middle row
   sends focus and `aria-describedby` to the wrong row.
4. **The E10 items.** Each is small; the `eslint.config.mjs` and `overflow-x: hidden` ones
   both mask other problems and are worth doing before the rest.
5. **Decide D4 and E9.** Neither is a fix; both need someone to choose.

**Deliberately deferred.** E4 (bundle splitting) and E8 (polling backoff) are recorded and
not scheduled. 245 KB gzip on a single-route prototype with no external users does not
justify pushing the client boundary through every component in the tree, and 200 requests
over five minutes against a local API on the SSE-failure path is not yet a cost anyone
pays. Take the one-line `ContextDialog` lazy-load and the CI bundle budget so the number
cannot drift; leave the rest until there is a deployment whose connection you know
something about.

# Reproducing the measurements

The three measurements this document leans on can each be re-derived. None requires the API to
be running; all responses are mocked at the network layer.

**Bundle sizes.** Build with a scratch dist directory, then read `build-manifest.json` for the
`rootMainFiles` list and gzip each chunk:

```bash
NEXT_DIST_DIR=.next-audit npm run build:web
cd apps/web/.next-audit/static/chunks
for f in *.js; do echo "$f raw=$(stat -c %s "$f") gzip=$(gzip -c "$f" | wc -c)"; done
```

`next build` rewrites `tsconfig.json` and `next-env.d.ts` as a side effect — revert both
afterwards, and delete the scratch dist directory, or ESLint will lint it (see E10).

**Font sizes and contrast.** Add a temporary Playwright spec that mocks `/health/ready` and
the question endpoints, drives the app to the answer-ready state, then walks
`document.querySelectorAll("*")`, keeps elements with a non-empty direct text node, and for
each reads `getComputedStyle` plus the nearest non-transparent ancestor background. Compute the
WCAG ratio from relative luminance and compare against 4.5 (or 3.0 for text at 24px, or 18.66px
at weight ≥700).

**axe `incomplete`.** In the same spec, call `new AxeBuilder({ page }).analyze()` and log
`result.incomplete` alongside `result.violations`, printing `node.target` and the `message` from
each of `node.any`, `node.all` and `node.none`. This is the check E1 asks to be made permanent —
at which point it stops being a manual step.
