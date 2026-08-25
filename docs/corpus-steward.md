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

## Release-specific Qdrant construction and attestation

Index construction is a separate build-side gate. It consumes the exact promoted release
bundle plus a sealed vector batch. The vector batch must be produced by the
benchmark-controlled embedding pipeline and pins the dense and sparse model IDs, immutable
model revisions, model-artifact SHA-256 digests, declared dimensions, and one vector pair
for every approved evidence digest. The Qdrant builder does not choose or download a model.

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

BAAI/bge-m3 is the selected multilingual dense candidate. It is not accepted for clinical
retrieval until it passes the independently adjudicated benchmark. Install the optional
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
space, emits BM25 length-normalized document TF and binary query TF, and relies on the
index's required Qdrant `idf` modifier:

```powershell
corpus-steward model-artifact-manifest `
  models\artifacts\qdrant-bm25-unicode-v1 `
  --kind SPARSE `
  --model-id med-rag/qdrant-bm25-unicode `
  --revision 1.0.0 `
  --dimension 4294967296 `
  --adapter-id med-rag/qdrant-bm25 `
  --adapter-revision 1.0.0 `
  --adapter-parameters models\configs\qdrant-bm25-adapter-parameters.json `
  --output data\local\sparse-model-manifest.json
```

Before sealing a release candidate, replace `average_document_length` in the sparse
configuration with the exact average token count for that candidate's approved evidence.
Optional UTF-8 stopwords must be inside the verified sparse artifact and named by its
relative `stopwords_path`; no implicit language resources are loaded.

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
corpus-steward index-produce-vectors data\local\validated-corpus-release.json `
  --embedding-backend candidate `
  --dense-model-root models\local\bge-m3 `
  --dense-model-manifest data\local\dense-model-manifest.json `
  --dense-artifact-sha256 <approved-dense-manifest-sha256> `
  --sparse-model-root models\artifacts\qdrant-bm25-unicode-v1 `
  --sparse-model-manifest data\local\sparse-model-manifest.json `
  --sparse-artifact-sha256 <approved-sparse-manifest-sha256> `
  --device cpu `
  --batch-size 8 `
  --output data\local\phase4-index-vectors.json
```

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
  --output data\local\phase4-index-build-validation.json

corpus-steward qdrant-validate data\local\validated-corpus-release.json `
  --vectors data\local\phase4-index-vectors.json `
  --output data\local\phase4-index-validation.json
```

Before any Qdrant write, the command reconstructs the release bundle from PostgreSQL and
requires exact equality with the supplied bundle. It also reconciles the immutable QA run,
all decision rows, approved release membership, quarantined count, and bundle artifact
digest. The release must be `VALIDATED` with `index_status=NOT_BUILT`.

The builder is no-delete and safe to resume. It creates only the manifest-reserved
collection name, rejects configuration or metadata drift, validates every existing point
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
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --output data\local\phase4-index-attestation.json
```

`qdrant-attest` performs a fresh exhaustive validation, records the verified Ed25519
signature under the existing `STAGE` key purpose, writes the signed attestation, and only
then calls the release registry transition to `index_status=VALIDATED`. The attestation
binds the validation-report digest and the complete QA accounting; its statement digest is
the digest recorded on the release. The command does not activate the release. Benchmark
acceptance and a separately signed activation decision remain mandatory later gates.

The index-attestation output contract is frozen in
`packages/schemas/qdrant-index-attestation-1.0.0.schema.json`. For authenticated Qdrant,
set `QDRANT_API_KEY` or pass `--qdrant-api-key`; the local stack is pinned to Qdrant
`1.15.4`, and a different version fails closed unless the expected version is explicitly
changed after compatibility review.

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
jurisdiction, language, publisher, and source-class filters. Every returned Qdrant point
ID, evidence digest, role set, and filter payload is checked against the release before it
can enter a metric. Hybrid retrieval uses candidate-weighted reciprocal-rank fusion with
evidence-ID tie breaking. A lane failure is isolated, recorded by lane and failure class,
and blocks acceptance by default instead of corrupting another lane silently.

Reports are digest-sealed and include per-case rankings plus Recall@K, nDCG, MRR, context
precision, alternative minimum-complete-evidence-set recall, required-role recall,
forbidden-evidence leakage, insufficient-evidence accuracy, candidate failures, latency,
per-safety-stratum results, and Wilson 95% intervals. Only the suite's declared candidate
mode controls acceptance; the other modes remain explicit ablations. Exit status 8 is a
measured rejection, while release/model/candidate-pin drift fails as a command error. The
current contracts are `retrieval-benchmark-suite-1.2.0.schema.json`,
`retrieval-benchmark-report-1.2.0.schema.json`, and
`retrieval-candidate-1.0.0.schema.json` under `packages/schemas`; the frozen 1.0 and 1.1
benchmark schemas remain for audit history.

Seal a candidate configuration before sealing the suite that references its digest:

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

The acceptance binds the holdout access/process revisions, suite, report, runner,
candidate, vector batch, manifest, collection, index attestation, and release. Activation
uses the breaking `signed-activation-decision-2.0.0` contract and fails closed when that
record is missing, stale, mismatched, replayed, or tampered. This gate does not make the
synthetic suite clinically acceptable: independently adjudicated development and untouched
holdout evidence, terminology/safety lanes, specialist candidates, and reranking
experiments are still outstanding.
