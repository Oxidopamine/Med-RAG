# Autonomous guideline corpus plan

Status: Phase 0 and the Phase 1 inventory-reconciliation slice are executable
Reviewed: 2026-08-25
Scope: official WHO, US, UK, and EU clinical-guideline sources selected by the project

## Implementation status

The first executable slice now includes contract version `1.0.0`, a frozen synthetic
release bundle, a checked-in JSON Schema, canonical release/evidence persistence, a
singleton active-release pointer, release/index/activation outbox events, deterministic
activation gates, and a separate steward validation command. Serving requests pin the
active release at submission and continue to abstain because retrieval is not configured.

Phase 1 now includes the PostgreSQL trust-root and signing-key registries, an idempotent
attempt/resumable-stage ledger, content-addressed raw inventory and source artifacts, a
connector SDK, deterministic synthetic connector, scoped WHO SMART FHIR connector,
inventory gates, signed exceptions, and Ed25519 verification for reconciliation stages and
activation decisions. A production workflow scheduler, remote object-store backend,
additional publishers, Qdrant index construction, and extraction/retrieval benchmarks
remain to be built in later phases.

## Decision

Build the guideline corpus system as an independently runnable service and workstream,
but keep it in the same repository as the web application and public API.

The two sides can be developed at the same time:

- The web application and question API consume a versioned, read-only corpus release.
- The corpus steward discovers, acquires, validates, and publishes corpus releases.
- PostgreSQL holds canonical release and evidence state.
- Immutable object storage holds original and derived source artifacts.
- Qdrant is a rebuildable retrieval index, never the source of truth.
- Until a validated release is active, the application continues to abstain.

The corpus process must not run inside a web request. A slow publisher, parser failure,
or connector repair must not affect web availability.

## System boundary

```text
                         build side

 official publishers -> durable workflow -> bounded AI workers
                              |                    |
                              v                    v
                       deterministic gates <- parser/verifier tools
                              |
                              v
                 immutable corpus release candidate
                              |
                    reconciliation + evaluations
                              |
                     signed activation decision
                              |
                              v
             PostgreSQL active release + Qdrant collection

                         serving side

 user -> web app -> question API -> active release only -> verified response/abstention
```

The intended code ownership boundary is:

| Area | Responsibility |
|---|---|
| `apps/web` | User experience and evidence presentation |
| `apps/api` | Public query contract, retrieval orchestration, and safety gates |
| Corpus-steward process | Discovery, connectors, acquisition, extraction, verification, indexing, and release construction |
| Shared domain contracts | Evidence identity, lifecycle, provenance, release manifest, and activation semantics |

The corpus steward can initially reuse Python domain code already under `apps/api`, but
it must have a separate process entry point, job lifecycle, credentials, and deployment.
As the implementation grows, move its runtime-specific code into an independently
packaged `apps/corpus-steward` without duplicating the shared contracts.

## Parallel-development contract

Web work does not need to wait for corpus ingestion. It should develop against a frozen
fixture or generated API contract containing:

- `corpus_release_id` and manifest digest;
- canonical `evidence_id` and `source_id`;
- exact quotation or table-cell content;
- generic source anchors plus format-specific locators;
- publisher, jurisdiction, language, and lifecycle state;
- applicability, recommendation strength, and evidence roles;
- verification results and abstention reasons.

Corpus work does not need the finished interface. It only has to produce records that
pass the shared contract and release gates.

Integration occurs at three deliberately narrow seams:

1. Shared versioned schemas and generated client types.
2. A PostgreSQL pointer to one active immutable corpus release.
3. Read-only queries against the concrete retrieval collection for that release.

Schema changes require compatibility tests. A corpus release must never mutate after
activation; corrections create a new release. Web requests already in flight may finish
against the old release, while new requests use the new one.

## What “all official and up to date” means

There is no unified official worldwide, US, or EU catalog that can prove every clinical
guideline on the web has been found. The system therefore makes a bounded and auditable
coverage guarantee:

> Every record enumerated by the configured authoritative publisher inventories at the
> recorded cutoff time is present in the release, or is listed in its signed exception
> register with the exact access, licensing, acquisition, or validation blocker.

