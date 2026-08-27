# Corpus steward and inventory reconciliation

The corpus steward is a build-side process. It does not run inside question requests and
does not inherit authority to approve its own evidence. The initial implementation reuses
the API package's shared Python domain models while exposing a separate
`app.corpus_steward.cli` process entry point.

## Frozen contract seam

Contract version `1.0.0` is defined in `apps/api/app/schemas/corpus.py` and exported to
`packages/schemas/corpus-release-bundle-1.0.0.schema.json`. Activation is separately
frozen in `packages/schemas/signed-activation-decision-2.0.0.schema.json`. The synthetic
fixture at `data/fixtures/corpus-release-v1.json` contains three structure-aware evidence
records: primary support, applicability, and exception/contraindication.

The manifest digest covers its cutoff, trust-root and policy digests, inventory
reconciliations, exception register, evidence digests, attestation references, and
release-specific Qdrant collection name. Each evidence digest covers the exact and search
views, source identity, lifecycle, roles, source anchors, and deterministic verification
results.

Validate the fixture:

```powershell
.\.venv\Scripts\python -m app.corpus_steward.cli validate-bundle `
  data/fixtures/corpus-release-v1.json --require-activatable
```

Regenerate the JSON Schema after an intentional versioned contract change:

```powershell
.\.venv\Scripts\python -m app.corpus_steward.cli export-schema `
  packages/schemas/corpus-release-bundle-1.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-activation-schema `
  packages/schemas/signed-activation-decision-2.0.0.schema.json
