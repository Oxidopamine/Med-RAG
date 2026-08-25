# Implementation roadmap

## Complete: foundation

- strict canonical schemas and executable synthetic safety fixtures
- conflict-safe source lifecycle DAG with scoped supersession
- tri-state eligibility/applicability evaluation with explicit negative facts
- deterministic claim gate with per-evidence verification coverage
- async API/UI contract with fail-closed abstention

## Complete: frontend hardening

- feature-level component decomposition with isolated SSE orchestration
- scoped CSS Modules and accessible dialog behavior
- generated OpenAPI path types plus strict runtime payload validation
- component tests and cross-browser Playwright accessibility coverage

## Complete: authenticated ingestion

- PostgreSQL source registry persistence and Alembic migration
- API-key authorization and per-publisher domain allowlists
- redirect-safe acquisition and immutable SHA-256-addressed PDF storage
- PyMuPDF page/span/bounding-box extraction
- extraction trust records, automated QA boundary, and quarantine audit workflow

## Complete: corpus release seam v1

- versioned release/evidence/provenance/locator/exception contracts
- deterministic canonical JSON and manifest/evidence digest binding
- frozen fixture release and checked-in JSON Schema compatibility test
- immutable canonical evidence and release membership persistence
- release-scoped index-validation gate and atomic active-release pointer
- transactional release/index/activation outbox events
- serving requests pinned to one active release or fail-closed abstention
- separate corpus-steward validation and schema-export command

## Complete: inventory reconciliation foundation

- versioned trust-root registry and publisher inventory connector SDK
- durable, idempotent attempt and resumable stage ledger
- immutable inventory-response artifacts and full reconciliation jobs
- cryptographic signature verification for stage attestations and activation decisions
- deterministic drift/failure connector and a WHO SMART FHIR package connector
- exact inventory accounting with verified, bounded exception records

## Complete: authoritative structured-source processing

- immutable trust-root revisions bound to reconciliation candidates
- bounded, non-extracting FHIR NPM package validation
- complete FHIR resource inventory with content and narrative digests
- package/ImplementationGuide identity, lifecycle, version, and license gates
- controlling-narrative linkage diagnostics and signed promotion decisions
- independently acquired, immutable controlling-narrative artifacts
- complete, digest-verified transitive FHIR package dependency closure
- cryptographically bound input-closure and structured-validation attestations

## Complete: authority composition and evidence materialization

- per-asset acquisition, materialization, redistribution, and rendering policy
- signed DAK-primary/SMART-companion authority binding
- experimental companion acceptance limited to non-clinical structural mappings
- deterministic PDF page and XLSX row extraction with source anchors
- complete asset/source-unit coverage accounting and immutable evidence artifacts
- signed corpus release candidate held behind QA with retrieval explicitly disabled

## Complete: evidence QA and corpus promotion

- signed compact manifest over content-addressed materialized evidence artifacts
- deterministic PDF/XLSX anchor replay against preserved narrative artifacts
- deterministic per-record decisions bound by a signed complete QA batch
- explicit clinical roles, applicability, thresholds, exceptions, and quarantine reasons
- canonical promotion of approved records with headers, duplicates, and non-evidence excluded
- signed inventory, evidence-verification, and release-policy attestations
- validated corpus registration held at `NOT_BUILT` before any retrieval work

## Complete: release-specific Qdrant construction gate

- sealed vector batches pinned to exact dense and sparse model artifacts and revisions
- bundle, PostgreSQL release membership, and immutable QA-ledger reconciliation
- stable evidence-to-Qdrant UUID mapping and approval-only payload construction
- no-delete, safely resumable collection creation with exact vector and payload indexes
- exhaustive point, payload, evidence, vector, dimension, and collection validation
- dense and sparse self-retrieval smoke tests under release/approval filters
- signed index validation attestation and `NOT_BUILT` to `VALIDATED` registration
- activation retained as a separate signed gate

## Next: additional authoritative publishers

- add licensed and released XML/JSON/JATS publisher connectors
- retain credential-gated NICE integration until licensed API credentials are provisioned

## Then: production workflow deployment

- schedule the existing durable stages in a production workflow runtime
- separate writer credentials and deployable steward packaging
- workload-identity-backed production signing keys and rotation policy
- object-storage backend for the existing content-addressed artifact interface

## In progress: benchmarked retrieval and candidate selection

