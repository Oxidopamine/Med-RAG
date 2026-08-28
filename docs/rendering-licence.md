# Rendering licence decision

Status: **DECIDED 2026-08-27 — branch A, with C to be filed in parallel**
Raised: 2026-08-27
Was blocking: priority item 5 (evidence cards and exact highlighting); unblocked for
locators and excerpts, still blocked for page images pending the branch-C request

The roadmap records this as "CC BY-NC-SA 3.0 IGO plausibly permits attributed
non-commercial display, but this is a decision to be made and recorded, not a code
change." That framing treats it as one binary. It is three separate acts with three
different answers, and only the third is actually blocked.

This is not legal advice. It is the decision laid out so it can be taken in one sitting,
with the parts that need external confirmation named.

## What is actually being decided

`render_allowed: false` on every WHO asset currently gates all three of these together:

| # | Rendering act | What it reproduces | Status |
|---|---|---|---|
| 1 | **The locator** — "Annex A, `HIV.D` worksheet, row 145, column E" | Nothing. A coordinate. | Not a copyright act. Shipped in `33dec94`. |
| 2 | **The passage** — `content_exact`, or the presentation rendering of it | A short excerpt of the work | Licence question, and a narrow one |
| 3 | **The page** — a PDF page image with the anchor region highlighted | A full page of the work | The genuinely blocked one |

Item 1 needs no decision and is already served. Item 2 is the ordinary excerpt case. Item
3 is where the difficulty is, and it is worse for this corpus than for a narrative one.

## What the licence says

From the CC BY-NC-SA 3.0 IGO legal code:

- **§3** grants the right to "Reproduce, Distribute and Publicly Perform the Work" and to
  create adaptations, subject to the restrictions below.
- **§4(c)** — NonCommercial. The licence does not define the term. It restricts exercise
  of the rights "in any manner that is primarily intended for or directed toward
  commercial advantage or private monetary compensation."
- **§4(d)** — Attribution. Keep intact all copyright notices, provide the attributions the
  Licensor indicates, the title of the work, and to the extent reasonably practicable the
  URI.
- **§4(b)** — ShareAlike. Adaptations may be distributed only under this licence, a later
  version with the same elements, or a compatible Creative Commons licence.
- **§8(g)–(h)** — IGO specifics. Disputes go to non-binding mediation, then UNCITRAL
  arbitration at the licensor's headquarters; IGO immunities from legal process may apply.

## What WHO's own policy adds, and why it changes the answer

WHO's publishing copyright policy layers requirements on top of the CC licence. Two
matter here.

**The carve-out.** WHO's policy treats "figures, tables, maps, photos" as *requiring
explicit permission from WHO* rather than being covered by the open-access licence. For a
narrative guideline that carve-out affects a handful of exhibits. **For this corpus it is
close to the whole corpus.** The release is 5,145 approved records that are almost
entirely spreadsheet rows, decision-table rows, and data-dictionary entries; the Annex A,
B and C sheets are tables by construction. A highlighted region of one of those pages is
a reproduction of a table, which is precisely the carved-out category.

So the roadmap's working assumption — that attributed non-commercial display is plausibly
permitted — reads as **true for item 2 and doubtful for item 3**, and the corpus's tabular
nature is what separates them. This needs confirming with WHO permissions directly; it
should not be decided from a reading of the policy page alone.

**The adaptation disclaimer.** WHO requires adaptations to carry wording to the effect
that the adaptation was not created by WHO and that WHO is not responsible for its content
or accuracy, alongside the original's title, year, and licence. Whether an evidence card
presenting a retrieved passage beside generated text is an *adaptation* or a *quotation*
is the question that decides whether ShareAlike (§4(b)) attaches to the product's output.

## The coupling nobody has written down

**NonCommercial is a constraint on the business model, not on the renderer.** Deciding
"yes, render under NC" today is only durable while the product stays non-commercial. WHO
handles commercial reuse through a separate permissions process. So a future commercial
version needs its own WHO permission regardless of what is decided here, and the decision
below should be recorded as conditional on the research-only framing rather than as
settled for all time.

