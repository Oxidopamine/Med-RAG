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

**Not yet in effect, and why.** Every one of the 5,145 evidence records carries its own
`render_allowed: false`, written at materialization from the trust root's licence policy
and sealed into the record's digest. The serving projection is a conjunction, so the
build-time copy vetoes the source-level grant and passages still render blank. Making
branch A visible therefore requires re-materializing the release under a licence policy
that permits rendering.

That is cheaper than it sounds and is still not free. `evidence_id` derives from
`run_id:asset_id:unit_id` and not from `render_allowed`, so a re-materialization at the
same materializer version reproduces **every evidence ID unchanged**, and `content_exact`
is untouched — so the embeddings remain valid and nothing needs re-encoding. What does
change is each record's digest, and therefore the release manifest and the release ID:
the work is a re-materialize, re-register, re-validate, re-seal of the existing vectors
under the new release ID, re-index, and re-attest.

[mvp-definition.md](mvp-definition.md) already says to batch that re-release behind the
coverage decision, because everything binding to this corpus is work done twice if stage
2 condemns it. So the flag flip rides that re-release rather than being taken on its own,
and the code is ready for it. **A reader today gets locators and citations; passage text
arrives with the next release.**

## Sources

- CC BY-NC-SA 3.0 IGO legal code: <https://creativecommons.org/licenses/by-nc-sa/3.0/igo/legalcode>
- WHO publishing copyright policy: <https://www.who.int/about/policies/publishing/copyright>
- WHO permissions request form, for branch C, linked from the policy page above
