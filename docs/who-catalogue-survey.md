# What the WHO guidelines catalogue actually contains

Status: measured 2026-08-31; corrects an earlier recommendation
Scope: whether widening `scope_item_ids` on the WHO hub connector delivers internal medicine

## The claim this note refutes

The expansion plan said the cheapest real breadth was widening `scope_item_ids` within the
existing `who-guidelines-hub` connector — *"358 GRC-approved guidelines, same publisher,
same licence, zero new trust-root work."* Two thirds of that sentence is wrong, and the
survey below is why. It was recorded rather than quietly dropped because the reasoning that
produced it looked sound and the catalogue was never actually read.

## What the survey did

Enumerated all 358 records through the same OData query
`WHOGuidelinesHubConnector.inventory_request` builds, so what it saw is what reconciliation
would see, and classified them by title and subtitle.

## Finding 1: the catalogue is not an internal-medicine corpus

| Domain | Records | Share |
| --- | ---: | ---: |
| Infectious disease | 113 | 32% |
| Maternal, newborn and child health | 90 | 25% |
| (unclassified — surgical, obstetric, infection control, policy) | 103 | 29% |
| Health systems and policy | 14 | 4% |
| NCD and chronic care | 11 | 3% |
| Mental health and substance use | 11 | 3% |
| Nutrition | 8 | 2% |
| Environment and occupational | 7 | 2% |
| Injury and violence | 1 | 0% |

**Records mentioning a core internal-medicine condition — hypertension, diabetes, heart
failure, COPD, kidney disease, anticoagulation, stroke, lipids, thyroid: four.** Three are
hypertension, and two of those three are hypertension *in pregnancy*. The fourth is a false
positive. There is no WHO guideline on the management of diabetes, heart failure, chronic
kidney disease, COPD or anticoagulation in this catalogue.

So widening to 358 would multiply the corpus roughly twenty-five-fold and add essentially
nothing to the specialty the expansion is for. **The parked `who-guidelines-ncd.json` was
already close to the ceiling of what WHO offers here** — which is a much better outcome for
that note than it first appears: it was not an arbitrary sample, it was near-exhaustive.

## Finding 2: the licence is not uniform

The catalogue's own `Copyright` field across 358 records:

| Declared | Records |
| --- | ---: |
| (empty) | 120 |
| CC BY-NC-SA 3.0 IGO (both spellings) | 66 |
| All rights reserved (both spellings) | 35 |
| World Health Organization, unqualified | 19 |

**Thirty-five WHO GRC-approved guidelines declare all rights reserved.** Five of them are
inside the chronic-care set this survey would otherwise have selected, including three
cervical-cancer guidelines. A trust root asserting `evidence_materialization_allowed: true`
across the catalogue would have claimed a licence WHO does not grant, on documents whose
own metadata says otherwise.

## Finding 3: enumeration has no partial-failure tolerance, by design

The first attempt at 285 items failed the whole reconciliation with `publisher returned
HTTP 404`, at `ENUMERATE_INVENTORY` rather than at any document fetch.

`WHOGuidelinesHubConnector._resolve_via_iris` distinguishes two failure classes and says so
in its own docstring: *structural* blockers (no IRIS item, withdrawn, no ORIGINAL bundle)
are recorded on the item as an `acquisition_blocker`, while *transport* failures raise —
"those are retryable faults, and turning one into a permanent exception would silently drop
a record the publisher does list."

That is the right call and it does not scale by itself. A stale IRIS handle returns 404
from `pid/find`, which is a transport failure, which raises, which fails the entire job. At
thirteen hand-checked items it never bit. At 285 it bit on the first run.

**A pre-flight is therefore a prerequisite for claiming a large scope**, not an
optimisation — and it has to be the connector's own resolution chain, not a
hand-reimplementation of it. Reconciling 285 failed on stale IRIS handles. Reconciling the
281 survivors then failed differently, with `publisher acquisitions require HTTPS`: an item
whose IRIS `ORIGINAL` bundle holds no English-suffixed PDF is `blocked` rather than
resolved, keeps the `urn:who-guidelines-hub:unresolved` placeholder as its `artifact_url`,
and fails the HTTPS gate at fetch. Hand-replicating the chain missed that rule, having
already missed stale handles.

The reliable method is to **dry-run `connector.enumerate_inventory` itself** and keep only
the items it returns with a resolved `https://` artifact URL and no `acquisition_blocker`.
That is exact by construction, because it is the code reconciliation runs.

| Stage of narrowing | Items |
| --- | ---: |
| GRC-approved catalogue | 358 |
| minus records WHO declares all rights reserved | 285 |
| minus stale or absent IRIS handles | 281 |
| minus no English PDF in the ORIGINAL bundle | **235** |

`allow_unsuffixed_language` stays false throughout: the connector must not guess across
languages, so a document published only in French is out of scope rather than acquired as
if it were the English edition.

## Finding 4: a relative artifact-store path forks the store by working directory

Unrelated to the catalogue, found while driving the pipeline, and the most quietly dangerous
of the four.

`STEWARD_ARTIFACT_STORE_PATH` was `data/local/steward-artifacts` — relative. `corpus-steward`
is documented to run from `apps/api`; the API server is documented to run from the repo root
with `--app-dir apps/api`. So the two resolved the same setting to **two different stores**:

- `data/local/steward-artifacts` — 22,133 blobs, the HIV corpus
- `apps/api/data/local/steward-artifacts` — 1,195 blobs, everything driven through the CLI

The database is shared and references artifacts by digest, so nothing failed at write time.
The hypertension release's evidence records, authority binding and release bundle were
written where the serving process cannot read them, and that would have surfaced only when
something tried to read one — which is exactly how it was found, when a QA re-read of a
stored bundle raised `FileNotFoundError` on a digest the database happily reported.

The two stores had **zero overlapping digests**, and every sampled blob's filename matched
the SHA-256 of its own contents, so consolidating them is a pure union with nothing to
reconcile. The setting is now absolute, and the backup of the previous `.env` is
`.env.bak-before-abs-store`.

## What was built instead

`data/trust-roots/who-guidelines-chronic-care.json` — ten adult chronic-care guidelines,
selected from the survey rather than assumed:

- Excludes every record declaring all rights reserved.
- Excludes infectious-disease, obstetric and paediatric-only records.
- Carries each asset's catalogue `Copyright` value verbatim in its notes, and sets
  `source_declared_license_id` only where WHO actually declares one — six of the ten declare
  nothing, so the field is null rather than guessed.

It is not registered or reconciled. One of the ten (hypertension) is already ingested.

## The conclusion that matters

WHO is not a source of internal-medicine guidelines. The machinery now handles narrative
corpora end to end, and the corpus it can reach from this publisher is roughly ten adult
chronic-care documents. **Breadth in internal medicine requires a non-WHO publisher, and
that is a licensing conversation rather than an engineering one** — NICE, ACC/AHA, ESC, ADA,
KDIGO, GOLD and IDSA are all-rights-reserved and need per-publisher permission.