```

## Persistence and activation

Migrations `0003_corpus_releases` and `0004_index_attestations` add:

- immutable manifest and canonical evidence payloads;
- release-to-evidence membership and signed exception records;
- release-specific index validation state;
- exactly one active-release pointer;
- an outbox written in the same transaction as release state changes.

`SQLCorpusReleaseRepository` registers a bundle idempotently, rejects evidence-ID or
manifest-ID reuse with different content, checks every evidence record against the source
registry and original artifact digest, and refuses activation until the release contract
and release-specific index attestation are validated. The signed decision contract binds
the release manifest, policy, Qdrant collection, point count, and index-attestation digest.
Activation atomically supersedes the old release, moves the pointer, and emits its outbox
event.

Activation decisions now resolve their detached signatures through the PostgreSQL signing
key and cryptographic-attestation registry and verify Ed25519 signatures before the active
pointer can move. Production activation must still remain disabled until workload identity,
the real Qdrant build/validation activity, and production key policy are connected.

## Phase 1 reconciliation pipeline

Migration `0005_inventory_reconciliation` adds:

- trust roots with publisher, domain, jurisdiction, product-family, licensing, polling,
  connector-version, and trusted-stage-key policy;
- Ed25519 public keys and immutable, verified detached attestations;
- idempotent jobs, durable attempts, and resumable stage outputs;
- content-addressed inventory responses, inventory manifests, source packages, and
  release-candidate artifacts;
- canonical inventory items, dispositions, blockers, signed exceptions, and immutable
  reconciliation candidates.

The five stages are `ENUMERATE_INVENTORY`, `DIFF_INVENTORY`, `FETCH_ARTIFACTS`,
`RECONCILE`, and `RELEASE_CANDIDATE`. A stage is marked complete only after its canonical
input/output digest statement is signed and verified against an enabled key registered for
the `STAGE` purpose. Failed attempts resume at the first incomplete stage. A completed,
blocked poll is immutable; use a new idempotency key after the publisher or exception state
changes.

The connector SDK supplies bounded HTTPS fetching, publisher-domain and SSRF checks,
redirect limits, pagination-loop detection, response-size limits, and ETag/Last-Modified
conditions. The deterministic connector exercises pagination, drift, 304 reuse, and
failures. The first real connector reads the official WHO SMART HIV FHIR
`package-list.json`, restricts the trust-root scope to its current release, preserves that
raw JSON, and acquires its published `package.tgz`. CI builds are deliberately excluded by
the checked-in trust-root policy.

### `who-guidelines-hub` connector

The second real connector reads the official WHO publications OData catalogue filtered by
the guidelines publishing office — the 358 records WHO renders as its own guidelines list,
vetted by the Guidelines Review Committee. WHO defines the population; the connector does
not decide what counts as a guideline.

Three behaviours are deliberate and should not be "simplified" away:

- **Pagination orders by `Id`, never by date.** Publication dates are neither unique nor
  stable enough to page against, and a shifting sort silently drops or repeats records.
- **`DownloadUrl` is never bound to `artifact_url`.** Its population depends on query sort
  order (measured 2026-08-27: 198 populated under `$orderby=Id`, 256 under
  `$orderby=PublicationDateAndTime desc`, same `$select`). Binding it would make the
  inventory fingerprint depend on query shape and report phantom drift on every
  re-reconciliation. Resolution always goes through the record's IRIS handle, which is
  deterministic and yields a publisher checksum; the hub URL is retained only as a
  cross-check and a divergence is recorded rather than silently resolved in IRIS's favour.
- **Structural blockers are recorded, not raised.** A record the publisher lists but does
  not make retrievable stays in the inventory carrying an `acquisition_blocker`, so
  complete-inventory accounting still sees it. Transport and JSON faults still raise:
  those are retryable, and turning one into a permanent exception would drop a record the
  publisher does list.

`scope_item_ids` in the trust-root config records exactly which catalogue records the
trust root claims. The complete catalogue count is preserved in the raw inventory
responses, so scope narrowing is auditable rather than silent — filtering during
enumeration would make "not in scope" indistinguishable from "the connector missed it".

`data/trust-roots/who-guidelines-ncd.json` is an authored expansion candidate held at
`enabled: false`. It must not be reconciled until narrative-only materialization exists:
PDF-only sources have no structured report, and `MaterializationService.materialize`
requires one. See [narrative-only-materialization.md](narrative-only-materialization.md)
for the full set of blockers and the accepted design.

**Two asset vocabularies meet on one artifact here, and that is correct.** In this trust
root every `asset_licensing` entry declares `LicensedAssetKind.NARRATIVE_SOURCE` while the
same bytes are preserved by reconciliation as `ArtifactKind.SOURCE`. They are not competing
labels for one property: `LicensedAssetKind` records *what the content is*, `ArtifactKind`
records *how it was acquired*. On the WHO SMART HIV path they happen to line up, because
the narratives are side-channel assets fetched separately from the FHIR package that is the
inventory source. On a narrative-anchored publisher the inventory source **is** the
controlling clinical narrative — `asset_id` in `asset_licensing` is the `item_id` from the
connector — so one artifact carries both. Do not reconcile the two enums or add a
`NARRATIVE_INVENTORY_SOURCE` member to paper over the overlap; the overlap is a fact about
the topology.

Every current official item must resolve to an immutable source artifact or an unexpired
exception whose detached Ed25519 signature verifies under a key registered for the
`EXCEPTION` purpose. Otherwise the command records an item-specific blocker, returns exit
status 4, and does not produce a candidate. A successful candidate is an acquisition
release candidate for later extraction and evidence verification; it is not yet an
activatable corpus release.

## Phase 2 structured-source processing

Migration `0006_structured_fhir_packages` adds immutable trust-root revisions, structured
package runs, per-resource FHIR inventory rows, controlling-narrative link diagnostics,
and content-addressed structured reports. A processor always starts from an included
artifact in a signed reconciliation candidate. It never reacquires a package from a URL,
and it resolves the exact trust-root digest bound by that candidate rather than silently
using a newer registry definition.

The first native processor handles FHIR NPM `package.tgz` artifacts without extracting
them to disk. It rejects unsafe paths, links and special members, duplicate paths, nested
archives, duplicate JSON keys, invalid UTF-8, excessive JSON depth, and configured archive
expansion limits. It then binds `package.json`, the root `ImplementationGuide`, and every
FHIR resource identity; checks declared-resource coverage; records content and narrative
digests; and evaluates FHIR version, lifecycle, license, narrative-authority, and package
dependency policy.

Every result, including malformed or policy-blocked input, is preserved as a canonical
report and verified Ed25519 stage attestation. A blocked package may retain a complete
structural inventory for diagnostics, but it is never marked promotion-eligible. Exit
status 5 means structured processing completed and recorded blockers; exit status 2 means
the command itself could not run safely.

Migration `0007_structured_input_closures` adds immutable, signed input-closure runs for
structured packages. Each run is bound to the reconciliation candidate, its historical
trust-root digest, inventory item, and source artifact. The resolver independently
preserves every configured controlling-narrative asset plus strict registry metadata and
tarballs for the complete transitive FHIR NPM dependency graph. Exact versions and
bounded `major.minor.x` ranges are deterministic; traversal, unsafe members, identity or
registry-digest mismatches, cycles, depth/size/package limits, and incomplete acquisition
all produce a signed blocked report.

Dependency packages are inspected in manifest-only mode: every tar header still counts
toward path, member-type, entry, total expansion, and compression-ratio limits, while only
`package/package.json` is opened as JSON. This permits official core/example package
layouts and opaque nested templates without extracting or trusting their contents.
Multiple explicitly pinned versions are retained as separate immutable graph nodes rather
than collapsed or silently upgraded. The structured processor cryptographically verifies
the closure attestation and exact digest before its narrative-asset and dependency gates
can pass. Exit status 6 means input resolution completed and recorded blockers.

## Operator flow

Apply migrations, generate or provision a stage key, register only its public half, and
register a trust root:

```powershell
corpus-steward keygen `
  --private-key data\local\keys\stage-private.pem `
  --public-key data\local\keys\stage-public.pem
corpus-steward register-key `
  --public-key data\local\keys\stage-public.pem `
  --key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --purpose STAGE
corpus-steward register-trust-root data\fixtures\trust-root-synthetic.json
```

Then run the concrete milestone:

```powershell
corpus-steward reconcile SYNTHETIC `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --output data\local\synthetic-reconciliation.json
```

When `--idempotency-key` is omitted, manual polls use one stable UTC-day key. Supply an
explicit scheduler run ID in production. The WHO definition is at
`data/trust-roots/who-smart-hiv.json`; its command target is `WHO_SMART_HIV`.

Resolve the authoritative inputs for a successfully reconciled one-item candidate, then
process that same immutable candidate:

```powershell
corpus-steward resolve-structured-inputs RC_<candidate-id> `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --output data\local\structured-input-closure.json
corpus-steward process-structured RC_<candidate-id> `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --output data\local\structured-package-report.json
```

