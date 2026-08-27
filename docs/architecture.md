# Architecture baseline

## Authority boundary

The authority boundary is the authenticated source artifact, canonical evidence object, structured clinical semantics, and deterministic safety policy. Model output is never accepted as citation metadata or source truth.

## First executable path

```text
question
  -> deterministic context preview
  -> non-clinical progress events
  -> evidence availability check
  -> abstention (until an approved corpus is configured)
```

This narrow path exercises the public async contract without implying that retrieval or verification exists before it does. Proposed clinical claims never appear in progress events.

## Authenticated ingestion boundary

```text
authenticated operator
  -> registered publisher and HTTPS domain policy
  -> size-limited acquisition with redirect revalidation
  -> PDF and optional expected-hash validation
  -> immutable content-addressed artifact
  -> PyMuPDF page/span/bounding-box extraction
  -> QA_REQUIRED or QUARANTINED (never retrieval-approved automatically)
```

Publisher domains are canonical registry records rather than request-provided trust hints.
IP literals, user information, non-HTTPS ports, cross-publisher redirects, private-network
destinations, malformed PDFs, encrypted PDFs, and hash mismatches fail closed. Successful
native extraction records exact text and coordinates but only advances a source version to
`QA_REQUIRED`; it does not grant retrieval approval.

## Backend boundaries

- `schemas`: frozen, extra-forbid API and domain contracts with canonical range,
  identity, and contradiction validation
- `lifecycle`: version DAG validation, conflict-safe record identity, and
  evidence-scope currentness
- `persistence`: async SQLAlchemy registry models backed by the versioned PostgreSQL schema
- `ingestion`: publisher policy, safe acquisition, immutable storage, layout extraction,
  trust classification, and quarantine orchestration
- `semantics`: formal tri-state eligibility/context comparison; facts not supplied
  are unknown rather than implicitly absent
- `verification`: deterministic required-check policy, duplicate-check rejection,
  per-evidence check coverage, and a fail-closed gate
- `retrieval`: one implementation of lane search and fusion, shared by the benchmark
  runner and the serving query path, plus the release binding serving must satisfy
- `reasoning`: orchestration only; it cannot override failed verification
- `api`: transport and dependency wiring

PostgreSQL is the canonical source-ingestion registry. Qdrant is a rebuildable,
release-specific index: it may be populated only from a reconciled approved release and
remains inactive until index attestation, benchmark acceptance, and signed activation.

## Corpus release boundary

The first corpus-steward slice freezes contract version `1.0.0` in both Pydantic and
checked-in JSON Schema form. A release bundle binds exact evidence payloads to its
manifest by canonical SHA-256 digests and accounts for every configured inventory item
as included or explicitly excepted.

```text
steward validate/build process
  -> immutable manifest + canonical evidence rows
  -> release-scoped Qdrant collection validation
  -> atomic PostgreSQL active-release pointer + outbox event
  -> question request pins pointer snapshot
  -> retrieval unavailable: abstain without claims
```

Candidate registration checks evidence lifecycle state and reconstructability to the
registered immutable source artifact. Activation is blocked until the contract gates and
release-specific index count and attestation pass. The pointer, release state transition,
and activation outbox event share one database transaction. Question jobs capture the
active release at submission so an activation cannot move an in-flight request to another
corpus.

## Serving retrieval boundary

A question is answered from the active release with the candidate that release was
accepted with, or it is not answered.

```text
question
  -> active release + signed benchmark acceptance
  -> binding check: accepted candidate digest, collection the candidate would build
  -> encode with the sealed candidate configuration
  -> release-filtered and approval-filtered lane search on the attested collection
  -> weighted RRF, retrieval floor, conflict-side selection, optional reranking
  -> canonical evidence resolution and licence policy
  -> grounded answer lane
```

`app/retrieval/pipeline.py` holds the mechanics and has exactly one implementation. The
benchmark exists to predict how serving behaves, and that prediction is only worth
something if both run the same code; a serving path that re-derived fusion would measure
one system and ship another. The sealed candidate is authoritative for lane vector names,
fusion weights, `rrf_k`, pool width, and output depth.