- complete: model-pinned vector producer boundary and deterministic plumbing baseline
- complete: sealed local model-artifact manifests with exhaustive bytes/inventory verification
- complete: artifact-bound production dense/sparse adapter boundary with asymmetric query encoding
- complete: hard production batch, timeout, transient-retry, attempt, shape, and numeric limits
- complete: sealed benchmark/gold-set contracts and synthetic end-to-end fixture
- complete: sparse, dense, and reciprocal-rank-fused ablation runner with release filters
- complete: Recall@K, nDCG, MRR, context precision, complete-set/role recall, leakage, and latency gates
- complete: local-only BGE-M3 dense and Qdrant-IDF-compatible Unicode BM25 sparse adapters
- complete: candidate/verified-local adapter selection with a bounded `production` CLI alias
- complete: allowlisted manifest-driven adapter registry and sealed candidate configuration
- complete: Qwen3 Embedding 0.6B/4B/8B adapter family with instruction formatting,
  last-token pooling, left padding, truncation, MRL dimension bounds, and post-truncation normalization
- complete: benchmark contract 1.2 with development/holdout partition identity, adjudicator
  provenance and agreement, access/process/threshold revisions, insufficient-evidence cases,
  alternative minimum evidence sets, per-safety-stratum gates, and Wilson uncertainty reporting
- complete: configurable weighted RRF, deterministic ties, isolated lane failures, and
  failure-default acceptance blocking
- complete: signed, expiring sealed-holdout acceptance contract and transactional activation
  gate binding suite/report/runner/candidate/vector/release/manifest/index relationships
- complete: negative activation coverage for missing, stale, mismatched, and stored-tampered
  benchmark acceptance records
- complete: immutable clinical reviewer-decision and disagreement-resolution ledger,
  separately sealed access/adjudication/threshold policies, and content-addressed artifacts
- complete: policy-guarded development and one-time sealed-holdout suite builders with exact
  release validation, full safety-topic coverage targets, and access audit events

The completed BGE-M3/BM25 path is candidate A and a control, not a clinically accepted or
best-available stack. Public leaderboards cannot establish clinical retrieval quality.
No candidate becomes `production` until it passes the sealed clinical holdout and signed
acceptance gate. The acceptance signature belongs to the benchmark/release authority, not
to a clinical adjudicator.

### Next: prove the real candidate runtimes

- run the pinned BGE-M3 weights through the real Torch/Transformers adapter on every
  supported device/dtype; record numerical parity, truncation, memory, throughput, latency,
  timeout, and recovery behavior
- derive and seal the BM25 average document length from the exact approved release rather
  than relying on the example value
- perform a controlled Qdrant 1.15.4 versus 1.19.x compatibility and relevance benchmark;
  retain the old pin until collection, filtering, payload, sparse-IDF, and attestation
  contracts pass unchanged or are deliberately versioned

### Next: trustworthy clinical benchmark contract and evidence

The adjudication repository and guarded suite-construction workflow are implemented. The
remaining work in this section is operational clinical review and real evidence population:

- clinical adjudicators review cases and resolve disagreements through the ordinary review
  workflow; they do not hold signing keys or cryptographically sign cases, suites, reports,
  attestations, or releases
- the benchmark service seals the finalized adjudication record, and the designated
  benchmark/release authority alone signs later acceptance and activation artifacts
- record adjudicator identity, clinical role, independence, instructions, evidence access,
  decision timestamps, disagreement, and resolution as provenance metadata without an
  adjudicator signature requirement
- create development and untouched sealed holdout suites; forbid candidate tuning against
  the acceptance holdout and bind both access/process revisions
- add independently adjudicated cases for contraindication, applicability, dose, monitoring,
  stale source, wrong jurisdiction, multilingual and cross-lingual retrieval, terminology,
  negation, and conflicting evidence
- populate the implemented insufficient-evidence, graded-relevance, alternative
  minimum-complete-evidence-set, and safety-stratum contracts with adjudicated clinical cases
- define sample-size targets, adjudicator agreement reporting, and the threshold-setting
  procedure before evaluating final candidates; retain the implemented Wilson reporting and
  add a prespecified method for non-binomial metric uncertainty

### Then: manifest-driven retrieval candidate matrix

- execute the implemented Qwen3 family against verified local artifacts and record numerical,
  memory, latency, truncation, timeout, and recovery behavior on every target runtime