Use `--item-id` with both commands when a candidate includes multiple source artifacts.
The current WHO
policy classifies the SMART HIV FHIR release as a structured companion to the controlling
WHO HIV DAK narrative. It also requires non-experimental status, matching license
declarations, explicit narrative linkage, five preserved DAK assets, and a reconciled
dependency closure before promotion. The checked-in policy uses the primary FHIR package
registry metadata and version endpoints; all acquired bytes are additionally addressed by
SHA-256 in the steward artifact store.

Exception keys should be separate from stage and activation keys. Register an exception
key for the `EXCEPTION` purpose, create a `CoverageExceptionContent` JSON file, and use
`corpus-steward approve-exception`. Exceptions never expand inventory scope and do not
convert failed acquisitions into source artifacts.

## Phase 3 authority composition and evidence materialization

Migration `0008_authority_materialization` adds immutable materialization runs and
source-unit evidence rows. New trust-root revisions use `asset_licensing`; the legacy
single `licensing_policy` remains readable only for historical signed revisions and is
rejected by materialization. Each source asset independently declares acquisition,
evidence-materialization, redistribution, and rendering permissions.

`corpus-steward materialize` verifies the signed input closure and structured-package
attestation, signs an authority binding, and assigns two non-interchangeable roles. A
historical acquisition candidate may use the current per-asset licensing revision only
when publisher, connector, and every controlling asset identity are unchanged; the signed
binding records both trust-root digests.

- DAK PDF/XLSX assets are `CONTROLLING_CLINICAL_SOURCE` inputs and may produce evidence;
- SMART FHIR is a `STRUCTURED_COMPANION`, limited to `STRUCTURAL_MAPPING_ONLY`, and its
  clinical content is never included—even when an experimental package is accepted.

PDF extraction accounts for every page and records page/block bounding boxes. XLSX
extraction accounts for every worksheet row and records exact sheet/row/column anchors.
Empty source units are explicitly counted. A candidate is produced only when every
configured DAK asset and source unit is accounted for, every asset permits evidence
materialization, and at least one evidence record exists.

Run the milestone after input resolution and structured processing:

```powershell
corpus-steward materialize RC_be174c24ccfbcecf332a58c93ae4ea02 `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --output data\local\corpus-materialization-result.json
```

Exit status 7 means materialization completed but licensing or evidence coverage blocked
the corpus candidate. A successful result is signed and marked `qa_required=true` and
`retrieval_eligible=false`; evidence must pass the separate QA command below, and
activation, indexing, and retrieval remain later gates.
Candidates created under a legacy global license remain immutable. Before materializing
one, register the updated per-asset trust root with `--replace`; the materializer treats it
as a narrowly validated licensing overlay rather than silently changing acquisition
provenance.

## Evidence QA and corpus promotion

Migration `0009_evidence_qa_promotion` adds the immutable Phase 4 QA ledger, and
`0010_automated_evidence_qa` removes its external decision gate. Materialized evidence
remains in one content-addressed artifact per source unit; new materialization reports
carry compact artifact entries instead of embedding every evidence payload. The historical
Phase 3 reports remain immutable and readable.

Run QA to replay every PDF page/bounding-box or XLSX sheet/row/cell anchor against the
preserved narrative artifact and classify the complete evidence set:

```powershell
corpus-steward qa CRC_e4a186e56c89dab138785ea32b6658f1 `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --bundle-output data\local\validated-corpus-release.json
```

The command writes a signed content-addressed evidence manifest, records every replay,
runs the versioned deterministic classifier, and seals one decision for every materialized
record. Each decision binds the materialized evidence digest and either:

- approve with one or more clinical roles plus applicability and optional publisher grade;
- quarantine with a reason such as anchor failure, extraction-structure failure, header,
  duplicate, non-evidence content, or unresolved clinical classification.

The classifier is fail-closed. Failed replays, unstable extraction structures, headers,
administrative content, duplicate exact content, and unresolved source classifications are
quarantined. It assigns evidence roles and explicit applicability, threshold, exception,
monitoring, and WHO GRADE metadata only when the source class and content rules support
them. No operator decisions or editable decision artifacts are accepted by the QA command.

The signed batch must account for every materialized record. Approved records become
canonical `CorpusEvidenceRecord` artifacts; quarantined records remain in the immutable QA
ledger but never enter release membership. The command signs inventory reconciliation,
evidence verification, and release-policy attestations, then registers a `VALIDATED`
release with a reserved release-specific collection name and `index_status=NOT_BUILT`.
Index construction and activation remain separate later gates.

## Candidate-specific Qdrant construction and attestation

Index construction is a separate build-side gate. It consumes the exact promoted release
bundle plus a sealed vector batch. The vector batch must be produced by the
benchmark-controlled embedding pipeline and pins the dense and sparse model IDs, immutable
model revisions, model-artifact SHA-256 digests, declared dimensions, and one vector pair
for every approved evidence digest. A candidate vector-profile digest covers the release,
vector names, model pins, and adapter pins. Its collection name is the release-reserved name
plus `--vp-<digest-prefix>`, so model families cannot share or collide with one another while
fusion weights, expansion revisions, and reranker-only variants can reuse identical vectors.
The Qdrant builder does not choose or download a model.