This is an argument for taking the decision now rather than deferring it: it is cheap
while the answer is "research-only, non-commercial", and it gets more expensive to
untangle the later it is taken.

## Branches

**A. Render items 1 and 2; hold item 3 pending WHO permission.**
Passage text and locators are served with full attribution; the page image stays behind
`render_allowed: false`. `AnchorViewer` already renders that state honestly — a marked
region over an empty frame, which is the correct rendering of "we know exactly where this
is and may not show it to you." Unblocks most of priority item 5 immediately, at the cost
of the anchor chain not yet terminating in a picture, which is MVP done-item 3.

**B. Scope the MVP explicitly to restricted rendering.**
Take no licence position at all; ship items 1 and 2 as a stated product limitation and
move done-item 3 out of the MVP. Costs nothing, decides nothing, and leaves the finished
viewer work serving only the empty-frame state.

**C. Seek WHO permission for page rendering.**
Submit the permissions request for reproducing table and figure content from the 2021
consolidated guidelines and the SMART HIV DAK. Slow, external, and the only branch that
actually reaches done-item 3.

## Recommendation

**A now, C in parallel, and do not wait on C to proceed.**

A is the only branch that converts finished work into shipped behaviour this week, and it
is the branch with the least licence exposure: short attributed excerpts for
non-commercial research use is the case the licence most clearly covers. C is the only
path to done-item 3 and has a long lead time, so starting it now costs nothing and
removes it from the critical path later. B is A without the upside and should be chosen
only if item 2 also turns out to be contested.

Note that A changes what MVP done-item 3 means: "every rendered claim resolves to a
passage a reader can locate" becomes locatable by address and quotable by excerpt, but
not yet visible as a page. That is a narrower claim and it should be written into the MVP
definition as such rather than silently satisfied.

## Decision

```
Branch chosen:      A now, C in parallel
Decided by:         Oxidopamine (project owner)
Date:               2026-08-27
Conditional on:     research-only, non-commercial framing
Revisit if:         the product takes on any commercial character
WHO permission ref: ____________________  (branch C, request not yet filed)
```

**Status: decided.** Locators (item 1) and passage excerpts (item 2) are served with full
attribution. Page images (item 3) stay behind `render_allowed: false` until WHO answers
the branch-C permissions request, which has not yet been filed — that reference stays
blank until it is, and a blank reference is not an argument for rendering pages anyway.

Two consequences, recorded here so neither is discovered later:

- **MVP done-item 3 is narrower than it reads.** "Every rendered claim resolves to a
  passage a reader can locate" now means locatable by address and quotable by excerpt,
  not visible as a page. This is written into
  [mvp-definition.md](mvp-definition.md) rather than silently satisfied.
- **This is a policy input, not a licence-wide switch.** `render_allowed` is a
  conjunction — `evidence.render_allowed and source.license_render_allowed` in
  `corpus/releases.py` — so branch A is applied by setting the source-level licence
  policy for WHO assets, and the per-evidence flag continues to govern independently.
  Nothing about the anchor chain, the evidence digest, or anchor replay changes.

Once taken, this decision sets `render_allowed` policy per asset in the materialization
layer. It is a policy input, not a code change to the anchor chain — the locator, the
evidence digest, and the anchor replay are unaffected by it either way.

## Applying it, 2026-08-28

The decision could not be expressed when it was taken. `render_allowed` was one boolean
gating both the excerpt and the page image, which is the conflation this document opened
by naming — so branch A was unrepresentable: permitting item 2 would have permitted
item 3 with it.

**Done.**

- The source-level permission is split in two. `license_excerpt_allowed` is "the passage
  text may be shown"; `license_render_allowed` narrows to "a region of the source page
  may be reproduced". Migration `0015_licence_excerpt_permission` adds the column and
  backfills the excerpt permission for any source already cleared for page reproduction,
  which is the only direction that is implied.
