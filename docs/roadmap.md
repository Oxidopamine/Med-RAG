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
- candidate-vector-profile isolation with final attestation selecting one immutable serving collection
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
- complete: benchmark contract 1.3 with `AUTOMATED_SOURCE_DERIVED` provenance,
  development/holdout partition identity, source-evidence derivation links, insufficient-evidence
  cases, alternative minimum evidence sets, per-safety-stratum gates, and Wilson uncertainty
  reporting
- complete: configurable weighted RRF, deterministic ties, isolated lane failures, and
  failure-default acceptance blocking
- complete: signed, expiring sealed-holdout acceptance contract and transactional activation
  gate binding suite/report/runner/candidate/vector/release/manifest/index relationships
- complete: negative activation coverage for missing, stale, mismatched, and stored-tampered
  benchmark acceptance records
- complete: immutable reviewer-decision and disagreement-resolution ledger,
  separately sealed access/adjudication/threshold policies, and content-addressed artifacts
- complete: policy-guarded development and one-time sealed-holdout suite builders with exact
  release validation, full safety-topic coverage targets, and access audit events
- complete: deterministic source-derived generation across all 11 safety topics, secret-ranked
  disjoint partitions, immutable generation records, and candidate-independent suite construction
- complete: a 275-case WHO SMART HIV development suite and custody-controlled 550-case holdout;
  the holdout is registered but not exported into the engineering workspace
- complete: an execution ledger that permits repeated development runs but atomically consumes
  each holdout once while binding the final sealed candidate and vector batch
- complete: deterministic development baseline with no filter leakage or candidate failures;
  remaining failures are expected retrieval-quality gaps in terminology, conflict completeness,
  and overall context precision
- complete: a versioned pinned-Qwen runtime matrix that verifies local artifact bytes, records
  environment/latency/throughput/memory/truncation/norm evidence, and seals unavailable targets
  as blockers instead of fabricating measurements
- complete: candidate-vector-profile collection identity, so Qwen and deterministic vectors
  cannot collide while reranker and fusion-only variants reuse the same exact vector batch
- complete: release-derived Unicode BM25 parameters for all 5,145 approved records and a
  checkpointed exact-release Qwen3-Embedding-0.6B vector-production path
- complete: bounded exact-terminology and clinical-safety query expansions with pure BM25-only
  and dense-only ablations; the deterministic v3 control improved hybrid complete-evidence
  coverage from 93.45% to 95.64% with no case regressions
- complete: verified-local Qwen3-Reranker-0.6B yes/no scoring adapter, deterministic safety-role
  preservation, isolated fallback, and sealed pool-20/50/100 development candidates
- complete: exact Qwen3-Embedding-0.6B/BM25 vector batch and exhaustively validated isolated
  Qdrant collection; pre-rerank development hybrid reached 99.27% recall, 98.55% complete-
  evidence coverage, and 99.82% required-role recall with no failures or forbidden leakage
- complete: registered Qwen3-Reranker-0.6B pool-20/50/100 development comparison with the
  artifact-bound dynamic-int8 CPU runtime and shared digest-checked score cache; every pool
  regressed complete-evidence coverage from 98.55% to 96.73% and was rejected. That comparison
  is confounded: all three pools shared a 256-token reranker budget which, after the official
  prompt template, instruction, and a roughly 90-token query, left about 75 tokens for a
  document whose release mean length is 86 and p95 is 204 tokens. Most documents were truncated
  mid-passage, and nDCG fell from 0.8900 to 0.62-0.66 with MRR from 0.8673 to 0.51-0.57, which
  is an ordering collapse rather than a mild relevance regression. The truncation explanation
  was tested and refuted: a pinned 512-token candidate, sealed as its own artifact digest, was
  measurably worse than 256 - answerable nDCG 0.4391 against 0.8118 for the pre-rerank control,
  MRR 0.2914 against 0.7641, and r-precision 0.1100, meaning the correct passage survives as
  the top hit in roughly one case in nine. Doubling the document budget made the ranking worse,
  so the 0.6B reranker rejection stands on the model's behaviour rather than on a starved
  context. The remaining explanation is distributional: a yes/no relevance cross-encoder is
  being handed a query that is itself the passage, and more passage text makes that worse