The Python producer exposes an `EmbeddingBackend` boundary for pinned candidate adapters.
Document and query encoding are separate operations so dense retrieval and asymmetric
sparse/BM25-style candidates do not have to pretend their query and document weights are
identical. `ProductionEmbeddingBackend` composes one dense and one sparse adapter only
after both local artifact trees have been exhaustively verified. It enforces a hard batch
ceiling and per-call timeout, retries only failures explicitly classified as transient,
caps attempts at five, and validates output counts, dimensions, sparse bounds, and finite
float32 values before returning material to the producer.

Model artifacts have a separate sealed contract. The manifest binds model identity and
revision, adapter implementation and revision, every behavior-affecting adapter parameter,
declared vector dimension, exact relative file inventory, byte sizes, and per-file SHA-256
digests. Create the manifest after placing an already acquired model in a local directory;
the output must be outside that directory:

```powershell
corpus-steward model-artifact-manifest models\local\bge-m3 `
  --kind DENSE `
  --model-id BAAI/bge-m3 `
  --revision 5617a9f61b028005a4858fdac845db406aefb181 `
  --dimension 1024 `
  --adapter-id med-rag/bge-m3-transformers `
  --adapter-revision 1.0.0 `
  --adapter-parameters models\configs\bge-m3-adapter-parameters.json `
  --output data\local\dense-model-manifest.json
```

BAAI/bge-m3 is the multilingual dense control, not the selected engineering candidate. The
current available engineering floor is the separately pinned Qwen3-Embedding-0.6B artifact;
neither is accepted for engineering release until a complete frozen candidate passes the
automated source-derived benchmark. Install the optional
runtime with `pip install -e ".[retrieval]"` from `apps/api`, place a dereferenced,
already-acquired BGE-M3 snapshot at the root above. The selected candidate revision is
`5617a9f61b028005a4858fdac845db406aefb181`; the adapter rejects another revision.
The adapter calls Transformers with `local_files_only=True` and
`trust_remote_code=False`; runtime inference cannot download missing model or tokenizer
files. Pooling, normalization, prefixes, truncation, micro-batch size, device, and dtype
are all mandatory sealed manifest parameters. `--device`, when supplied to a runtime
command, is an assertion against that sealed device rather than an untracked override.

The sparse candidate is dependency-free. It tokenizes NFKC/casefolded Unicode medical
text, adds CJK character/bigram terms, hashes into the declared unsigned 32-bit term
identifier space (the current release artifact bounds it to 262,144 buckets), emits BM25
length-normalized document TF and binary query TF, and relies on the
index's required Qdrant `idf` modifier:

```powershell
corpus-steward model-artifact-manifest `
  models\artifacts\qdrant-bm25-unicode-v1 `
  --kind SPARSE `
  --model-id med-rag/qdrant-bm25-unicode `
  --revision 1.0.0 `
  --dimension 262144 `
  --adapter-id med-rag/qdrant-bm25 `
  --adapter-revision 1.0.0 `
  --adapter-parameters models\configs\qdrant-bm25-adapter-parameters.json `
  --output data\local\sparse-model-manifest.json
```

Before sealing a release candidate, replace `average_document_length` in the sparse
configuration with the exact average token count for that candidate's approved evidence.
Optional UTF-8 stopwords must be inside the verified sparse artifact and named by its
relative `stopwords_path`; no implicit language resources are loaded.

Derive that value from the exact validated release instead of estimating it from a sample:

```powershell
corpus-steward bm25-release-statistics `
  data\local\validated-who-smart-hiv-release.json `
  --adapter-parameters `
    models\configs\qdrant-bm25-who-smart-hiv-v1-parameters.json `
  --output models\configs\who-smart-hiv-bm25-release-statistics-v1.json
```

The current sealed WHO SMART HIV statistics bind release
`CR_b6155a25415b25f3ba787b3b036da10d` and manifest `c233322a...ce98c`: 5,145
documents, average length `86.36793002915452`, p95 length `204`, and statistics digest
`14a388...dac8`, using the exact `unicode-medical-v1` tokenizer contract.

Qwen runtime evidence is generated only from a development suite and verified local model
artifacts. The matrix request pins each model commit, dimension, artifact path/digest, device,
dtype, maximum length, and batch size. Missing artifacts are recorded as blockers rather than
downloaded implicitly or assigned synthetic measurements:

```powershell
corpus-steward qwen-runtime-matrix `
  models\configs\qwen3-embedding-runtime-matrix-v1.json `
  --bundle data\local\validated-who-smart-hiv-release.json `
  --suite benchmarks\suites\who-smart-hiv-source-derived-development-v7.json `
  --workspace-root . `
  --output data\local\qwen3-embedding-runtime-matrix-v1-report.json
```

The checked-in CPU/fp32 report measured the exact Qwen3-Embedding-0.6B artifact at commit
`97b0c614...65b3`, artifact digest `9ec38140...64b6a`, on 32 queries and 32 documents over
three timed runs. It recorded 1.47 query items/s, 0.405 document items/s, 35.76-second query
batch p95, 100.84-second document batch p95, 5,772,599,296-byte peak process RSS, zero
truncations, and maximum unit-norm deviation `2.22e-15`. The 4B and 8B entries remain
explicitly blocked because no verified local artifact or independent artifact pin is available
on this runtime; that is not a performance result. The digest-sealed report is stored under
`benchmarks/runtime/` and did not access the sealed holdout.