- The two are **nested, not independent**: the page permission is derived from the
  excerpt permission, because a page whose text may not be quoted cannot have that text
  reproduced as a picture instead. This also makes `EvidenceDetail`'s own invariant —
  restricted evidence exposes no exact highlight — structurally unviolatable rather than
  merely respected.
- `EvidenceDetail.render_allowed` now answers the client's question, "may I show this
  passage", so it carries the excerpt permission. The page permission reaches the client
  only through `exact_highlight_available`, which is the only thing it can license.
- The decision is recorded against all four WHO sources by
  `corpus-steward set-source-licence --excerpt --no-page-render`, which requires both
  flags explicitly so one can never be set while the other drifts.

**Why the source-level grant was not enough.** Every materialized evidence record carries
its own `render_allowed`, written at materialization from the trust root's licence policy
and sealed into the record's digest. The serving projection is a conjunction, so the
build-time copy vetoed the source-level grant and passages rendered blank. Note where that
copy comes from: `materialization_service.py` writes it from
`licensing_root.license_for_asset(asset_id)`, and `AssetLicensingPolicy` has a single
`render_allowed` boolean with no excerpt/page split. **The trust root is the thing that had
to change**, not the `sources` table — `set-source-licence` had already been run and was
already correct.

### The cost model this section first published was wrong

It claimed that "a re-materialization at the same materializer version reproduces every
evidence ID unchanged... the embeddings remain valid and nothing needs re-encoding." That
is not what happens, and the error mattered: it made the remaining work look like a
re-seal when it was a full re-encode.

At the same materializer version **there is no re-run at all.** `_run_id` is
`sha256(candidate:name:version)` with no timestamp, and `repository.existing()` keys on
that same triple, so a second `materialize` finds the stored run and returns it as a
success without ever re-reading the licence. The naive sequence silently changes nothing.

Defeating that short-circuit is what forces the cost. The only lever is the version, so
`MATERIALIZER_VERSION` moved `1.0.0` → `1.1.0` — a number that records a forced re-run
under a changed licence policy, not a change in what the materializer does. A new version
means a new `run_id`, and since `evidence_id` is `sha256(run_id:asset_id:unit_id)`, **every
evidence ID changes.** The existing vectors key on those IDs and cannot be reused;
`qdrant_index.py` rejects the mismatch outright with `VECTOR_BATCH_EVIDENCE_SET_MISMATCH`.
So the real work was re-materialize, re-QA, **re-embed all 5,145 approved records from
scratch**, re-seal, re-index, re-attest.

### A stale conflation in the QA gate, found on the way

`qa_repository.py` asserted `sources.license_render_allowed == evidence.render_allowed`
when registering canonical sources. That equation predates migration
`0015_licence_excerpt_permission` and was not updated with it: the evidence flag is the
*excerpt* permission, which is how `corpus/releases.py` reads it, while
`license_render_allowed` is the page-image permission this document holds shut. Under
branch A the two necessarily disagree, so QA refused every WHO source outright — and on a
source it had to *insert*, it would have granted page reproduction while denying excerpts,
inverted on both axes. The check now asserts `license_excerpt_allowed`, and says nothing
about the page permission: nothing about a piece of evidence licenses reproducing the page
it came from, and that decision is left to `set-source-licence` and its fail-closed
default.

### What shipped

Release `CR_6c5f7e503ea9382fd77356de226fd4bc`, from
`MAT_96aa943935b28a8ef0da9b902ee1cb70` via `CRC_d2993bdf4f7a007b882108bebe81d2d6`, with
5,145 approved records — the same count as the release it replaces, which is the evidence
that only the licence flag moved. `WHO_HIV_DAK_2_ANNEX_D` was deliberately left at
`render_allowed: false`; it has no `sources` row and its records have never reached a
release. Like its predecessor the release ends at `VALIDATED` and is served as
`RESEARCH_UNACTIVATED` — activation is a separate signed gate and this was not it.