- complete: benchmark contract 1.4 corrects the acceptance metric set. Context precision at a
  fixed depth is bounded by `min(|gold|, k)/k`; with 150 single-gold cases, 25 two-gold cases,
  and 100 negatives at `top_k=10` the old 275-case suite could not exceed 0.4364, which is
  exactly what the leading candidate scored. The 0.60 threshold was unsatisfiable, and the
  standing "improve context precision" task was chasing an already-optimal metric. Reports now
  carry `context_precision_ceiling_at_k`, sealing a suite whose policy exceeds its own ceiling
  fails closed, and the achievable gate moved to `r_precision`
- complete: every mode summary now reports `answerable_*` metrics separately from the
  insufficient-evidence partition. Negative cases pass whenever the release filter matches
  nothing by construction, so a deterministic hashing baseline scored 1.0 on all 100 of them;
  they are a third of the suite and were inflating every blended headline number
- complete: removed retrieval-time context budgeting that selected an output depth by matching
  the benchmark generator's own question-template prefixes, and re-derived conflict-side lane
  selection from declared expansion origins instead of positional lane names
- complete: a `PARAPHRASED_INTENT` stratum built by controlled-vocabulary substitution. Five of
  the existing strata have measured query-term coverage of 1.000 by their gold passage, so they
  test near-duplicate lookup and systematically reward exact lexical matching; the new stratum
  measures 0.411. Its first revision gated answerability on the passage being the unique best
  lexical match, which selected the lexically unambiguous records and made it the easiest
  stratum in the suite - r-precision 0.960 sparse against 0.280 for `TERMINOLOGY`. The rule now
  requires the passage to sit inside a lexical rank band: reachable within the retrieval depth,
  never already first. It is not a clinician-authored paraphrase
- complete: benchmark contract 1.5 restricts acceptance gates to three defensible forms after
  a review of the evaluation literature found no basis for absolute retrieval thresholds. IR
  evaluation is comparative by construction, scores are not comparable across test collections,
  and topic difficulty contributes more variance than system quality; BM25 alone spans 0.213 to
  0.789 nDCG@10 across BEIR. Gates are now either derived from the downstream consumer's context
  budget, stated as a Wilson lower bound rather than a point estimate, or expressed relative to
  a comparator measured on the same case set. R-precision is degated: 175 of 200 answerable
  cases carry one gold record, so it degenerates to precision@1 and measures top-1 ranking that
  no safety argument depends on
- complete: benchmark contract 1.6 separates the measurement from the system under test.
  `rrf_k`, `rrf_weights`, and `candidate_limit` left the sealed suite; only `top_k` stays,
  because every rank metric and the context-precision ceiling are defined at it. The suite
  previously pinned fusion, so re-tuning fusion minted a new benchmark and post-retrieval
  selection was the only tunable surface left - which is how template-keyed output
  budgeting came to be written. The candidate manifest is now authoritative for fusion
- complete: the threshold policy states how its numbers were derived. Every v5 gate was a
  Wilson-95 lower bound of a measured comparator run, so `set_before_candidate_evaluation`
  asserted a pre-registration that had not happened. It is replaced by an explicit
  `threshold_derivation`, a `comparator_measured_at` timestamp that must precede the
  policy, and `candidates_under_test_unevaluated`, which is the claim the policy can
  actually support. Declaring a comparator under any other derivation now fails closed
- complete: a second, naive comparator floor is enforceable alongside the tuned one. A
  tuned comparator encodes this suite's own iteration history, so non-inferiority against
  it cannot detect the two systems overfitting together; an untuned lexical baseline can.
  The gate is implemented and unpopulated - it activates once that baseline is measured
- complete: the naive floor is measured, and it changes the reading of the accepted
  candidate. `who-smart-hiv-naive-bm25-floor-v1` is the accepted candidate with both
  expansions disabled; its sparse mode is release BM25 on raw queries, which is the
  untuned lexical baseline. On the exact 430-case v7 suite it scores 0.9608 answerable
  complete-evidence (245/255, Wilson 95% [0.9293, 0.9786]), report
  `9102f8ad07a36eaf2b10dad9e79b55977c4455cc56c99bce7240f8d9749945ad`