The deployment-floor reranker is separately pinned to
`Qwen/Qwen3-Reranker-0.6B` commit `e1775d95...c42af`. Its verified-local manifest declares
artifact kind `RERANKER`, scalar score dimension 1, adapter revision 1.1.0, CPU dynamic-int8,
maximum prompt length 256, micro-batch size 50, the exact clinical instruction, and artifact
digest `815e08f7...43b61`. Runtime scoring follows Qwen's causal-LM prompt contract and the
softmax probability of the `yes` token against `no`; it does not load a sequence-classification
head or download runtime code. The CPU pin was selected from local worst-case passage probes:
20 documents at 256 tokens took 15.57 seconds under dynamic int8, while fp32 at 512 tokens took
57.05 seconds; int8 batch 50 remained below the runtime memory limit while batch 100 did not.

Record the printed `artifact_sha256` in independently controlled deployment or acceptance
configuration. Every production load must supply that external pin; validating only a
self-consistent manifest is insufficient:

```powershell
corpus-steward model-artifact-verify models\local\bge-m3 `
  --manifest data\local\dense-model-manifest.json `
  --expected-artifact-sha256 <approved-64-character-sha256>
```

Verification is local-only and never downloads missing files. It rejects symlinks, Windows
reparse points, special files, non-portable or case-colliding paths, missing or unexpected
files, content drift, and configured file-count or byte-limit overruns. Production adapter
implementations receive a `VerifiedModelArtifact`; its reference is the model pin sealed
into vector batches and compared again by the benchmark runner.

The producer batches `content_search` in evidence-ID order, rejects incomplete backend
output, float32-quantizes vectors, binds each vector to the canonical evidence digest, and
seals the complete batch. A dependency-free deterministic hashing backend remains
available to test the build and benchmark plumbing:

```powershell
corpus-steward index-produce-vectors data\fixtures\corpus-release-v1.json `
  --dense-dimension 384 `
  --sparse-dimension 262144 `
  --output data\local\synthetic-index-vectors.json
```

That backend is a lexical baseline, not an accepted biomedical semantic model. A
production adapter must supply immutable model identity, revision, and artifact digest
through the same boundary. Select the verified local adapters for production vector
generation (the two expected digests must come from independent acceptance or deployment
configuration):

```powershell
corpus-steward index-produce-vectors data\local\validated-who-smart-hiv-release.json `
  --embedding-backend candidate `
  --dense-model-root models\local\qwen3-embedding-0.6b `
  --dense-model-manifest data\local\model-manifests\qwen3-embedding-0.6b-cpu-float32.json `
  --dense-artifact-sha256 9ec38140d99f44343a3cdc19a0fc249843e37b86764fd411e168487b2d364b6a `
  --sparse-model-root models\artifacts\qdrant-bm25-unicode-v1 `
  --sparse-model-manifest data\local\model-manifests\qdrant-bm25-who-smart-hiv-v1.json `
  --sparse-artifact-sha256 126656d608bca79f24cde2e5707b55fae76f6b15ddc9ee9078788cf6d9261000 `
  --device cpu `
  --batch-size 8 `
  --checkpoint data\local\qwen3-0.6b-release-vectors.checkpoint.json `
  --output data\local\qwen3-0.6b-release-vectors.json
```

The checkpoint is an atomic, digest-sealed exact-prefix snapshot. Resume verifies the release,
model definitions, evidence order, evidence digests, and checkpoint digest before continuing;
an interrupted multi-hour production run never accepts a hole or stale vector prefix. By
default it is persisted every ten completed batches and after the final batch; use
`--checkpoint-interval-batches` to select another positive durability cadence.

An external producer may instead emit
`IndexVectorBatchContent` JSON and seal it after generation:

```powershell
corpus-steward index-seal-vectors data\local\phase4-index-vector-content.json `
  --output data\local\phase4-index-vectors.json
```

The shape of the sealed artifact is frozen in
`packages/schemas/qdrant-vector-batch-1.0.0.schema.json`. Sparse indices must be unique,
strictly increasing, and smaller than the declared sparse dimension. Dense vectors must
exactly match the declared dense dimension. All values must be finite; model identity and
artifact digests are mandatory.

Build the reserved collection, then run the independent read-only validation command:

```powershell
corpus-steward qdrant-build data\local\validated-corpus-release.json `
  --vectors data\local\phase4-index-vectors.json `
  --candidate models\configs\candidate.json `
  --output data\local\phase4-index-build-validation.json

corpus-steward qdrant-validate data\local\validated-corpus-release.json `
  --vectors data\local\phase4-index-vectors.json `
  --candidate models\configs\candidate.json `
  --output data\local\phase4-index-validation.json