**A reader now gets locators, citations, and passage text.** What stays withheld is the
page image: `license_render_allowed` is still `false` on all four sources, so
`exact_highlight_available` is false everywhere. That is branch A holding exactly where it
was drawn.

## What the excerpt permission was then used for, 2026-08-28

Two surfaces were added on top of the branch A grant. Neither reaches item 3, and both are
recorded here so the excerpt permission's actual footprint is written down rather than
inferred from the code.

**The row, rather than its serialisation.** `content_exact` for a workbook record is the
extractor's `A146=…\nE146=…` line-per-cell form of one spreadsheet row, and the interface
printed it verbatim. It is now parsed back and laid out as addresses and values. This
reproduces exactly the same bytes the excerpt permission already covered — the parse is
presentational, and it falls back to printing the text unchanged whenever the shape does
not hold completely.

**The rows either side of a cited one.** `GET /v1/sources/{id}/tables/{table_id}/rows`
answers with the evidence records anchored within three rows of a cited one, bounded at
ten. This *is* a wider disclosure than a single citation: a reader who opens it sees up to
seven rows where they previously saw one. It is judged to sit inside branch A on three
grounds, and if any of them is wrong this is the surface to withdraw first:

- Every row is projected through `_safe_evidence_detail`, the same path a citation takes,
  so a source without `license_excerpt_allowed` yields addresses and no text. The window
  cannot be used to reach text a citation withheld.
- Seven rows of a decision table is still a short excerpt of a 5,145-record release, and
  the request is per-citation and reader-initiated rather than a bulk route.
- It reproduces no figure, table image, or map, so WHO's carve-out — the thing that
  actually blocks item 3 — is not engaged. What is reproduced is text.

The page permission is untouched by both. `license_render_allowed` remains `false` on all
four sources, and the expanded page viewer, page turning, and higher-resolution rendering
that were built alongside these are all reachable only through the route that reads it.

### The serving path was never moved onto it, 2026-08-28

"What shipped" above records the release and stops there, which left the decision
invisible to anyone using the product. The release was built, but the serving stack was
still pinned to its predecessor, so every passage in the workspace continued to render
"Licence does not permit showing this passage" — correct code reporting a stale release.

Three pins in `.env` and one missing index were the whole of it:

- `SERVING_RELEASE_BUNDLE_PATH` -> `data/local/validated-who-smart-hiv-release-v2.json`
- `SERVING_VECTORS_PATH` -> `data/local/benchmark-source-derived/qwen3-0.6b-release-vectors-v2.json`
- `SERVING_QDRANT_COLLECTION` -> `corpus_cr_6c5f7e503ea9382fd77356de226fd4bc--vp-ba7b034759c3753db76951da`

The bundle and the vectors already existed; only the Qdrant collection had never been
built. `qdrant-build` against the conflict-aware Qwen3-0.6B + BM25 candidate indexed all
5,145 approved points and validated clean.

**The vector-profile suffix changes with the release even when the models do not.**
`candidate_vector_profile_sha256` digests `corpus_release_id` and `manifest_sha256`
alongside the dense and sparse model pins, so the same candidate yields `vp-ba7b0347...`
here against `vp-7d236ba6...` on `CR_b6155a25`. A differing suffix is not evidence of a
differing candidate, and reading it as one would send someone rebuilding a profile they
already have.

Verified end-to-end: a question against the running API returns ten evidence records, all
`render_allowed: true` and all carrying `exact_text`. Page images remain withheld —
branch C is still unfiled, and nothing here changes that.

**The general point.** A licence decision is not applied when the release embodying it
exists; it is applied when the thing serving requests is the release embodying it. This
document twice recorded the former as done. The serving pins belong in the "What shipped"
checklist, not in a follow-up section written after someone hit the wall.

## Sources

- CC BY-NC-SA 3.0 IGO legal code: <https://creativecommons.org/licenses/by-nc-sa/3.0/igo/legalcode>
- WHO publishing copyright policy: <https://www.who.int/about/policies/publishing/copyright>
- WHO permissions request form, for branch C, linked from the policy page above
