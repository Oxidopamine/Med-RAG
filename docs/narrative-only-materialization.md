# Narrative-only materialization

Status: decisions accepted; step 1 (digest regression fixture) complete, schema widening not started
Authored: 2026-08-27
Decided: 2026-08-27
Scope: the structured/materialization seam, so a PDF-only publisher can reach evidence
Test case: `data/trust-roots/who-guidelines-ncd.json` (13 WHO guidelines, all CC BY-NC-SA 3.0 IGO, `enabled: false`)

## The gap is wider than the structured report

The stated blocker is that `MaterializationService.materialize` requires a structured
package report and binds `structured_report_sha256`. That is real, but it is the third of
four blockers a PDF-only source hits, and it is not the first one it hits.

**1. The input closure blocks before materialization is reached.**
`StructuredInputClosureService.resolve` parses the preserved source artifact as a FHIR
package unconditionally ([structured_input_service.py:141](../apps/api/app/corpus_steward/structured_input_service.py#L141)).
A PDF raises `FHIRPackageValidationError`, which becomes a `SOURCE_PACKAGE` **BLOCK**
check, which makes `complete=False`, which stops materialization at
[materialization_service.py:121](../apps/api/app/corpus_steward/materialization_service.py#L121)
— one line *before* the structured-report check at
[:132](../apps/api/app/corpus_steward/materialization_service.py#L132). `_policy` also
hard-requires `connector_config.dependency_registry`
([:819](../apps/api/app/corpus_steward/structured_input_service.py#L819)), which a PDF
publisher has no reason to declare.

**2. There is no narrative asset to resolve, and there cannot be one.**
The closure acquires narratives from `connector_config.controlling_narratives`. The WHO
NCD trust root has no such block and must not grow one: each guideline's PDF URL is
resolved per item through its IRIS handle at enumeration time, and the connector
deliberately refuses to bind the unstable hub `DownloadUrl` into anything
([corpus-steward.md](corpus-steward.md), "DownloadUrl is never bound"). Thirteen
statically-declared bitstream URLs would reintroduce exactly the drift that decision
removed.

The trust root already encodes the right model. Its `asset_licensing` entries are keyed by
the WHO record UUID, and `scope_item_ids` holds the same UUIDs — which is `item_id` from
`WHOGuidelinesHubConnector._to_item`. **For a narrative-anchored publisher the inventory
source artifact *is* the controlling clinical narrative.** `asset_id ≡ item_id`. That is
the topology, and it is the inverse of the DAK topology, where the source artifact is the
FHIR package and the narratives are side-channel assets.

**3. One digest, one artifact kind.**
Reconciliation preserves the guideline PDF as `ArtifactKind.SOURCE`
([service.py:353](../apps/api/app/corpus_steward/service.py#L353)).
`SQLMaterializationRepository.artifact_storage_keys` rejects any input artifact that is not
`NARRATIVE_SOURCE` ([materialization_repository.py:102](../apps/api/app/corpus_steward/materialization_repository.py#L102)),
and `SQLReconciliationLedger.record_artifacts` refuses to re-register a digest under a
second kind ([ledger.py:368](../apps/api/app/corpus_steward/ledger.py#L368)). So "just fetch
it again as a narrative asset" is closed off by design, and correctly so — the same bytes
must not acquire two provenance stories.

**4. One materialization run per candidate — a live correctness bug on the breadth path.**
`MaterializationRunRow` is unique on `(reconciliation_candidate_id, materializer_name,
materializer_version)`. `_run_id` hashes only the candidate and the materializer
([materialization_service.py:606](../apps/api/app/corpus_steward/materialization_service.py#L606)),
and `repository.existing(candidate_id)` ignores `item_id` entirely. The HIV candidate holds
one included item, so this never showed. The WHO NCD candidate holds thirteen:
`materialize --item-id <second>` would find the first item's stored run and **return it as
that item's result**, silently. This is not a missing feature; it is a wrong answer.

## What the structured report actually contributes

Before replacing it, name the jobs it does, because "make the field optional" drops all
four:

1. **An independent, signed statement about the interior of the acquired bytes.** The
   artifact was parsed under a declared `processor_name`/`processor_version`, safely, and
   yielded an enumerable set of addressable units digested as `resource_inventory_sha256`.
2. **A second, independent path back to the same anchors.** The structured report binds
   `input_closure_sha256` and `structured_input_run_id`, which bind `source_artifact_sha256`
   and `trust_root_sha256`. Materialization verifies that path separately in
   `_verify_structured_report`.
3. **A publisher-side licence cross-check.** `_require_companion_license` compares the
   package's own declared licence against the operator's per-asset policy. The operator's
   claim about the licence is checked against the source's claim.
4. **A role boundary carried into the report.** `StructuralMappingAttachment` is pinned to
   `STRUCTURAL_MAPPING_ONLY` / `clinical_content_included: False`.

Jobs 1–3 have direct narrative analogues. Job 4 does not: in the DAK topology the
structural asset is *not* the clinical authority, so the boundary runs between two
artifacts. In the narrative topology one artifact is both. The boundary that must survive
is the one that actually carries the safety property — **clinical content may be promoted
only from an asset the trust root licenses for evidence materialization and that the
binding marks `CONTROLLING_CLINICAL_SOURCE`** — and that is unaffected.

## Recommendation: a narrative source analysis stage, as a peer of structured processing

Add a stage, not a bypass. `NarrativeSourceAnalysisService` (`narrative_service.py`,
`narrative_schemas.py`), processor `narrative-document-native` `1.0.0`, running between the
input closure and materialization, mirroring `structured_service` shape for shape:

- **Identity and continuity fields identical to `StructuredPackageReportContent`:**
  `narrative_run_id`, `reconciliation_candidate_id`, `trust_root_id`/`_sha256`,
  `inventory_item_id`, `source_artifact_sha256`, `structured_input_run_id`,
  `input_closure_sha256`, `processed_at`.
- **Per-asset `NarrativeDocumentAnalysis`:** `asset_id`, `artifact_sha256`, `media_type`,
  `byte_size`, `unit_count`, and **`unit_inventory_sha256`** — the digest of the ordered
  `(source_unit_id, sha256(content_exact))` pairs. Structure and identity only; no clinical
  text enters the report.
- **Document declarations, the licence cross-check analogue:** PDF version, encryption,
  embedded files, JavaScript presence, DocInfo title/producer/creation date, and XMP
  `dc:rights` / `xmpRights:WebStatement` as `declared_license_id`. WHO PDFs carry the
  CC BY-NC-SA 3.0 IGO statement, so job 3 survives with real content rather than a stub.
- **Checks mirroring `StructuredCheckCode`:** `DOCUMENT_SAFETY`, `DOCUMENT_IDENTITY`,
  `UNIT_COVERAGE`, `LICENSE_POLICY`, `NARRATIVE_AUTHORITY`; `promotion_eligible`,
  `blockers`, `warnings` gated the same way.
- **Signed and attested** under its own predicate
  (`.../attestations/narrative/NARRATIVE_SOURCE_ANALYSIS`), artifact-stored,
  ledger-recorded.

The load-bearing detail: **this stage is not the extractor.** It produces a structural
census; the extractor produces clinical text. Materialization must recompute the unit
inventory from the bytes it extracts and compare it against the signed
`unit_inventory_sha256`. Without that comparison the new report is a rubber stamp and the
chain is weaker than the one it replaces. With it, the relationship is exactly the one the
FHIR path has — an independent prior statement that the later stage must agree with.

## The digest-stability trap, and why it decides the contract shape

`canonical_json_bytes` is `model_dump(mode="json")` with no exclusions
([corpus.py:24](../apps/api/app/schemas/corpus.py#L24)), and
`SignedAuthorityBinding.verify_digest_and_attestation` recomputes the digest on **every**
`model_validate` ([materialization_schemas.py:130](../apps/api/app/corpus_steward/materialization_schemas.py#L130)).

Therefore: **adding any field to `AuthorityBindingContent` — including
`x: T | None = None` — changes the canonical bytes of every already-signed HIV binding, and
every stored materialization report and corpus release candidate stops loading.** The
`None` default does not save you; it serializes.

The repo has already hit this and has a precedent for the escape hatch:
`TrustRootDefinition.sha256` pops `asset_licensing` from the payload when the legacy
`licensing_policy` is set, with a comment saying exactly why
([schemas.py:114-122](../apps/api/app/corpus_steward/schemas.py#L114-L122)).

It also has a precedent for the *better* answer, and did not label it as one. The stored
HIV materialization report predates compact evidence entries and still carries 5,854 full
`MaterializedEvidenceRecord`s. It loads today only because `MaterializationReportContent`
was migrated by **widening the field to a union** rather than replacing it. Option C is not
a new strategy for this codebase; it is the one that already worked once, unnamed.

Three ways out:

| Option | Approach | Cost |
| --- | --- | --- |
| A | Compat-shim digest on `AuthorityBindingContent`, following the trust-root precedent | Preserves history; adds a second shim, and shims accumulate |
| B | Bump `MATERIALIZER_VERSION`, re-materialize the HIV lane | Simple, but `_run_id` and therefore every `evidence_id` changes, cascading into QA batches, canonical evidence, releases, and benchmark adjudications |
| C | **Never add a field. Widen existing fields by union.** | Byte-identical for existing content; needs disjoint required fields so validation resolves the right member |

**Recommend C.** Pydantic serializes the union *member*, not the union, so widening
`structural_mapping: StructuralMappingAttachment` to
`StructuralMappingAttachment | NarrativeAnalysisAttachment` leaves every stored HIV report's
canonical bytes untouched. The file already establishes this pattern:
`MaterializationReportContent.evidence` is
`tuple[MaterializedEvidenceArtifactEntry | MaterializedEvidenceRecord, ...]`
([materialization_schemas.py:278](../apps/api/app/corpus_steward/materialization_schemas.py#L278)).

Concretely, C means:

- `MaterializationReportContent.authority_binding: SignedAuthorityBinding | SignedNarrativeAuthorityBinding`
- `MaterializationReportContent.structural_mapping: StructuralMappingAttachment | NarrativeAnalysisAttachment`
- A new sibling `NarrativeAuthorityBindingContent` — the fields of
  `AuthorityBindingContent` minus `structured_report_sha256` and `structured_companion_id`,
  plus `narrative_analysis_sha256`; `assets` `min_length` 1; the "exactly one companion"
  validator replaced by "zero companions, every asset a controller".
- `AuthorityBindingContent` is **frozen**. Not one field added.

Because `CanonicalModel` sets `extra="forbid"`, the two binding members are unambiguously
discriminable by their disjoint required fields under smart-union validation. Both halves
of this were checked against the installed pydantic 2.13.4: a `None`-defaulted field does
appear in `model_dump(mode="json")`, and widening a field to a union leaves the existing
member's dump byte-identical. Still pin it in a test rather than trusting the check:

> **Land this first, before any other change:** a regression test that pins the canonical
> digest of a stored HIV `SignedAuthorityBinding` and `MaterializationReport` fixture and
> asserts round-trip `model_validate` → `canonical_sha256` equality. It is the guardrail for
> every step below.

### The guardrail that landed

`apps/api/tests/unit/test_steward_digest_history.py`, with fixtures under
`apps/api/tests/fixtures/steward_history/`. It works on three levels:

- **Byte-frozen real artifacts.** The `SignedAuthorityBinding` (7,222 B) and two
  `MaterializedEvidenceRecord`s — one PDF-anchored, one `TABLE_CELL`-anchored — were copied
  verbatim out of the content-addressed artifact store for run
  `MAT_213e6d22e22dc92bd429920e87a2beef`. Because the store names every blob by the SHA-256
  of its own bytes, the fixtures carry self-verifying provenance, and no application code
  regenerates them. The assertion is the strongest available: `canonical_json_bytes(model)`
  must equal the original bytes exactly, not merely digest to the same value.
- **The 53 MB materialization report, verified in place.** Too large to vendor, so its
  digest is pinned as a literal and checked against the real blob whenever the local
  artifact store is present (`MED_RAG_STEWARD_ARTIFACT_STORE` overrides the path); the test
  skips with an explicit reason otherwise. **It passes today** — the stored report validates
  and round-trips byte-identical under the current schema, which is what makes option C
  viable rather than merely attractive.
- **Pinned field-name sets** for all fifteen content models reachable from a signed
  envelope, including the ones the vendored fixtures cannot reach
  (`MaterializationReportContent`, `EvidenceCoverageReport`, `CorpusReleaseCandidateContent`,
  …). These run in every environment with no fixtures at all, and they are what catches an
  added field immediately rather than at the next materialization.

Verified by mutation: injecting `narrative_analysis_sha256: str | None = None` into
`AuthorityBindingContent` — the exact shape of change option C forbids — fails four tests,
including the real stored binding and the real 53 MB report.

## Run identity: two materializers, no cascade

The first draft of this note treated per-item run identity as a fix to `_run_id` and
therefore as a change that would move the HIV run id. That dilemma only exists if the
narrative path reuses the structured path's run-id derivation. It should not.

**The narrative materializer gets its own name, its own version, and its own run-id
function that includes `item_id` from the start.** `materializer_name` widens from
`Literal[MATERIALIZER_NAME]` to a union of literals, which under option C dumps identically
for existing content, so this stays inside the chosen strategy rather than needing a second
one. HIV's structured run keeps byte-identical ids; multi-item narrative candidates are
correct on day one; nothing cascades into `evidence_id`, QA batches, canonical evidence,
release membership, or benchmark adjudications.

Blocker 4 then stays latent on the structured path, where multi-item candidates do not
exist. **Do not "fix" `_run_id` for the structured path.** Add a guard that raises if a
structured candidate ever carries more than one included item, and record it as a known
limit. That converts a silent wrong answer into a loud one without touching signed history.

## Every site that must widen with the schema

Type-checking does not propagate the union to code that parses a stored JSON column back
into a concrete model. Five sites, all in the same step as the schema change:

| Site | Why |
| --- | --- |
| [materialization_repository.py:155](../apps/api/app/corpus_steward/materialization_repository.py#L155) | `binding: SignedAuthorityBinding = content.authority_binding` annotation |
| [materialization_repository.py:170](../apps/api/app/corpus_steward/materialization_repository.py#L170) | reads `content.structural_mapping.structured_run_id`, which narrative analysis will not have |
| [qa_repository.py:63](../apps/api/app/corpus_steward/qa_repository.py#L63) | `QACandidateContext.authority_binding` annotation |
| [qa_repository.py:124](../apps/api/app/corpus_steward/qa_repository.py#L124) | **`SignedAuthorityBinding.model_validate(run.authority_binding)`** — a hard parse of a stored JSON column into the structured member. The one site the union does not reach by type-checking. Left alone, a narrative binding enumerates, materializes, and then dies at QA with a confusing validation error. |
| [materialization_service.py](../apps/api/app/corpus_steward/materialization_service.py) | the local binding annotation |

The `.content.assets` and `.content.licensing_trust_root_sha256` accesses in
`qa_service.py` are safe: both binding members carry those fields.

## Work items

Ordered; each is independently reviewable.

1. **Digest regression fixture.** *(Done — `apps/api/tests/unit/test_steward_digest_history.py`.)*
   No behaviour change.
2. **Narrative binding member and the five widen sites** — `NarrativeAuthorityBindingContent`,
   `NarrativeAnalysisAttachment`, the `materializer_name` literal union, and the table above.
   `AuthorityBindingContent` is not touched.
3. **Structured multi-item guard.** Raise if a structured candidate carries more than one
   included item; record the known limit. No change to `_run_id` on that path.
4. **Narrative-anchored input closure.** A mode selected by trust-root config (proposed
   `source_topology: "NARRATIVE_ANCHORED"`, absent ⇒ today's DAK behaviour): skip FHIR
   parsing and the dependency registry, and synthesize the `ResolvedNarrativeArtifact` from
   the already-preserved source artifact — `asset_id = item_id`, `configured_url`/`final_url`
   from the candidate's `SourceArtifactReference`, no refetch. The closure schema needs no
   change: `direct_dependencies`, `requirements` and `dependency_packages` already default
   to `()`, and `verify_closure` already tolerates a null dependency digest.
5. **Narrative source analysis stage** — schemas, service, repository, migration, predicate.
6. **Artifact-kind check by asset.** `artifact_storage_keys` takes an expected kind per
   asset rather than a blanket `NARRATIVE_SOURCE` requirement, so a source-anchored
   narrative can be read as `SOURCE`.
7. **Materialization narrative branch** — the narrative materializer name/version/run-id
   function, the unit-inventory recomputation check,
   `materialization_runs.structured_run_id` made nullable alongside a new `narrative_run_id`
   and `inventory_item_id`, and the
   `Phase 3 currently requires one controlling DAK source set` restriction
   ([:604](../apps/api/app/corpus_steward/materialization_service.py#L604)) scoped to the DAK
   mode.
8. **CLI** — `analyze-narrative <candidate_id> --item-id`, mirroring `process-structured`.
9. **Enable the WHO NCD trust root** and run all 13 items end to end.

## Decisions

Accepted 2026-08-27.

1. **Digest strategy: option C.** Never add a field to `AuthorityBindingContent`; widen by
   union. B is disqualified — changing `_run_id` cascades into every `evidence_id` and from
   there into QA batches, canonical evidence, release membership, and benchmark
   adjudications, which would void the accepted development report.
2. **`asset_kind`: document the collision, do not add `NARRATIVE_INVENTORY_SOURCE`.** The
   two vocabularies describe different axes — `LicensedAssetKind` is *what the content is*,
   `ArtifactKind` is *how it was acquired*. Their meeting on one artifact is a fact about
   the WHO topology, not a modelling error. A WHO guideline PDF is legitimately both
   `LicensedAssetKind.NARRATIVE_SOURCE` and `ArtifactKind.SOURCE`. Recorded in
   [corpus-steward.md](corpus-steward.md).
3. **`render_allowed: false` on all 13: confirmed and intended.** The render licence is
   undecided project-wide and every WHO asset including HIV is already false. Nothing
   user-facing may depend on this corpus.

## Cross-team: extraction granularity is not solved here

`DAKSourceExtractor` already handles `application/pdf` and emits page-level units with bbox
anchors, so nothing blocks extraction. Passage presentation and near-duplicate suppression
belong to a separate workstream, and this note does not solve them. It does owe that work a
specific prediction, because the DAK findings in [roadmap.md](roadmap.md) were measured on
XLSX rows and the narrative corpus fails differently:

- **Unit size inverts.** An XLSX row is a few dozen characters; a guideline PDF page is
  1–4 KB of prose spanning several unrelated recommendations. Truncation and passage-level
  scoring tuned on rows will under-serve pages, and a page that matches on one paragraph
  drags in everything else on it.
- **Near-duplicates arrive from a different direction.** The DAK's duplicates are the same
  logical row repeated at different sheet offsets. The WHO NCD set's duplicates are
  *cross-document*: three cervical-cancer-screening guidelines from 2021, 2021 and 2026, and
  two lead-exposure documents where one is the executive summary of the other. Offset-based
  or within-asset dedup will not catch these; near-identical recommendation text from two
  editions of the same guideline is a supersession problem wearing a dedup costume, and
  picking the wrong edition is a clinical-safety failure, not a ranking nuisance.
- **Running headers and footers repeat on every page.** Page-level units make WHO
  boilerplate — document title, ISBN, page number — a term that occurs in every unit of a
  300-page document.
- **`content_exact` is prose here, not coordinates.** The `A145=HIV.D8` presentation defect
  does not apply to PDF units, so a presentation fix aimed only at spreadsheet coordinates
  will look complete while leaving the page-granularity problem untouched.

The first WHO NCD retrieval results should be read as a chunking and supersession finding,
not as a verdict on the materialization path.