- use `Qwen3-Embedding-4B` plus `Qwen3-Reranker-4B` as the primary practical development
  preset; benchmark the 8B variants as the quality-ceiling preset where target hardware
  satisfies the sealed memory and latency budgets, and retain the 0.6B variants as the
  deployment-floor preset
- treat Qwen-first as build priority rather than pre-acceptance: every size, instruction,
  precision, truncation, and quantization combination is a distinct pinned candidate and
  must pass the same clinical development suite and untouched acceptance holdout
- retain pinned BGE-M3 dense retrieval as the multilingual control candidate
- add an English-biomedical MedCPT query/article dual-encoder specialist and extend the
  model-artifact contract to bind both asymmetric artifacts as one candidate identity;
  preserve the official 512-token, `[CLS]`-pooling, and 768-dimension behavior, encode
  guideline passages as an explicitly versioned title/section-plus-chunk pair, and do not
  use MedCPT as the multilingual default
- compare the current Unicode BM25 encoder with pinned official Qdrant/FastEmbed BM25;
  evaluate tokenizer, stemming, multilingual, collision, exact-term, and latency behavior
- add BGE-M3 learned lexical weighting as its own sparse-vector lane without silently
  applying the BM25/IDF scoring contract; retain BM25 as an independent exact lexical
  control and do not describe BGE-M3 token weighting as synonym or vocabulary expansion
- keep the English, MS-MARCO-trained, CC BY-NC-SA `naver/splade-v3` checkpoint out of the
  default production matrix; it may be measured only as a research comparator unless its
  licensing and clinical-domain limitations are deliberately resolved
- add an exact-terminology lane with deterministic, versioned vocabulary expansion and
  explicit licensing/jurisdiction handling for controlled clinical terminologies
- add deterministic safety-query variants for applicability, contraindication, dose,
  monitoring, jurisdiction, and freshness without allowing generative query drift
- add a bounded reranker interface with Qwen3-Reranker-4B as the primary practical
  candidate, Qwen3-Reranker-8B as the hardware-permitting quality ceiling,
  Qwen3-Reranker-0.6B as the deployment floor, BGE-reranker-v2-m3 as the multilingual
  control, and MedCPT Cross-Encoder as the English-biomedical specialist
- benchmark reranker candidate pools of 20, 50, and 100 and output depths of 5, 10, and the
  actual generation-context budget; report absolute and relative deltas with uncertainty,
  and make no fixed 10-15% improvement assumption
- enforce the pre-rerank candidate-recall ceiling, minimum-complete-evidence-set recall, and
  safety-role coverage so a relevance reranker cannot silently remove a required exception,
  contraindication, applicability, dose, monitoring, jurisdiction, or freshness item
- extend the implemented lane-failure accounting with per-stage memory, timeout, malformed
  output, and fallback metrics for the new lexical, terminology, safety-query, specialist,
  and reranking lanes

### Then: select, attest, and activate

- choose the stack from the development suite using complete-evidence recall, safety-stratum
  recall, forbidden leakage, context precision, latency, memory, and failure rate; never a
  general-purpose leaderboard
- run the frozen candidate exactly once against the untouched sealed acceptance holdout;
  reject any post-holdout model, prompt, tokenizer, fusion, threshold, corpus, or index change
- exercise the implemented signed acceptance/activation gate with the real selected stack;
  add explicit rejected-report and cross-release replay integration cases alongside the
  existing missing, stale, mismatched, and tamper coverage

## Finally: verified rendering

- atomic claim planning
- numeric, unit, operator, quote, and provenance validators
- implement the independent high-risk semantic verifier as a calibrated three-way
  entailment/contradiction/insufficient-support signal, never as mathematical proof and
  never as a replacement for deterministic validators
- bind the exact verifier checkpoint, tokenizer, training/evaluation provenance, thresholds,
  precision, and runtime to the release; `MedNLI` is a dataset rather than a model, and an
  unverified community checkpoint must not be implied by a generic model-family name
- verify atomic claims against their minimum complete evidence sets without silent context
  truncation; separately test negation, modality, population/applicability, dose, number,
  unit, operator, duration, and multi-evidence reasoning
- calibrate verifier thresholds per claim type and language, measure entailment precision,
  contradiction recall, calibration error, and selective risk, and fail closed to
  `UNRESOLVED` for unsupported languages, truncation, low margin, or conflicting evidence
- evidence cards and exact PDF highlighting