The guarantee is tied to a versioned trust-root registry. For every issuer, it records:

- official organization identity and jurisdictions;
- guideline definition and included product families;
- authoritative catalog, API, feed, sitemap, repository, and lifecycle endpoints;
- permitted acquisition method, licence, and credential reference;
- supported languages and regional sites;
- inventory and pagination rules;
- withdrawn, archived, corrected, adopted, and superseded states;
- polling schedule and expected freshness;
- connector version and last successful full reconciliation.

Search engines, PubMed, Crossref, BIGG-REC, GIN, and similar aggregators are recall
challengers. They may identify a missing candidate, but cannot make it authoritative.
Only verification against the official issuer can promote a candidate.

The AI agent may propose new trust roots or changed acquisition rules. It cannot approve
those changes, silently expand scope, bypass a licence, or redefine “official.”

## Source precedence

Use the highest-fidelity official representation available:

1. Licensed publisher API or structured XML/JSON feed.
2. Stable released FHIR package or WHO SMART artifact, reconciled to its narrative.
3. Official JATS/XML or structured HTML.
4. Born-digital PDF.
5. Scanned or mixed PDF with OCR.
6. Region-level VLM recovery for unresolved content only.

The publisher’s controlling narrative publication and lifecycle status remain the source
of clinical authority. A structured or computable artifact is a linked companion unless
the publisher explicitly designates it as controlling.

Current interoperability baselines include:

- [HL7 FHIR Clinical Practice Guidelines 2.0.0](https://hl7.org/fhir/uv/cpg/)
- [HL7 Canonical Resource Management Infrastructure 2.0.0](https://hl7.org/fhir/uv/crmi/)
- [Clinical Quality Language](https://cql.hl7.org/)
- [WHO SMART Guidelines](https://smart.who.int/)
- [JATS 1.4](https://jats.nlm.nih.gov/publishing/1.4/)
- [NICE syndication API](https://www.nice.org.uk/reusing-our-content/nice-syndication-api)

FHIR CPG and CRMI are trial-use standards. The canonical evidence model should be a
compatible superset, not a lossy copy of their status or grading fields. Preserve the
publisher’s native grading system and model corrections, adoption, withdrawal, and
partial supersession explicitly.

## Automated control plane

Use a durable deterministic workflow engine, such as Temporal or an equivalent, for the
outer state machine. Network fetches, model calls, parsers, storage writes, and index
operations are idempotent activities. Agent conversation state is not the job ledger.

The production sequence is:

```text
durable workflow
  -> bounded AI task
  -> deterministic machine gate
  -> signed stage attestation
  -> immutable release candidate
  -> inventory reconciliation and evaluation
  -> atomic activation
```

Bounded AI workers may perform:

- issuer and catalog discovery;
- connector change diagnosis and repair proposals;
- document and lifecycle classification;
- section, recommendation, and table interpretation;
- disagreement analysis between independent parsers;
- candidate relationship and supersession discovery;
- release anomaly investigation.

AI workers may not:

- change trust or release policy;
- add unrestricted tools or network destinations;
- mark their own output verified;
- overwrite original artifacts;
- resolve contradictory official evidence by guessing;
- sign attestations or activate releases.

## Acquisition and currentness loop

For each configured source, the steward runs both incremental polling and periodic full
reconciliation:

1. Enumerate the complete official inventory and retain the raw response.
2. Compare counts and identifiers with the previous inventory.
3. Follow pagination and current, withdrawn, archive, correction, and adoption views.
4. Fetch conditionally with publisher-supported `ETag` and `Last-Modified` semantics.
5. Preserve original bytes, resolved URL, headers, timestamps, and SHA-256 digest.
6. Link alternate formats and computable companions to the controlling narrative.
7. Detect new, changed, removed, corrected, adopted, withdrawn, and superseded items.
8. Create a new release candidate instead of editing the active release in place.
9. Reconcile challenger catalogs and unexplained inventory drift.
10. Block release while any required discrepancy is unresolved.

Publisher-specific connectors are required because status semantics differ. NICE, for
example, must use its licensed API rather than scraping, and sources excluded by that
licence require separate rights and connectors.

## Extraction and verification

No single parser or VLM is source truth. Route by input type and compare independent
representations:

| Input | Primary extraction | Independent check or fallback |
|---|---|---|
| FHIR/XML/JSON/JATS | Schema-validating native parser | Rendered narrative comparison |
| Publisher HTML | DOM and publisher-specific parser | Rendered page or official PDF |
| Born-digital PDF | PyMuPDF text and geometry | Docling structure and reading order |
| Scanned/mixed PDF | PaddleOCR/PP-StructureV3 | Alternate OCR or Docling OCR |
| Difficult region | Existing native/OCR tokens | Crop-level VLM proposal |

Pin exact parser, model, tokenizer, schema, prompt, and connector versions. Do not depend
on mutable `main` branches or unconstrained package ranges in a released corpus.

Every source preserves:

- immutable original bytes and digest;
- exact text separately from normalized search text;
- page, character/span, DOM, XML, FHIR, and other applicable anchors;
- recommendation identity and section hierarchy;
- table cell graph, spans, headers, caption, footnotes, and coordinates;
- derivation links among narrative, DAK, FHIR package, translation, and adaptation;
- extraction outputs and disagreements from every parser used.

Automatic approval requires agreement on policy-defined invariants. Digits, decimals,
ranges, comparison operators, units, dose frequencies, negation, recommendation grades,
and footnote markers are critical fields. A VLM can propose a repair but cannot override
contradictory native or OCR evidence. Unresolved cases remain quarantined.

## Canonical storage and provenance

PostgreSQL is the canonical store for sources, evidence, lifecycle relations, workflow
state, release manifests, and activation. Store canonical state and an outbox event in
the same transaction so downstream indexing is retryable and idempotent.

Each stage emits an in-toto-style statement bound to artifact digests. Its predicate
records inputs, connector/parser/model versions, policy digest, timestamps, output,
verifier identity, and result. Sign with a control-plane workload identity, not an agent
credential. The [SLSA attestation model](https://slsa.dev/spec/v1.2/attestation-model)
provides the baseline; signatures prove origin and integrity, not clinical correctness.

Build one immutable Qdrant collection per `corpus_release_id`. Validate it completely,
then activate it using the canonical PostgreSQL release pointer. Qdrant aliases may be
used operationally, but they are not a substitute for the canonical activation record.

## Retrieval release

Index structure-aware evidence units instead of fixed token windows:

- recommendations and their rationale;
- semantic paragraphs;
- algorithm steps;
- table rows with headers, captions, and footnotes;
- eligibility, exception, contraindication, monitoring, and dosing statements.

Each evidence object has an immutable exact view and a context-enriched search view that
resolve to the same canonical ID.

The serving pipeline is:

1. Filter by active release, effective lifecycle state, jurisdiction, language, source
   class, and approval status.
2. Run independent exact/sparse and dense searches.
3. Generate separate deterministic safety searches for applicability, recommendation,
   exceptions, contraindications, dose or threshold, and monitoring.
4. Fuse candidates with reciprocal-rank fusion.
5. Rerank with a model selected by the project benchmark.
6. Require the minimum complete evidence-role set.
7. Abstain if coverage, provenance, currentness, or required roles are incomplete.

Use Qdrant native sparse BM25 plus separately benchmarked learned-sparse and dense lanes.
Build the artifact-bound Qwen3 embedding/reranker family first, with the 4B pair as the
primary practical preset, the 8B pair as a hardware-permitting quality ceiling, and the
0.6B pair as a deployment floor. Retain BGE-M3 as the multilingual control and MedCPT as
an English-biomedical specialist. This is an implementation priority, not a production
selection; do not select an embedding or reranker from a public leaderboard alone.

## Security and licensing

Treat downloaded documents, HTML, metadata, FHIR narratives, and tool responses as
hostile input that may contain prompt injection or malicious files.

Required controls include:

- immutable per-stage tool allowlists and least-privilege identities;
- publisher-domain egress allowlists and SSRF protection;
- sandboxed parsers, OCR, archive handling, and CQL execution;
- no credential access for content-reading AI workers;
- short-lived, audience-bound credentials without token passthrough;
- file type, size, archive-depth, redirect, and decompression limits;
- publisher licence and robots-policy enforcement;
- complete traces that exclude secrets and protected health information.

Acquisition stops on missing rights or credentials and records the blocker. Automation
does not imply permission to scrape or republish.

## Evaluation and release gates

Before selecting parsers or retrieval models, build a frozen project benchmark stratified
by publisher, jurisdiction, language, format, scan quality, layout, evidence type, and
clinical risk. Generic document and retrieval leaderboards are screening evidence only.

Extraction evaluation covers:

- character/word accuracy and reading order;
- section hierarchy and exact source highlighting;
- table structure and cell/header/footnote association;
- exact numbers, operators, units, negation, and recommendation grades;
- narrative-to-structured-artifact consistency;
- end-to-end provenance reconstruction.

Retrieval evaluation covers:

- Recall@K, nDCG, MRR, and context precision;
- minimum-complete-evidence-set recall;
- exception, contraindication, applicability, dose, and monitoring recall;
- stale-source and wrong-jurisdiction leakage;
- abstention calibration and latency.

Every benchmark revision runs lexical-only, dense-only, hybrid, and hybrid-plus-reranker
ablations. Critical-field corruption, stale-source leakage, wrong-jurisdiction leakage,
or an unresolved authoritative-inventory discrepancy blocks activation.

## Delivery sequence

### Phase 0: freeze the seam

- Version the corpus release, evidence, provenance, and abstention contracts.
- Add fixture releases for independent web/API development.
- Separate corpus-writer privileges from serving read privileges.
- Define the active-release pointer and compatibility tests.

### Phase 1: durable steward foundation

- Add the separate steward process and durable workflow runtime.
- Add trust-root registry, connector SDK, immutable artifact storage, and outbox.
- Add signed stage attestations and release-manifest generation.
- Preserve the existing fail-closed application behavior.

### Phase 2: authoritative structured connectors

- Implement licensed feeds and released FHIR/XML/JSON/JATS paths first.
- Implement narrative-to-structured-artifact reconciliation.
- Add publisher-specific lifecycle state machines.
- Establish full-inventory and incremental-currentness jobs.

### Phase 3: document ensemble

- Add HTML, born-digital PDF, scanned PDF, tables, and crop-repair routes.
- Run the parser bake-off and pin passing versions.
- Add quarantine, disagreement diagnosis, and connector repair workflows.

### Phase 4: benchmarked retrieval

- Implemented foundation: immutable-release dense/BM25 indexes, sealed candidate manifests,
  manifest-dispatched Qwen3/BGE/BM25 adapters, weighted fusion, benchmark 1.1 safety strata,
  uncertainty and insufficient-evidence accounting, and signed acceptance-gated activation.
- Build exact terminology, learned lexical, deterministic safety-query, and MedCPT lanes.
- Evaluate bounded Qwen3/BGE/MedCPT reranking candidates and preserve safety-role coverage.
- Populate development and untouched holdout suites with adjudicated clinical evidence.
- Test rollback to a prior accepted release and the complete deployment runtime.

### Phase 5: continuous coverage

- Schedule incremental polling and periodic full reconciliation.
- Add challenger discovery, drift alerts, and self-healing repair proposals.
- Publish signed coverage dashboards and exception registers.
- Exercise publisher failures, prompt injection, parser corruption, and stale-release drills.

## Definition of done

The corpus system is ready to serve only when:

- every configured official inventory reconciles at a recorded cutoff;
- every missing item has an explicit signed exception;
- every approved evidence object reconstructs to immutable official source content;
- lifecycle and supersession checks pass;
- extraction and retrieval regression gates pass;
- no critical-field, stale-source, or wrong-jurisdiction violation is present;
- the release is immutable, signed, reproducible, and atomically activatable;
- the public API remains fail-closed when any required condition is unavailable.

This architecture automates routine operation with AI while preventing the same agent
from creating evidence, declaring it correct, and publishing it without an independent
machine-verifiable gate.