```

Before any Qdrant write, the command reconstructs the release bundle from PostgreSQL and
requires exact equality with the supplied bundle. It also reconciles the immutable QA run,
all decision rows, approved release membership, quarantined count, and bundle artifact
digest. The release must be `VALIDATED`. Multiple engineering vector profiles may be built
without mutating or overwriting an earlier profile; final attestation and activation remain
separate frozen-candidate gates.

The builder is no-delete and safe to resume. It creates only the manifest-reserved
candidate-profile collection name, rejects configuration or metadata drift, validates every existing point
before resuming a partial build, and never overwrites an existing point. Each approved
canonical evidence ID maps to a deterministic UUIDv5 point ID. Qdrant payloads contain
filter/provenance metadata and digests, not exact evidence text; serving must resolve final
evidence from PostgreSQL.

Validation scrolls the complete collection with payloads and vectors and fails on any
missing, duplicate, unexpected, malformed, digest-drifted, or non-approved point. It
requires the pinned Qdrant version, exact named dense/sparse configuration, IDF sparse
modifier, exact keyword payload indexes, point count, dimensions, stable point mapping,
and payload/evidence/vector set digests. Deterministic samples must retrieve themselves
through both dense and sparse queries under release and approval filters. These are
structural smoke tests, not benchmark acceptance.

After reviewing the validation output, revalidate, sign, and register the index:

```powershell
corpus-steward qdrant-attest data\local\validated-corpus-release.json `
  --vectors data\local\phase4-index-vectors.json `
  --candidate models\configs\candidate.json `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --output data\local\phase4-index-attestation.json
```

`qdrant-attest` performs a fresh exhaustive validation, records the verified Ed25519
signature under the existing `STAGE` key purpose, writes the signed attestation, and only
then atomically selects that candidate-profile collection and transitions the release registry
to `index_status=VALIDATED`. A validated release cannot switch profiles. The attestation
binds the validation-report digest and the complete QA accounting; its statement digest is
the digest recorded on the release. The command does not activate the release. Benchmark
acceptance and a separately signed activation decision remain mandatory later gates.

The index-attestation output contract is frozen in
`packages/schemas/qdrant-index-attestation-1.0.0.schema.json`. For authenticated Qdrant,
set `QDRANT_API_KEY` or pass `--qdrant-api-key`; the local stack is pinned to Qdrant
`1.15.4`, and a different version fails closed unless the expected version is explicitly
changed after compatibility review.

## Automated source-derived benchmark construction

Migration `0013_automated_source_benchmarks` adds immutable generation-policy, generation-
record, suite-build, access-event, and execution ledgers. Benchmark contract 1.3 identifies
these suites with `AUTOMATED_SOURCE_DERIVED` provenance. Every generated case binds its
canonical source-evidence IDs and digests, derivation rule/version, and one secret-ranked
partition-assignment digest. No reviewer decision or human approval is required.

The threshold policy sets development and holdout sample targets and safety-stratum gates for
every `ClinicalSafetyTopic`. The access policy separates candidate engineering from holdout
custody. The generation policy pins the implementation rules and only the SHA-256 digest of a
32-byte partition seed; the seed itself remains outside source control.

```powershell
corpus-steward benchmark-register-access-policy `
  benchmarks\policies\automated-access-v1.content.json `
  --output benchmarks\policies\automated-access-v1.json
corpus-steward benchmark-register-threshold-policy `
  benchmarks\policies\automated-thresholds-v1.content.json `
  --output benchmarks\policies\automated-thresholds-v1.json
corpus-steward benchmark-generate-partition-seed `
  --output data\local\benchmark-source-derived\partition-seed.bin
corpus-steward benchmark-seal-generation-policy `
  benchmarks\policies\automated-generation-v1.content.json `
  --output benchmarks\policies\automated-generation-v1.json
```

Generate and register both partitions in one deterministic transaction. Export only the
development suite into the engineering workspace. Omitting the holdout and generation-record
output arguments keeps their case payloads in custody-controlled immutable storage:

```powershell
corpus-steward benchmark-generate-source-derived `
  data\local\validated-who-smart-hiv-release.json `
  --access-policy benchmarks\policies\automated-access-v1.json `
  --threshold-policy benchmarks\policies\automated-thresholds-v1.json `
  --generation-policy benchmarks\policies\automated-generation-v1.json `
  --request benchmarks\policies\automated-generation-v1.request.json `
  --partition-seed data\local\benchmark-source-derived\partition-seed.bin `
  --development-output `
    benchmarks\suites\who-smart-hiv-source-derived-development-v7.json
```

The generator creates source-derived positives, filter-constrained negatives, polarity-aware
conflict pairs, terminology and negation cases, and explicit jurisdiction, freshness,
publisher, and unsupported-language cases. It fails if either partition cannot meet any
prespecified topic target. Generated suites intentionally contain no candidate digest. At
execution, the ledger atomically records the final sealed candidate and vector-batch digests;
development runs may repeat, while the holdout claim is unique and remains consumed after a
completed or failed run.

The final holdout run can load the registered suite by digest without exporting its case
payload into the engineering workspace:

```powershell
corpus-steward benchmark-run data\local\validated-who-smart-hiv-release.json `
  --vectors data\restricted\final-vector-batch.json `
  --registered-suite-sha256 <sealed-holdout-suite-sha256> `
  --candidate models\configs\final-candidate.json `
  --actor-identity benchmark-automation-service `
  --embedding-backend candidate `
  --output data\restricted\sealed-holdout-report.json
```

The migration-0012 independent-review workflow remains readable for audit compatibility but
is not part of the automated engineering path and cannot block product development. Physicians
participate only in evaluation of the completed product.

## Retrieval benchmark runner

Benchmark suites are sealed and bound to one corpus release and manifest. A suite carries
graded gold evidence, the minimum complete evidence set, required clinical evidence roles,
context filters, forbidden evidence IDs, ablations, ranking parameters, candidate mode,
and acceptance thresholds. The synthetic suite is plumbing coverage only:

```powershell
corpus-steward benchmark-run data\fixtures\corpus-release-v1.json `
  --vectors data\local\synthetic-index-vectors.json `
  --suite benchmarks\suites\synthetic-v1.json `
  --candidate models\configs\synthetic-candidate-v1.json `
  --output data\local\synthetic-benchmark-report.json