- the accepted Qwen3-Embedding-0.6B/BM25 conflict-aware candidate scores 0.9647
  (246/255). Against BM25 alone that is one case in 255, inside both confidence
  intervals. The +0.137 margin the candidate was accepted on was measured against
  `who-smart-hiv-deterministic-expanded-control-v3`, whose lanes are the
  `baseline-hashing-dense` and `baseline-hashing-sparse` plumbing stubs, so the tuned
  comparator is a synthetic control rather than a retrieval system and 0.8275 is not a
  lexical baseline. This is the case the naive floor was built to catch
- observed: query expansion does not change the single-lane ablations at all. The
  expanded and unexpanded runs report byte-identical sparse (0.9608) and dense (0.6824)
  summaries, so expansion acts only on the fused path. Its measured effect there is to
  repair fusion rather than to add recall - unexpanded hybrid is 0.9137, below BM25
  alone at 0.9608, because fusing a 0.6824 dense lane with a 0.9608 sparse lane loses
  cases; expansion brings the fused score back to 0.9647. The mechanism is worth
  confirming in code before this is relied on
- decision needed: the naive margin. At `minimum_naive_comparator_margin` 0.0 the
  candidate clears the floor by one case. At the 0.02 recorded below for the tuned
  comparator it does not clear it at all, because 0.9608 + 0.02 exceeds 0.9647. The
  policy ships the measurement with the margin left at its existing 0.0; raising it is a
  decision about whether a one-case gain justifies a neural dense lane and its latency
- known limit: the naive floor is populated for `development_acceptance` only. A
  comparator floor is defensible because it was measured on the same case set, and this
  one was measured on the development suite. Pinning it into `sealed_holdout_acceptance`
  would compare holdout performance against a development-measured baseline; measuring
  a naive floor on the holdout instead would consume that suite's one permitted
  execution. The tuned comparator is already pinned into holdout acceptance from a
  development measurement, which has the same defect and predates this work
- discrepancy: this entry claims 0.02, and the sealed artifacts say 0.0.
  `minimum_comparator_margin` is 0.0 in both acceptance blocks of
  `automated-thresholds-v1` and in the v7 suite's embedded acceptance. The margin was
  never applied, so the tuned floor currently permits an exact tie
- discrepancy: `comparator_report_sha256` `d15276f6...` names a run against
  `who-smart-hiv-source-derived-development-v6`, while the sealed suite is v7. The run
  is in the execution ledger and its metrics are identical to the v7 re-run
  (`460e6d68...`), because v7 is v6's cases re-sealed under contract 1.6 - but the pin
  references a different suite digest than the one under test
- complete: the non-inferiority margin is 0.02 rather than 0. Tying a deterministic
  control is not evidence that a neural stack earns a 10x latency cost
- complete: sampling moved off uniform 25s onto measured headroom. The comparator scores
  1.00 on five lexical strata and on every negative stratum, so 8 of 12 strata cannot
  discriminate between candidates at all; all measurable difference lives in
  PARAPHRASED_INTENT (0.68), TERMINOLOGY (0.72), and CONFLICTING_EVIDENCE (0.80). Those
  three go to 60 development cases, the saturated lexical strata drop to 15, and the
  tightened Wilson bounds land where discrimination actually happens
- complete: the paraphrase lexical-rank band no longer tracks the retrieval depth. With
  the upper bound equal to `top_k`, every paraphrase case sat inside the serving depth by
  construction and a candidate could improve its own score by widening its output; the
  band is now an independent constant above any serving depth, and generation rejects a
  `top_k` that reaches it
- decision: `minimum_mean_required_role_recall` stays ungated. Complete-evidence-set
  membership already requires every required role to be retrieved, so the role gate was
  a second reading of the same quantity rather than an independent safety check
- known limit: `generation_context_budget` equals `top_k`, so `complete_evidence_at_budget`
  is currently identical to `complete_evidence_set`. It is forward-looking infrastructure
  and separates only when the rendering layer is handed fewer passages than are retrieved
- known limit: `minimum_insufficient_evidence_accuracy` is 1.0 over a partition whose
  negatives are detectable by release filter alone, so it currently certifies the filter
  rather than abstention. It will not survive negatives that are plausible instead of
  filtered, and the abstention literature puts frontier models below 50% on that task
