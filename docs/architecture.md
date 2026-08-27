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
- `reasoning`: orchestration, serving retrieval, and grounded answer composition; it
  cannot override failed verification, and model output is a proposal that may be lowered
  to abstention but never raised to an answer
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

## Serving retrieval boundary

```text
question
  -> deterministic bounded expansion (no generative query drift)
  -> release + approval + lifecycle filtered dense/sparse search over one collection
  -> weighted RRF with deterministic tie-breaking and isolated lane failures
  -> payload-integrity check against canonical evidence
  -> presentation view: render, fingerprint, classify form
  -> duplicate suppression, then top-k selection
  -> evidence-role completeness gate, qualified by passage form
  -> grounded answer composition, or abstention before any model call
```

`ServingRetrievalService` is deliberately **not** `RetrievalBenchmarkRunner`. The runner
is the measured system: the accepted development report and the frozen comparator floor
were produced by that exact code path, so reshaping it to serve free-text questions would
silently invalidate comparability with every prior report. The two also have different
jobs — serving has no gold set, needs a latency budget, and treats role completeness as a
decision that gates abstention rather than a number reported afterwards.

Lifecycle is a filter, never a ranking signal. `SERVABLE_LIFECYCLE_STATES` is an
allowlist containing only `EFFECTIVE`, so a state added later is excluded until someone
decides it is safe to answer from. `PARTIALLY_SUPERSEDED` is excluded because serving
cannot tell which part of a record was superseded. This matters because a superseded
edition can be near-identical in text to the current one — neither fusion nor duplicate
suppression can be trusted to prefer the right member, and returning a withdrawn
recommendation carrying valid provenance is the worst failure this system can emit. The
constraint is inert on a single-version release and load-bearing the moment a
multi-edition publisher is materialized.

What they must share is safety behaviour, so the payload-integrity checks are reproduced
rather than skipped. A point whose payload disagrees with the canonical evidence record
is a corrupted index, and both paths fail closed on it. Passage text never comes from
Qdrant: the index carries only filterable metadata, and `content_exact` is read from the
canonical evidence records, because Qdrant is a rebuildable projection and never source
of truth.

## Presentation is a view, never a re-materialization

`app/reasoning/presentation.py` sits between canonical evidence and everything that
displays it. It never produces a new record: `content_exact`, anchors, and evidence
digests are what the QA ledger decided on and what the signed release binds, so a record
re-materialized to read better would invalidate both. Citation stays bound to
`evidence_id`, and the immutable content is carried alongside the view rather than
replaced by it.

XLSX evidence is anchored per cell, so `content_exact` for a spreadsheet row is the
cell-addressed form `A145=HIV.D8 …`. That is the correct anchor — replaying it against
the workbook is what the QA gate does — and it is unusable as passage text. Parsing it
back into cells yields three things the serving path consumes, all of them determinations
about existing content rather than new facts about it:

- a rendering under the publisher's own column labels, read from the release's own
  approved header rows where they survived QA and declared where they did not;
- a fingerprint over the substantive cells, which is what lets a verbatim copy collapse
  onto the row it copies;
- a classification of *form* — data-dictionary entry, decision rule, schedule entry,
  indicator, header, caption, section title, narrative — decided from structure and never
  from a role label.

The role gate consumes the third. Roles in this corpus are assigned by source asset
rather than by content, so `PRIMARY_SUPPORT` is only counted from a passage whose form can
carry a recommendation at all; an unrecognised table cannot, because the view cannot tell
what it is and the gate exists to fail closed. This does not repair the labels — that is
a QA-time fix with a release-wide cost, recorded in `docs/roadmap.md` — it stops the
abstention gate resting on a claim it never verified, on the same argument as reproducing
the payload-integrity checks rather than trusting the index. Disqualified role claims are
reported, so an answer withheld because of the corpus's labelling says so.

This does not touch `render_allowed`, which gates display of the *source document* —
evidence cards and exact PDF highlighting — and remains unresolved for every WHO asset.

One limit is current rather than architectural: `QuestionService` does not yet call this
path — it emits progress events and abstains with `RETRIEVAL_PIPELINE_NOT_CONFIGURED` —
so the boundary is reachable only through `scripts/ask.py`.

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
