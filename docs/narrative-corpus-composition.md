# Narrative corpus composition and unit granularity

Status: **complete through item 8.** A real WHO guideline is now a signed corpus release
(`CR_e043c79f575f5a54ed09cefaf53d5506`), and composite assembly of several documents works
end to end. Reviewed 2026-08-30 at `/code-review high`: eight findings, all addressed — see
"What review found". Remaining: index build, benchmark acceptance, and the coverage
measurement whose frame is decided in
[narrative-coverage-frame.md](narrative-coverage-frame.md)
Authored: 2026-08-30
Decided: 2026-08-30
Scope: the two properties that enter signed history the moment the first narrative
release is signed, and therefore have to be decided before it is

[narrative-only-materialization.md](narrative-only-materialization.md) takes a PDF-only
publisher as far as a signed input closure and stops at two open questions it did not
own. Work item 9 — "enable the WHO NCD trust root and run all 13 items end to end" —
produces thirteen releases of which serving can hold exactly one, and that note's closing
section states plainly that extraction granularity "is not solved here". Both are decided
below.

Neither decision is reversible after the first narrative release is signed. Both are free
today: the NCD trust root is still `enabled: false`, and the census that ran on the WHO
2021 consolidated guidelines (594 pages, 592 text-bearing units) has not been promoted to
anything.

## D1 — The servable corpus is one composite release

**Decision.** A narrative corpus of many documents composes into **one** corpus release,
through a sibling `CompositeCorpusReleaseCandidateContent` that binds a tuple of QA'd
`(materialization_run_id, qa_run_id, evidence_manifest_sha256)` triples instead of one
`materialization_run_id`. Widened by union, per the option C strategy already established
in [narrative-only-materialization.md](narrative-only-materialization.md).
`CorpusReleaseCandidateContent` is frozen and not touched.

### Why the alternative is not the cheap one

The obvious reading of "thirteen documents, thirteen releases" is that serving should
widen to a set of releases. It should not, and the reason is not a preference:

1. The measured retrieval system is weighted RRF over **one** candidate pool.
   `ServingRetrievalService` searches one collection
   ([retrieval_service.py:240](../apps/api/app/reasoning/retrieval_service.py#L240)), and
   `ServingPipeline` holds one release's evidence map and one collection, refusing to
   serve if either moves
   ([serving_pipeline.py:114-128](../apps/api/app/reasoning/serving_pipeline.py#L114-L128)).
2. A collection belongs to exactly one release. `corpus_releases.qdrant_collection` is
   `unique=True` ([models.py:318](../apps/api/app/persistence/models.py#L318)), and
   `IndexVectorBatchContent`, `IndexValidationReport` and the index attestation each bind
   a single `corpus_release_id`
   ([index_schemas.py:75](../apps/api/app/corpus_steward/index_schemas.py#L75),
   [:139](../apps/api/app/corpus_steward/index_schemas.py#L139),
   [:176](../apps/api/app/corpus_steward/index_schemas.py#L176)).
3. Therefore serving N releases means N searches fused across N independently ranked
   lists. **That is a different fusion**, not a wider filter. It mints a new benchmark
   contract, voids the frozen comparator floor, and requires re-measuring the accepted
   development report against a ranking function nobody has measured.

Multi-release serving is a rewrite of the measured system wearing the costume of a
configuration change. The activation gate would also have to move from a singleton
pointer to a set, which is the single most safety-critical transaction in the build
plane. Neither cost buys anything the composite release does not already give.

### What the composite release costs, and what it does not

The objection to one release is that adding one guideline re-releases the whole corpus.
That is true of the *release*, and mostly false of the *work*:

| Stage | Incremental when document 14 is added? |
| --- | --- |
| Acquisition, closure, narrative analysis | Yes — per item, already |
| Materialization | Yes — the narrative materializer derives one run per `(candidate, item)`, and an unchanged item's run is unchanged |
| Evidence QA | Yes — QA is per materialization run, and evidence is content-addressed, so an unchanged document's `evidence_id`s and digests do not move |
| Release assembly | New, and cheap — it re-signs a tuple of digests |
| Vector production | **Not today.** `IndexVectorBatchContent` is release-scoped, so a new release means a new batch. But `EvidenceVectorRecord` is `(evidence_id, evidence_sha256, dense, sparse)` — a pure function of evidence content ([index_schemas.py:59-70](../apps/api/app/corpus_steward/index_schemas.py#L59-L70)). A digest-keyed cache makes the new batch a copy plus one document's embeddings |
| Index construction | Mostly — collection creation is already no-delete and resumable |
| **Benchmark acceptance** | **No, and this is the real cost.** The source-derived suite is generated from the release, so adding a document regenerates the suite and moves the comparator floor |

So the scaling work is two named items, neither of them blocking the first narrative
release:

- **A digest-keyed vector cache**, so re-release is I/O rather than GPU. Do it when
  re-embedding actually hurts, not before.
- **Per-document benchmark partitioning**, so acceptance measures the added document's
  stratum against a floor the other documents already established, rather than
  re-measuring the whole suite. This is the one that decides whether the corpus scales
  past a few dozen documents. Defer until roughly the fifth document, when the shape of
  the problem is visible in real numbers rather than predicted.

### Shape of the change

- QA stays per-document. It is the right granularity: a QA batch is a complete,
  immutable approve/quarantine decision over one materialization run, and nothing about
  breadth argues for merging those.
- A new assembly step runs *after* N QA batches and before index construction, verifying
  that every bound QA run is approved, that no `evidence_id` collides across runs, and
  that every run shares one trust-root revision. It emits a signed composite candidate.
- `CorpusReleaseEvidenceRow` already holds many rows per release
  ([models.py:413](../apps/api/app/persistence/models.py#L413)), so release membership
  persistence needs no change.
- `CorpusQARunRow` carries both `corpus_release_candidate_id` and
  `materialization_run_id`; under composition the candidate id it carries is the
  composite one, which is a repository change, not a schema break.

## D2 — A PDF evidence unit is a bounded group of layout blocks

**Decision.** The narrative path extracts **block-grouped units**: consecutive PyMuPDF
text blocks in sorted reading order, grouped under a size window, never crossing a page
boundary, with running headers and footers removed. Unit identity is
`pdf:page:{page}:block:{first_block_ordinal}`. Anchors carry the bboxes of the blocks in
that unit only.

### The extractor is the replay contract

This is the fact that makes granularity irreversible, and it is worth stating exactly.
`QAService._replay` re-runs `extractor.extract()` over the preserved source bytes and
requires, per `source_unit_id`, that `content_exact`, `content_search` and `anchors` all
match the stored evidence record exactly
([qa_service.py:250-300](../apps/api/app/corpus_steward/qa_service.py#L250-L300)). The
extractor's output *is* the C2 property. Changing how it enumerates units retroactively
makes every record built from the old enumeration unverifiable.

Two consequences:

1. **`DAKSourceExtractor` is frozen on the DAK path.** The HIV release's `MAIN` asset
   holds `pdf:page:N` units. Editing `_pdf` in place would mean the HIV release could
   never be replayed again — C2 lost retroactively for records already inside a signed
   release. The narrative path therefore gets its own extractor identity, exactly as it
   gets its own materializer name, version and run-id function.
2. **The census has to move with it.** `analyze_narrative_document` defaults to
   `DAKSourceExtractor` ([narrative_analyzer.py:196](../apps/api/app/corpus_steward/narrative_analyzer.py#L196),
   [:227](../apps/api/app/corpus_steward/narrative_analyzer.py#L227)), and the whole
   point of `unit_inventory_sha256` is that materialization can recompute it and
   disagree. If the census and the materializer enumerate differently the check is not
   weakened, it is inverted — it would fail on every correct run. Both stages take the
   narrative extractor.

A useful property falls out of the same mechanism: replay hands the extractor the
**entire document**, so document-global decisions are deterministic and replayable.
Running-header detection — text repeating at the same y-position across a majority of
pages — is therefore admissible inside the extractor. A rule that needed state from
outside the artifact would not be.

### Why not pages, and why not recommendations

Pages are what the DAK path does and what
[narrative-only-materialization.md](narrative-only-materialization.md) already predicts
will fail: 1–4 KB of prose spanning several unrelated recommendations, so a page that
matches on one paragraph drags in everything else on it, and WHO boilerplate becomes a
term occurring in every unit of a 300-page document. On a 13-document corpus that is
thirteen title strings appearing in several thousand units.

Recommendation-scoped units are what retrieval actually wants, and they are refused
anyway. A parser that decides what counts as a recommendation is a clinical heuristic,
and D2's whole point is that the extractor's output is frozen into signed history and
must be reproduced by replay forever. Putting "is this a recommendation" there means
every future correction to that judgement is a re-release of the corpus, and it will be
fragile across publishers whose recommendation formatting differs.

**The semantic judgement goes where it can still be changed.** Two places, both already
built for it:

- **Evidence roles at QA time**, which is where the F2 fix belongs and which
  [roadmap.md](roadmap.md) already says to do once, alongside narrative materialization.
- **Passage form at serving time**, in `presentation.py`, which is a *view* — re-decidable
  without touching a digest. This is where the narrative analogue of `_classify_row` goes,
  and it is load-bearing: today anything not cell-addressed becomes `PassageKind.NARRATIVE`
  ([presentation.py:600-610](../apps/api/app/reasoning/presentation.py#L600-L610)) and
  `NARRATIVE` is in `RECOMMENDATION_BEARING_KINDS`, so on a prose corpus every table of
  contents, methods chapter and reference list is recommendation-bearing and the role gate
  stops being able to fail closed at all.

### The unit rule, precisely

Deterministic, and every part of it recomputable from the artifact alone:

1. Take `page.get_text("blocks", sort=True)`, drop non-text blocks and empty blocks —
   unchanged from the current `_pdf`.
2. Drop **boilerplate blocks**, under the conjunction in the next section.
3. Group the surviving blocks in reading order into units, closing a unit when adding the
   next block would exceed the size window, and always at a page boundary. Blocks are
   grouped, never split, so a unit boundary always falls on a boundary the extractor was
   handed and no anchor covers a region the source did not delimit.
4. `content_exact` is the grouped block text joined as today; `anchors` are that unit's
   block bboxes only.

### The boilerplate rule, as measured rather than as first drafted

This note's first draft said "repeats at a comparable y-position on at least a fixed
majority of the document's pages". **Measurement refuted it**, and the corrected rule is
recorded here because the reason it was wrong is a property of the artifact class rather
than an arithmetic slip.

Measured on the WHO 2021 consolidated HIV guidelines (594 pages, 8,870 text blocks):

- **Naive exact-text repetition finds nothing.** No block text repeats on even 20% of
  pages. The publisher concatenates the page folio *into the running header block*, so the
  first block of a page reads `'118 Consolidated guidelines on HIV prevention, ...'` and
  every page's header is a distinct string. Digits are therefore masked before comparison.
- **With digits masked, the header is two-level and alternating**: the document title on
  264 pages (44%) and the chapter title on the rest, across eight variants of 13–264 pages
  each. A majority threshold would have caught at most one of them. The threshold is
  therefore a small fraction of pages — 2%, floored at 3 — not a majority.
- **Genuine recurring headings must survive**, and do. `Background`, `Research gaps` and
  `Implementation considerations` recur throughout the document but flow with the text
  (`Background` occurs at 29 different y positions) and are rarely first or last on their
  page.

A block is dropped only if all of these hold: it is first or last on its page in reading
order; it is at most 200 characters; its digit-masked fingerprint recurs at the same
20-point y-band on at least the page threshold; and that fingerprint carries at least 12
non-digit characters, or is a bare folio. The last clause is a guard rather than a
measured need — no false positive occurs on this document — against a publisher whose page
furniture is terser, since two short body lines differing only by a number would otherwise
share a fingerprint.

The asymmetry is deliberate and worth stating: keeping a boilerplate line costs retrieval
quality, while dropping a real one silently removes clinical text from a corpus whose
whole claim is that every rendered claim is bound to a source. Every clause above is a
reason *not* to drop.

**Result on the calibration document:** 1,891 units against 592 page-units, median 986
characters against 3,222, 528 boilerplate blocks removed across 10 distinct fingerprints —
every one of them a running header or a bare folio, the longest 98 characters — and
identical output across repeated runs.

The size window, the thresholds and the y-band are constants of the extractor version.
Changing any of them changes every unit id and every anchor, which is a re-materialization,
so they were chosen once against a real document rather than picked as round numbers.

## Decisions

Accepted 2026-08-30.

1. **One composite release, not a release set.** The fusion argument decides it: N
   releases means N candidate pools means a different ranking function means a new
   benchmark contract. Scaling is addressed by making re-release incremental, not by
   making serving distributed.
2. **Block-grouped units with boilerplate removed, under a new narrative extractor
   identity.** `DAKSourceExtractor` is frozen; the census moves to the new extractor with
   materialization.
3. **No clinical semantics in the extractor.** "Is this a recommendation" is decided at QA
   time (roles) and at serving time (form), both of which can be corrected later — QA at
   the cost of a re-release, presentation at no cost.

## Work items

Ordered; each independently reviewable. Items 1, 2 and 4 replace work item 7 of
[narrative-only-materialization.md](narrative-only-materialization.md); items 5–7 are new.

**Corrected 2026-08-30, after item 2.** This list originally went straight from the census
to the materialization branch, which cannot work: the census has schemas and a pure
processor but no service, repository, table or predicate, so there is nothing to *persist*
a signed census in and nothing for materialization to fetch and disagree with. That is the
unlanded half of work item 5 of the earlier note, and dropping it from this list was an
error in the list rather than a change of plan. It is now item 3, and the later items are
renumbered.

1. **`NarrativeSourceExtractor`**, its own name and version, block grouping and
   boilerplate removal. *(Done — `apps/api/app/corpus_steward/narrative_extractor.py`,
   `narrative-block-grouped` 1.0.0, with `tests/unit/test_narrative_extractor.py`.)*
   Calibrated against the WHO 2021 consolidated guidelines rather than against round
   numbers, which is what caught the folio-in-header defect in this note's first draft.
   `ExtractedAsset` gained a defaulted `dropped_boilerplate_blocks` counter — a plain
   dataclass, not a canonical model, so no digest is affected and the DAK path reports 0.
   The WHO hypertension guideline is not acquired yet (the NCD trust root is still
   unreconciled), so the constants should be re-checked against it at item 6 before
   anything is signed.
2. **Point the census at it** — `analyse_narrative_document`'s extractor default, and the
   `unit_inventory_sha256` regression check that materialization can disagree with.
   *(Done.)* `NARRATIVE_PROCESSOR_VERSION` moved 1.0.0 → **2.0.0**: the report's shape is
   unchanged but its unit enumeration is not, so the two are incomparable rather than
   merely different, and a minor bump would have implied otherwise. Nothing 1.0.0 was ever
   signed. A new test pins the census and the extractor together by asserting that a
   census taken with `DAKSourceExtractor` *cannot* agree — that mismatch is the failure
   this stage exists to detect, and it would otherwise fire on every correct run.
   Verified on the real 594-page guideline: 1,891 units, digest independently recomputed
   and equal, no clinical text in the serialized report.
3. **Narrative source analysis stage** — the service, repository, table, migration and
   attestation predicate that turn the pure census into a signed, stored report.
   *(Done — `narrative_service.py`, `narrative_repository.py`,
   `migrations/versions/0016_narrative_analysis_stage.py`, with
   `tests/unit/test_narrative_analysis_stage.py`.)* Mirrors `structured_service` /
   `structured_repository` shape for shape, because materialization verifies the narrative
   report by the same route it verifies the structured one. Notes:
   - `narrative_analysis_runs` is keyed on (candidate, item, processor, version). The item
     is in the key deliberately — this is the multi-item topology, and a lookup keyed on
     the candidate alone would hand back the first document's census as every other
     document's, which is the defect already recorded against the structured materializer.
   - `source_topology()` was lifted to a module-level function in
     `structured_input_service`; the closure service's private static method now delegates
     to it. Two stages parsing the same field independently is how they stop agreeing.
   - A new `ArtifactKind.NARRATIVE_ANALYSIS_REPORT`, which required widening the
     `ck_steward_artifacts_kind` check constraint — the one place the store's vocabulary
     is enumerated.
   - The census refuses a DAK trust root outright. There `asset_id == item_id` does not
     hold, so the report would bind an asset that is not the clinical authority.
4. **Materialization narrative branch** — narrative materializer name/version/run-id,
   the unit-inventory recomputation against item 3's signed census, the
   `materialization_runs` migration, and the DAK-only scoping of the
   one-controlling-source restriction. *(Done —
   `MaterializationService.materialize_narrative`,
   `migrations/versions/0017_narrative_materialization.py`, with
   `tests/unit/test_narrative_materialization.py`.)* Four things worth carrying:
   - **The seal/attest/persist tail is shared, not duplicated.** Both topologies build
     their own authority binding and asset list, then run one `_materialize_assets`. Two
     paths signing a corpus release candidate independently is how they stop agreeing
     about what one means — the same argument that shaped the input closure.
   - **The census check is the point.** Materialization recomputes
     `unit_inventory_sha256` from the bytes it extracts and blocks with
     `UNIT_INVENTORY_MISMATCH` when it disagrees with the signed prior statement. A test
     drives that by handing the materializer the page extractor after a block-grouped
     census; without it the whole stage would be a rubber stamp.
   - **Coverage accounts in block groups, not pages — a real trade-off, recorded.**
     `AssetCoverage.verify_accounting` requires `materialized + empty == expected` *and*
     `materialized == len(evidence_ids)`, which hard-wires one evidence record per source
     unit. That holds for a page and a spreadsheet row and cannot hold for block groups.
     The schema is frozen by signed history and cannot gain a field, so
     `expected_source_units` is reported in block groups. What that gives up is that the
     count no longer independently proves every page was reached; that proof moves to the
     census comparison above, which is stronger. `empty_source_units` still names pages
     that produced nothing, so a page lost to over-aggressive boilerplate removal stays
     visible.
   - **The migration backfills rows a trigger calls immutable.**
     `trg_materialization_runs_immutable` rejects every UPDATE on the table, so 0017
     suspends it for the backfill and restores it immediately. That protection exists to
     stop the application rewriting signed history; `inventory_item_id` is new metadata
     already implied by the structured run each row points at, and no signed field is
     touched. The migration fails loudly rather than guessing if any row has no resolvable
     item.
5. **`analyze-narrative` CLI**, mirroring `process-structured`. *(Done — plus
   `materialize-narrative`, with `tests/unit/test_narrative_cli.py`.)* Both take
   `candidate_id` and `--item-id`, which is what makes the multi-item topology drivable at
   all: a WHO NCD candidate carries thirteen guidelines and without it only the first is
   reachable. **Exit codes name the stage class, not the topology** — `analyze-narrative`
   shares 5 with `process-structured`, `materialize-narrative` shares 7 with
   `materialize`. A caller reacting to "source analysis refused this artifact" should not
   have to learn a second vocabulary depending on what kind of source it was, and the
   distinct-status property exists so a signed policy block is never mistaken for a crash.
   Not yet run against a real document: no narrative-anchored trust root is registered and
   the NCD root is still `enabled: false`, so the first real invocation is item 7.
6. **Composite release assembly** — *(Implemented; end-to-end verification blocked, see
   below.)* `release_assembly_schemas.py`, `release_assembly_service.py`,
   `migrations/versions/0018_composite_release_assembly.py`, the `assemble-release` CLI
   (exit 9), and `tests/unit/test_release_assembly.py`.

   **D1's sketch was wrong about the shape, and the code is better than the plan.** D1 said
   this needed a sibling `CompositeCorpusReleaseCandidateContent`. It does not.
   `CorpusReleaseManifestContent` already carries `inventory_snapshots` as a tuple
   validated against repeating a trust root, and `evidence` as a tuple — **the release
   manifest was multi-source from the start**. So assembly merges member bundles and hands
   the result to `SQLCorpusReleaseRepository.register_candidate`, the same registrar and
   the same registry gate every single-document release goes through. No new release
   schema, and `corpus_release_evidence` needed no change.

   What is genuinely new is the *decision*: `SignedReleaseAssembly` records which QA runs
   were composed at which digests and what manifest resulted, because "which documents are
   in this release" should be answerable from the registry rather than by reading a
   manifest and trusting it. `corpus_release_members` makes it queryable, with `qa_run_id`
   unique table-wide — a run composed into two releases would be indexed and activated
   under two identities.

   Assembly refuses: a member that did not clear QA, colliding evidence IDs across members,
   members under different licensing revisions or release policies, and a run already
   inside another composite. The release id derives from the member bundle digests, so
   re-assembling the same set names the same release.

   Three DAK assumptions surfaced and were widened on the way, each the same shape —
   `ArtifactKind` records *how bytes were acquired*, not whether they may become evidence:
   `qa_repository` and `app/corpus/releases.py` both hard-required `NARRATIVE_SOURCE`, and
   `ReconciliationService.reconcile` required `connector_config.structured_asset_id` to
   find one acquisition permission. **The parked WHO NCD trust root declares no
   `structured_asset_id`, so it could not have been reconciled at all.** Narrative
   acquisition permission is now a conjunction over the licensed assets, with per-item
   licensing enforced in `_fetch` where the item list is finally known.
7. **Teach the QA classifier this artifact class.** *(Done —
   `qa_classification._narrative_roles` / `_narrative_applicability`,
   `presentation.classify_prose`, with `tests/unit/test_narrative_role_classification.py`
   and the decision measured first in
   [narrative-role-classification.md](narrative-role-classification.md).)* Narrative
   evidence now clears QA. Two findings from building it:
   - **Anchor replay was hard-wired to the DAK extractor.** `QAService` replays by
     re-extracting the preserved bytes, so a narrative release replayed with the page
     extractor found no unit for any record and quarantined all of them — the check would
     have reported a corrupted corpus on a correct one. The extractor is now selected by
     the run's topology, as materialization already did.
   - **The serving-side length floor was wrong and an existing test caught it.**
     *"Start ART in all adults."* is 24 characters and is a recommendation. The floor
     belongs at QA time, where it matches the published frame definition; a view decides
     what form a passage takes, and a short sentence is still a sentence.

   Original description, kept because it states the problem:
   `qa_classification._base_roles` assigns evidence roles by *source asset*, matching
   `ANNEX_A`/`ANNEX_B`/`ANNEX_C`/`MAIN` and returning an empty role set for anything else.
   Every narrative unit therefore quarantines as `CLINICAL_CLASSIFICATION_UNRESOLVED`, QA
   approves nothing, and **no narrative release can exist** — which is why item 6's
   two-document tests are written and skipped rather than passing. The applicability
   vocabulary is HIV-specific for the same reason: `_POPULATION_PATTERNS` and
   `_CARE_SETTING_PATTERNS` are regexes over HIV terms, so a hypertension guideline gets no
   population and no care setting either.

   This is correct fail-closed behaviour, not a defect, and it is the F2 fix
   [roadmap.md](roadmap.md) already says to make **once**, at QA time, alongside narrative
   materialization: roles decided from the passage rather than from the asset it came from.
   It is a clinical-safety decision — what counts as recommendation-bearing prose — and
   re-deciding `evidence_roles` later is a re-release, so it should be designed rather than
   inferred from whatever makes a test pass. The coverage-frame extractor in the benchmark
   tooling is a working prototype of the heuristic: statement-shaped block, deontic verb,
   GRADE marker, minus headings and footnotes.

   Note also that `presentation.RECOMMENDATION_BEARING_KINDS` admits `NARRATIVE`
   wholesale, so on a prose corpus the serving-side role gate stops being able to fail
   closed at all. Both halves belong in this item.
8. **One guideline end to end** to a validated, unactivated release, then read the
   answers. Not thirteen.

Deferred, with triggers rather than dates: the digest-keyed vector cache, when
re-embedding hurts; per-document benchmark partitioning, at roughly the fifth document.

## What review found

Run after items 1-7, at `high`. Eight findings, every one real; four were in composite
assembly, which its skipped tests had left unexercised. Recorded because the pattern is
the point: the defects clustered exactly where nothing executed the code.

| Finding | Disposition |
| --- | --- |
| The census digest was reimplemented inline in `narrative_analyzer` instead of calling `unit_inventory_digest`, whose docstring requires byte-identity | **Fixed.** Drift would have made every correct narrative materialization report a false `UNIT_INVENTORY_MISMATCH` - a fail-closed gate firing on sound input |
| `materialize_narrative` dereferenced `self._narrative_repository`, which defaults to `None` | **Fixed** - refuses up front, because a service wired without the census cannot perform the check that justifies the path |
| `_header_band` anchored on `min(occupancy)`, so one stray block above the header disabled band detection document-wide | **Fixed** - anchors on the topmost bin populated on most pages. Re-verified on the real guideline |
| Migration 0017 re-enabled the immutability trigger in a `finally`, inside an already-aborted transaction | **Fixed** - the rollback restores it, and letting the original error surface is what makes a failed backfill diagnosable |
| Assembly de-duplicated attestations on `(stage, digest)` while the manifest requires unique `stage`; every two-member composite died on an opaque validation error | **Refused explicitly.** Attestations cannot be merged at all - each is a signed statement about *its own* member's contents. A composite needs attestations issued over its own content, which is the promotion restructure |
| `_release_id` was deterministic but the manifest embedded `created_at = now()`, so a retry could never succeed | **Fixed** - the timestamp derives from the members |
| Inventory snapshots were de-duplicated by trust root, silently keeping the last when members disagreed | **Fixed** - refuses, because the discarded snapshot's excepted item ids feed the release's own exception cross-check |
| The membership claim and the registration are in separate transactions | **Recorded as a known limit** in the service docstring. The uniqueness constraint still prevents the corrupt outcome, so it is a poor error on a race rather than a lost invariant; making it atomic is the same restructuring |

## Item 8: the first narrative corpus release

Run 2026-08-31 against the real document, not a fixture.

**Source.** WHO, *Guideline for the pharmacological treatment of hypertension in adults*
(IRIS 10665/344424), acquired from `iris.who.int` under a new trust root
`WHO_GUIDELINES_HYPERTENSION` scoped to that one item. The parked
`who-guidelines-ncd.json` is untouched; widening `scope_item_ids` is how this grows.

| Stage | Result |
| --- | --- |
| Reconciliation | 843,539 bytes, `57f6376d…`, 4 signed stage attestations |
| Input closure | `RESOLVED`, narrative-anchored, no refetch |
| Narrative census | **176 units**, all five checks ran, `promotion_eligible` |
| Materialization | **176 evidence records**, coverage complete, no `UNIT_INVENTORY_MISMATCH` |
| Evidence QA | **176 replayed, 0 replay failures**, 160 approved / 16 quarantined |
| Release | `CR_e043c79f575f5a54ed09cefaf53d5506`, `VALIDATED`, index `NOT_BUILT` |

**The chain holds on a document nothing was calibrated on.** Anchor replay reproduced every
one of 176 block-grouped units from the preserved bytes, and the materializer's recomputed
unit inventory agreed with the separately signed census.

**The role surface is plausible, which is the point of the whole expansion.** 52 of 160
records carry `PRIMARY_SUPPORT` (32.5%), against F2's DAK measurement of 90.7% carrying it
with 91% of those unable to bear a recommendation. `APPLICABILITY` resolved on 75 records
with populations like *adults*, *women*, *patients with hypertension* — the HIV vocabulary
would have returned nothing and blocked the role gate outright.

**The conditional register earned itself again.** The first recommendation in the release
reads *"WHO suggests pharmacological antihypertensive treatment of individuals without
cardiovascular disease but with high cardiovascular risk, diabetes mellitus, or chronic
kidney disease, and systolic blood pressure of 130–139 mmHg."* Under the first version of
the classifier — the one that scored 0.952 — that passage would have been `RATIONALE` and
not recommendation-bearing.

### Three findings to carry forward

1. **`recommendation_grade` is null on all 160 records.** Zero passages contain an inline
   `(strong recommendation, …certainty evidence)` marker: this guideline carries strength in
   a table rather than in body prose, unlike the HIV consolidated guidelines. Grade
   extraction is document-format-specific and does not transfer. It is not load-bearing for
   the role gate, but any downstream use of grade must treat absence as unknown rather than
   as "ungraded".
2. **`LICENSE_POLICY` warned rather than passed**, as on the HIV document: no XMP rights
   statement. The publisher-side licence cross-check is a stub in practice for WHO PDFs —
   job 3 of the four the structured report does is not really being performed.
3. **False positives are of the predicted kind.** A section introduction — *"6 Implementation
   tools 6.1 Guideline recommendations…"* — is labelled `PRIMARY_SUPPORT`. That is the
   0.828 precision floor showing up on a real document, it is the direction the rule
   deliberately errs in, and claim-level grounding is what protects the reader from it.

Also observed: an en-dash in *130–139 mmHg* survives extraction as a replacement character
in `content_exact`. Cosmetic in isolation, but `content_search` is what retrieval matches
on, so it is worth checking whether the normalisation drops or mangles it before the corpus
grows.