- complete: a provider-swappable grounded-answer lane. Claude is reached through the
  Anthropic SDK's platform clients on Amazon Bedrock or Google Vertex, selected by a
  sealed parameter file, so changing provider or model is a config change rather than a
  code change. Two platform limits are handled explicitly: automatic prompt caching is
  unavailable on both, so the cache breakpoint is placed on the frozen instruction block,
  and server-side refusal fallbacks are unavailable on both, so a refusal becomes an
  abstention instead of a silent retry against a different model
- complete: the answer lane treats model output as a proposal, never a result. No
  retrieved evidence means abstention decided before any model call; every claim must
  cite evidence IDs drawn from the set actually retrieved for that question, and a claim
  citing anything else is discarded whole rather than repaired; if nothing survives, the
  result is an abstention even when the model reported sufficient evidence. The model's
  sufficiency signal can lower an outcome to abstention but can never raise one to an
  answer, because a model given insufficient context answers more confidently, not less
- known limit: the answer lane is unit-tested against a stubbed backend only. No live
  Bedrock or Vertex call has been made, and no generation candidate has been benchmarked
- complete: sample mass rebalanced away from the strata that cannot separate systems. The five
  verbatim-fragment topics carry 15 cases each; `TERMINOLOGY`, `CONFLICTING_EVIDENCE`, and
  `PARAPHRASED_INTENT` carry 60. On the rebalanced 430-case suite the deterministic control
  falls from 0.9000 to 0.8275 answerable complete-evidence with no change to the baseline
  itself, which is the mix doing its job
- complete: the prespecified comparator floor is measured, not chosen. The deterministic
  expanded control scores 0.8275 (Wilson 95% [0.7763, 0.8689]) on the exact case set, and that
  measurement is frozen into the threshold policy before the candidate is evaluated; per-stratum
  floors are each stratum's own Wilson lower bound
- complete: the Qwen3-Embedding-0.6B/BM25 conflict-aware candidate is ACCEPTED on the
  development suite with no blockers - 0.9647 answerable complete-evidence (Wilson lower 0.9343)
  against a 0.7763 lower-bound gate and a 0.8275 comparator floor, required-role recall 0.9647,
  zero forbidden leakage, zero candidate failures, p95 1,673 ms. Development acceptance is not
  clinical validation and the sealed holdout is untouched
- complete: a bounded-expander defect found by the wider conflict stratum. One deterministic
  terminology expansion reduced to a bare punctuation fragment, which a lexical backend rejects
  outright and which aborted an entire run. Non-searchable variants are now dropped at the
  expansion boundary. On the sealed holdout the same fault would have consumed a one-time
  custody-controlled execution claim, because the ledger binds the candidate even when a run
  fails
- next: freeze a complete candidate and run the untouched sealed holdout exactly once

Development-suite scores must be read against how the suite is built. Its queries are
normalized source fragments, so most strata reward exact lexical overlap and BM25 alone reaches
0.9927 recall while the dense lane reaches 0.9291. A high blended score on this suite is
evidence that near-duplicate lookup works, not that clinical retrieval works.

The completed BGE-M3/BM25 path is candidate A and a control, not a clinically validated or
best-available stack. Public leaderboards cannot establish product quality. No candidate
becomes the selected engineering release until it passes the sealed engineering holdout and
signed acceptance gate. Engineering acceptance is not clinical validation, certification,
or evidence that the product is safe for autonomous clinical use.

### In parallel: prove the real candidate runtimes

- run the pinned BGE-M3 weights through the real Torch/Transformers adapter on every
  supported device/dtype; record numerical parity, truncation, memory, throughput, latency,
  timeout, and recovery behavior
- complete: derive and seal BM25 statistics from the exact approved release: 5,145 documents,
  average document length 86.36793002915452 tokens, and p95 length 204 tokens
- complete: measure pinned Qwen3-Embedding-0.6B CPU/fp32 against 32 development queries and
  documents with three timed runs: 1.47 query items/s, 0.405 document items/s, 5.38 GiB peak
  process RSS, no truncation, and maximum unit-norm deviation 2.22e-15; the exact report is
  digest-sealed under `benchmarks/runtime/`
- perform a controlled Qdrant 1.15.4 versus 1.19.x compatibility and relevance benchmark;
  retain the old pin until collection, filtering, payload, sparse-IDF, and attestation
  contracts pass unchanged or are deliberately versioned

### Next: manifest-driven retrieval candidate matrix

- acquire, independently pin, and measure the Qwen3-Embedding-4B and 8B artifacts on runtime
  targets with adequate memory; their current CPU-matrix entries fail closed as missing-artifact
  blockers and contain no inferred performance numbers