```

For a real candidate vector batch, add the same `--embedding-backend candidate`,
model-root, manifest, independently pinned digest, and optional device-assertion arguments
used during vector generation. `verified-local` is equivalent and `production` is retained
only as a compatibility alias. The benchmark verifies both artifact references against the
vector batch and the sealed candidate before loading the dense runtime, preventing a
query-time model or candidate-parameter substitution.

The runner embeds every question with the exact model pins from the vector batch and fails
on any mismatch. Sparse, dense, and hybrid modes use identical release, approval,
jurisdiction, language, publisher, source-class, source-version, and lifecycle filters. Every
returned Qdrant point
ID, evidence digest, role set, and filter payload is checked against the release before it
can enter a metric. Hybrid retrieval uses candidate-weighted reciprocal-rank fusion with
evidence-ID tie breaking. A lane failure is isolated, recorded by lane and failure class,
and blocks acceptance by default instead of corrupting another lane silently.

The current candidate runner adds two bounded, versioned sparse-query lanes only to hybrid
mode: exact terminology extraction and deterministic clinical-safety variants for
applicability, contraindication, dose, monitoring, jurisdiction, and freshness. Dense-only
and BM25-only therefore remain pure ablations. Expanded fusion preserves a bounded base-
retrieval floor and available safety-role representatives. The optional Qwen3 reranker is a
verified local causal-LM adapter using the sealed instruction and yes/no probability score;
its output also preserves the retrieval floor and exception, applicability, dose, and
monitoring representatives. Reranker failure is recorded and falls back deterministically.

`clinical-safety-query-v2` is the no-reranker conflict-aware revision. It keeps each conflict
side as an independent lexical probe, adds bounded polarity/negation clauses, reserves one
representative per side, preserves primary and safety-role representatives already present in
the fused top ten, prefers at most three source versions, and suppresses 85%-overlap passages
until non-redundant candidates are exhausted. Candidate generation and selection remain
deterministic, with evidence-ID tie breaking.

Development diagnostics can be emitted without changing the benchmark-report contract. Pass
one or more `--development-trace-case-id`, `--development-trace-depth 100`, and
`--development-trace-output <path>` arguments to `benchmark-run`. The runner rejects traces
for every partition except `DEVELOPMENT`. The trace binds the suite, candidate, vector batch,
collection, and runner; it records full dense, BM25, and expansion rankings, per-gold ranks,
actual outer-RRF contributions, the final selected rank, every applied preservation rule, and
a `CANDIDATE_GENERATION_FAILURE` versus `TOP_10_SELECTION_FAILURE` classification.

Pool comparisons use newly sealed, candidate-bound development-suite variants with identical
cases and gates. With `--register`, an additive lineage ledger binds each variant to its
registered immutable development parent, candidate digest, access policy, and artifact. The
derivation command refuses non-development suites, and automated holdout contracts reject a
candidate binding, so this path cannot expose or retune the sealed holdout:

```powershell
corpus-steward benchmark-derive-development-suite `
  benchmarks\suites\who-smart-hiv-source-derived-development-v7.json `
  --candidate models\configs\who-smart-hiv-qwen3-0.6b-rerank-pool-100.json `
  --output data\local\who-smart-hiv-development-rerank-pool-100.json `
  --register `
  --actor-identity benchmark-automation-service
```

`benchmark-run --reranker-score-cache <path>` reuses exact query/document probabilities across
overlapping pool variants. The cache identity binds the verified artifact, adapter revision,
instruction digest, and maximum length; its entries and identity are digest-checked, writes are
atomic after each case, and inconsistency fails closed. Cache-assisted development latency is
the measured warm-cache runtime and must not be treated as a cold deployment-latency result.

The completed 275-case development comparison is:

| Candidate | Report SHA-256 | Recall@10 | Complete evidence | Required roles | nDCG@10 | MRR | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Pre-rerank hybrid | `2ec967fb...2b83` | 99.27% | 98.55% | 99.82% | 0.8900 | 0.8673 | 10,425 |
| Rerank pool 20 | `e14162dd...64be` | 97.45% | 96.73% | 97.88% | 0.6618 | 0.5703 | 29,268 |
| Rerank pool 50 | `6bd18729...cf7` | 97.64% | 96.73% | 98.36% | 0.6291 | 0.5249 | 41,681 |
| Rerank pool 100 | `755ee209...432b` | 97.64% | 96.73% | 98.42% | 0.6234 | 0.5162 | 36,707 |

Every reranker pool had zero execution failures and zero forbidden-evidence leakage, but each
gained complete evidence on only two cases and lost it on seven versus pre-rerank. Terminology
complete-evidence coverage fell from 100% to 80%/84%/84%; conflict coverage fell from 84% to
84%/80%/80%. The 0.6B reranker is therefore rejected for this candidate. The pre-rerank hybrid
remains the engineering leader, but it is not frozen or holdout-eligible because context
precision, CPU latency, and conflict completeness still fail development thresholds.