`app/retrieval/serving.py` is what refuses. It will not serve a release whose accepted
candidate digest differs from the loaded one, or whose collection is not the one that
candidate would have built, recomputed from the release manifest. It will not answer
through a partial fusion: the benchmark isolates a failed lane so a run can continue and
report it, while serving abstains, because the accepted scores describe the whole fused
configuration. It will not hand licence-restricted evidence to a generation provider,
though such records still rank and are still counted. It will not widen a request whose
organization filter matches no publisher in the release.

Every progress status is emitted by the stage that produced it. A candidate with no
reranker never reports `RERANKING`, and one with no query expansion never reports
`SEARCHING_COUNTER_EVIDENCE`.

A deployment with no `SERVING_CANDIDATE_PATH` starts with no engine and abstains on every
question with a stated reason. It never falls back to an unpinned model. Partially
specified artifacts are a different case and refuse to start at all.

`scripts/serving/local_serving_exercise.py` drives the whole path against local
PostgreSQL and Qdrant, activating the synthetic fixture release in a scratch database.

## Structured source boundary

```text
signed reconciliation candidate
  -> exact historical trust-root revision
  -> re-verified immutable source package
  -> independently preserved narrative assets + transitive FHIR packages
  -> verified authoritative-input closure attestation
  -> bounded, non-extracting FHIR validation
  -> complete resource + narrative-digest inventory
  -> lifecycle/license/narrative/dependency promotion gates
  -> canonical report + verified Ed25519 attestation
```

Structured resources are diagnostic build records, not retrieval evidence. The processor
can preserve a complete official companion package while blocking promotion when the
package is experimental, conflicts with publisher licensing policy, does not identify its
controlling narrative, or has an unreconciled dependency closure. No structured run can
move the active-release pointer.

## Evidence QA boundary

```text
signed corpus release candidate
  -> signed artifact manifest (no aggregate evidence payload)
  -> replay every PDF/XLSX anchor against preserved bytes
  -> immutable complete approve/quarantine decision batch
  -> canonical approved evidence artifacts
  -> three signed release attestations
  -> VALIDATED PostgreSQL release, index NOT_BUILT
```

Replay failures, incomplete classifier output, duplicate approved content, and releases
with zero approved records fail closed. Quarantined material is retained for audit but cannot become
release membership. QA reserves a release-specific Qdrant collection name only; it does
not create an index or move the active-release pointer. The separate Qdrant builder
reconciles the bundle with PostgreSQL and the QA ledger, writes only approved release
members, exhaustively validates the collection, and signs the result. It still cannot move
the active-release pointer.

## Safety behavior

Missing, failed, or unresolved required checks all withhold a claim. A missing canonical evidence ID cannot be treated as a citation. Related measurements such as eGFR and creatinine clearance remain distinct unless a separately validated calculation path is introduced.

Candidate evidence must be canonical and must be covered by every evidence-scoped
required check. Duplicate required checks are ambiguous and therefore fail. Evidence
returned for a renderable claim is limited to that claim's nominated candidates.

Clinical context has explicit present and known-absent sets. Silence about a required
or excluded fact evaluates to `UNKNOWN`, which is not eligible for unconditional
rendering. The synthetic cases in `data/fixtures/foundation_applicability.json` are
executed by the safety test suite to preserve this behavior.

## Frontend boundaries

The Next.js page and layout remain server components, while the interactive evidence
workspace is a narrow client boundary. Presentation panels are separate from
`useEvidenceRun`, which owns question submission, SSE progress, polling fallback, and
stale-run cancellation. Component styles are locally scoped CSS Modules; global CSS is
limited to tokens and document-level defaults.

FastAPI's OpenAPI document is checked in and generates the TypeScript HTTP contract.
The generated path types are consumed through `openapi-fetch`, while strict Zod schemas
validate HTTP and SSE payloads at runtime. This two-layer boundary prevents either
compile-time schema drift or malformed network data from being treated as trusted UI
state. See `docs/frontend.md` for the development workflow.