- use `Qwen3-Embedding-4B` plus `Qwen3-Reranker-4B` as the primary practical development
  preset; benchmark the 8B variants as the quality-ceiling preset where target hardware
  satisfies the sealed memory and latency budgets, and retain the 0.6B variants as the
  deployment-floor preset
- treat Qwen-first as build priority rather than pre-acceptance: every size, instruction,
  precision, truncation, and quantization combination is a distinct pinned candidate and
  must pass the same engineering development suite and untouched acceptance holdout
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
- label the resulting activation as engineering acceptance only; do not present it as
  clinician approval or clinical validation
- all benchmark construction, candidate selection, acceptance checks, and release gates are
  automated; physician participation begins only with final-product evaluation

## Then: verified rendering

- complete: deterministic atomic claim planning. A model claim is split on sentence
  terminators and semicolons, never on coordinating conjunctions, and every part must
  pass independently. This closes the case where a fabricated half rides along on a
  grounded half because every number in the sentence occurs somewhere in the cited
  passages. Coordination stays inside one unit deliberately: "500 mg and 1 g" and
  "monitor CD4 and viral load" have the same surface shape and opposite correct splits
- complete: numeric, unit, operator, quote, and provenance validators, three-state and
  fail-closed. A validator reports `UNSUPPORTED` only for a positive mismatch and
  `UNRESOLVED` when it cannot read the evidence it was asked to check, so restricted
  evidence withholds a claim rather than passing it unchecked. Only `SUPPORTED` claims
  render; `verification_status` stopped being a constant string. Abstention now
  separates `NO_CLAIM_SURVIVED_GROUNDING` from `NO_CLAIM_SURVIVED_VERIFICATION`,
  because a citation defect and a content defect point at different faults
- known limit: these validators check surface features, not entailment. A claim that
  reuses the evidence's own numbers, units, and thresholds in the wrong relation passes
  every one of them. What they decide completely is fabrication - a value, unit, bound,
  or quotation that never appeared in the cited evidence at all
- known limit: normalization sets the false-positive floor. Written-out numbers, unit
  aliases, and both operator orders are handled, and unrecognized unit tokens degrade to
  a bare numeric check rather than inventing a mismatch, but the coverage is a fixed
  clinical vocabulary rather than a general one. It has not been measured against real
  generated answers, only against unit fixtures, because no live generation candidate
  has been run
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
- complete: withheld-claim diagnostics reach the API as per-validator counts and never
  as text. A withheld claim is unverified model output, so putting it in the response
  under a diagnostic name would hand it to exactly the reader the check protects; the
  text stays inside the service. `VerificationSummary` now enforces that supported plus
  withheld equals rendered, on both sides of the boundary, and the answer panel names
  which part of a claim failed rather than which module decided it
- evidence cards exist and carry page and printed-page locators; exact PDF highlighting
  does not, and is blocked rather than unbuilt. `EvidenceLocator.bbox` is already
  populated and unused, but drawing it requires serving the source PDF to the browser,
  and `AssetLicensingPolicy.redistribution_allowed` defaults to false per asset. Whether
  a given publisher's PDF may be redistributed to a clinician's browser is a licensing
  determination, not an engineering one, and it has not been made for any asset. Until
  it is, the honest surface is the locator reference the cards already show
- blocked: decide per-asset redistribution for rendering before building a PDF viewer.
  If redistribution is refused for a publisher, exact highlighting for that publisher
  needs a different design - a rendered page image produced under the materialization
  policy rather than the source file itself

## Finally: clinician review of the complete product

- involve physicians only after the end-to-end product, retrieval stack, verification layer,
  evidence presentation, and failure behavior are complete and internally accepted
- have clinicians evaluate the finished user experience and representative outputs for
  usefulness, citation fidelity, unsafe omissions, contraindications, applicability, dose,
  monitoring, jurisdiction, freshness, and abstention behavior
- treat clinician findings as final-product evaluation feedback rather than as labor for
  building or tuning the retrieval benchmark
- route any resulting product change back through development evaluation and a newly sealed
  holdout cycle; never tune against the previously opened holdout
- make no clinical-validation, certification, or autonomous-use claim unless a separately
  designed external clinical evaluation supports it