Reports are digest-sealed and include per-case rankings plus Recall@K, nDCG, MRR, context
precision, alternative minimum-complete-evidence-set recall, required-role recall,
forbidden-evidence leakage, insufficient-evidence accuracy, candidate failures, latency,
per-safety-stratum results, and Wilson 95% intervals. Only the suite's declared candidate
mode controls acceptance; the other modes remain explicit ablations. Exit status 8 is a
measured rejection, while release/model/candidate-pin drift fails as a command error. The
current contracts are `retrieval-benchmark-suite-1.5.0.schema.json`,
`retrieval-benchmark-report-1.5.0.schema.json`, and
`retrieval-candidate-1.0.0.schema.json` under `packages/schemas`; the frozen 1.0 through 1.4
benchmark schemas remain for audit history.

Context precision at a fixed depth cannot exceed `min(|gold|, k) / k`, so a suite whose cases
carry fewer gold records than `top_k` has a hard ceiling well below 1.0 that no retrieval stack
can cross. Contract 1.4 therefore reports `context_precision_ceiling_at_k` beside every context
precision value, and `BenchmarkSuiteContent` refuses to seal a suite whose acceptance policy
demands more context precision than the suite can produce. The achievable gate is
`r_precision`: the share of the top `min(|gold|, k)` results that are gold, which reaches 1.0
for a perfect ranking and cannot be raised by truncating the result list. Retrieval-side
context budgeting keyed on question wording was removed for exactly that reason; it inflated
context precision by reading the benchmark generator's own question templates.

### How acceptance thresholds are set

Absolute score floors do not transfer between corpora. Retrieval effectiveness varies more with
collection and topic difficulty than with system quality - BM25's own nDCG@10 across the BEIR
suite spans 0.213 to 0.789, and 0.325 to 0.665 across its four biomedical collections alone -
so a bar copied from a leaderboard or chosen by intuition measures the corpus, not the stack.
Contract 1.5 therefore restricts gates to three defensible forms and reports everything else:

- **Derived from the consumer.** `generation_context_budget` declares how many passages the
  rendering layer will actually hand its verifier, and
  `minimum_answerable_complete_evidence_at_budget_rate` gates evidence completeness at that
  depth rather than at whatever depth the benchmark happened to search. The requirement traces
  to a real failure - a required contraindication never reaching the claim gate - instead of to
  a chosen number.
- **Stated as a confidence bound.** With `require_confidence_lower_bound`, completeness gates
  compare the Wilson 95% lower bound against the threshold, not the point estimate. "The lower
  bound clears X" is a claim about the population; "the mean clears X" is a claim about these
  draws.
- **Relative to a named comparator.** `comparator_candidate_id`, `comparator_report_sha256`,
  and `comparator_answerable_complete_evidence_rate` pin a baseline measured on this same case
  set, and the candidate must match it plus `minimum_comparator_margin`. Because the comparator
  is prespecified before candidate evaluation, its number is measured first and frozen into the
  threshold policy.

R-precision is reported and ungated by default. On this suite 175 of 200 answerable cases carry
exactly one gold record, so R-precision degenerates to precision@1 - it measures whether the
single correct passage ranks first, which no part of the safety argument depends on.

Every metric is reported twice: blended across all cases, and again over the answerable cases
alone (`answerable_*`). Insufficient-evidence cases are scored by abstention and pass whenever
the release filter legitimately matches nothing, so they cost no retrieval quality to satisfy —
in the WHO SMART HIV development suite they are 100 of 300 cases. Blended means alone would let
that third of the suite mask an answerable-case regression.

Seal a candidate configuration before running a suite. Synthetic and legacy reviewed suites
bind this digest during construction; original automated source-derived suites bind it in the
execution audit record and report, while registered development derivations bind it in the
suite, derivation ledger, execution record, and report:

```powershell
corpus-steward candidate-seal models\configs\candidate.content.json `
  --output models\configs\candidate.json
```

After the frozen candidate has run exactly once against the sealed acceptance holdout, the
benchmark/release authority prepares `acceptance.content.json`, signs it, verifies the
registered key, and links it transactionally to the release:

```powershell
corpus-steward register-key `
  --public-key data\local\keys\benchmark-public.pem `
  --key-id benchmark-release-authority `
  --signer-identity benchmark-release-authority `
  --purpose BENCHMARK_ACCEPTANCE `
  --database-url $env:DATABASE_URL
corpus-steward benchmark-accept data\local\acceptance.content.json `
  --report data\local\acceptance-report.json `
  --output data\local\signed-benchmark-acceptance.json `
  --database-url $env:DATABASE_URL `
  --signing-key data\local\keys\benchmark-private.pem `
  --signing-key-id benchmark-release-authority `
  --signer-identity benchmark-release-authority
```

The acceptance binds the holdout access and provenance revisions, suite, report, runner,
candidate, vector batch, manifest, collection, index attestation, and release. Activation
uses the breaking `signed-activation-decision-2.0.0` contract and fails closed when that
record is missing, stale, mismatched, replayed, or tampered. This is an automated engineering
gate, not clinician approval or clinical validation. Candidate runtime measurements,
larger-model and specialist candidates, completed real-candidate reranker comparisons,
verified rendering, and final-product physician evaluation remain separate work.
