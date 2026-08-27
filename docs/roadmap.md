# Implementation roadmap

## Current direction: one narrow corpus, answered end to end

Decided 2026-08-27. The MVP is **WHO SMART HIV only** — the existing validated release of
5,145 approved records. The goal is a product that answers a clinical question from that
corpus, refuses convincingly when evidence is incomplete, and shows its sources. Corpus
breadth is explicitly deferred.

The reasoning: build-side rigor is far ahead of product surface. Signed attestations,
immutable releases, and a benchmark that has survived three self-corrections all exist,
and nothing has ever answered a question. Every additional benchmark refinement has lower
marginal return than reading real outputs. Breadth first would still leave nothing
answerable end to end; depth first yields a working product on a narrow corpus.

Priority order, superseded by [mvp-definition.md](mvp-definition.md):

1. one live generation call against Vertex — narrowed there to de-risking the plumbing
2. build the question set from the parent consolidated guidelines — moved ahead of the
   reading, because extraction is cheap and gives the reading a real sampling frame
3. run the two-stage coverage measurement; the decision point for everything below it
4. harden abstention against plausible negatives, sourced from step 3's correct abstentions
5. evidence cards and exact PDF highlighting, once the render licence is decided
6. corpus breadth via narrative-only materialization — or promoted to blocking by step 3

[mvp-definition.md](mvp-definition.md) states what "done" means, why the question set may
not come from the corpus, and the pre-registered rule the coverage measurement decides
under. The original list read "read 50 real outputs" before "write 30–40 questions" and
fixed no threshold for either; n = 50 cannot resolve a coverage threshold near the middle
of the range, so the measurement is now staged and the decision rule is written down
before the reading rather than after it.

Explicitly **not** now: spending the sealed holdout, adding publishers, adding benchmark
machinery. Frame the product as a WHO HIV guidelines assistant, not a clinical guidelines
assistant — the narrower claim is both true and the stronger position.

### Blocking licence decision

Every WHO asset is `render_allowed: false` pending deployment-specific licence review.
Evidence cards and exact highlighting — the visible payoff of all the anchor and locator
work — are gated on resolving that. CC BY-NC-SA 3.0 IGO plausibly permits attributed
non-commercial display, but this is a decision to be made and recorded, not a code change.

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

## Complete: serving retrieval path and first contact with real questions

- `ServingRetrievalService` (`app/reasoning/retrieval_service.py`): release-filtered
  hybrid dense/sparse retrieval, weighted RRF, deterministic tie-breaking, isolated lane
  failures, and an evidence-role completeness gate that blocks generation rather than
  reporting a metric afterwards
- deliberately separate from `RetrievalBenchmarkRunner`. That runner is the measured
  system: the accepted development report and the frozen comparator floor were produced
  by that exact code path, so reshaping it to serve free-text questions would silently
  break comparability with every prior report. The payload-integrity checks are
  reproduced rather than shared, because failing closed on a corrupted index is a safety
  property both paths need
- `scripts/ask.py`: asks one question end to end against a validated release without
  activating it, so no sealed-holdout claim is consumed to look at an answer
- verified against the real release: 5,145 records, ~320 ms, role gate passing

### First-contact findings — the corpus does not answer clinical questions well

Three real questions against the accepted candidate, and the result reframes the 0.9647
development score. The score is not wrong; it measures what the suite is built from.
Because every benchmark query is a normalized source fragment, the suite rewards
near-duplicate lookup, and near-duplicate lookup is exactly what works here.

- *"When should ART be started in adults with HIV?"* returned two monitoring-indicator
  spreadsheet rows, two near-duplicate data-dictionary rows about **infants**, and a page
  of references. Nothing answered the question.
- *"What are the contraindications to dolutegravir?"* returned data-dictionary rows
  defining `DTG` as an input-option code, labelled `PRIMARY_SUPPORT`.
- *"How often should viral load be monitored during ART?"* worked — the decision-support
  logic genuinely encodes 6-month and 12-month intervals and the >1000 copies/mL
  threshold.

Three distinct defects, in increasing order of seriousness:

1. **Passage text carries spreadsheet coordinates.** `content_exact` for XLSX rows reads
   `A145=HIV.D8 … B145=HIV.D.DE144 …`. Correct as an exact anchor, unusable as passage
   text for either a model or a reader. A separate presentation rendering is needed;
   the anchor must not change.
2. **Near-duplicate pollution.** The same logical row recurs at different sheet offsets
   and consumes top-k slots — items 1 and 2 were the same content in two of three
   questions.
3. **Role labels overstate support.** A code-list entry labelled `PRIMARY_SUPPORT` means
   the role-completeness gate is satisfied by evidence that supports no clinical claim.
   This is the safety-relevant one: the gate meant to prevent unsafe answers currently
   passes on data-dictionary rows.

Underlying all three: the DAK is operational and tabular — data dictionaries, decision
logic, indicators. It answers "what data element records this" far better than "what
should a clinician do". Where the decision logic covers a question, retrieval is good.
Where the question needs clinical narrative, the corpus largely does not hold it.

This is evidence for the narrative-only materialization path, not against it — the WHO
consolidated HIV guidelines are narrative PDFs and would carry exactly the missing
content.

### Re-measured after the presentation fix

All three questions re-run against the same release, with and without `--expand`, six runs
at 246–359 ms. The presentation layer did what it claims; the corpus did not improve.

- **Q1, ART initiation timing.** The infant rows are gone — zero occurrences of "infant"
  in the top ten either way — but the reclaimed slots went to more ART-regimen rules, and
  pushing to depth 30 surfaces nothing that states a start time. **This is a corpus fact,
  not a ranking one.** The corpus holds the timing vocabulary ("within 7 days" ×56,
  "same day" ×16) almost exclusively as *reasons for not initiating*: a coded list of
  delay reasons each phrased "did not initiate ART at diagnosis or within 7 days
  because…", plus a Boolean recording whether the target was met. The recommendation is
  never asserted; it exists only as the negative space those fields are defined around.
  No ranking can retrieve an assertion the corpus does not contain.
- **Q2, dolutegravir contraindications.** The earlier entry above overstated the plain
  result. Measured: six data-dictionary code-list entries, one PrEP-suitability rule, two
  boilerplate narratives, one table section title. The genuinely relevant record — the
  DTG-plus-metformin interaction rule — sits at rank 19 plain and only reaches rank 10
  with `--expand`. That is the one place expansion earned its keep across all six runs.
- **Q3, viral-load monitoring interval.** Plain is clean and correct. `--expand` is a
  **regression**: it drops the single passage that literally states the 6-month,
  12-month, and every-12-months intervals, replacing it with paediatric
  regimen-transition rules that merely contain the phrase "viral load testing". Expansion
  displaces the best passage on the one question this corpus actually answers.

Two findings outrank the rest.

**The role gate passes on off-topic evidence.** Q2 reported `answerable: true` when its
only countable `PRIMARY_SUPPORT` was a *tenofovir* contraindication. The gate proves
completeness of kind, never of subject, and cannot be made to prove subject without
becoming a relevance judgement. Recorded as a known limit at `REQUIRED_ANSWER_ROLES`.
What actually protects the reader is claim-level grounding in the answer lane, which
discards a claim whose cited passages do not support it.

**Expansion is not uniformly positive.** It rescued one question and broke another. Its
value is not established, and the sealed candidate that was development-accepted uses it.
This is independent corroboration of a conclusion reached on the unmerged
`m2-verified-rendering` branch — "the dense lane is correctly configured; the benchmark is
the problem" — reached there by paired significance testing. Read that branch before
tuning retrieval further.

## Complete: serving presentation layer

All three first-contact defects are addressed in the serving layer. `content_exact`,
anchors, and evidence digests are untouched: `app/reasoning/presentation.py` is a *view*
over approved records, and citation stays bound to `evidence_id`, so the QA ledger and
the signed release still describe exactly what is served. The three roadmap questions now
return readable, distinct passages.

- **Defect 1, coordinates in passage text — fixed.** `PassagePresenter` parses the
  extractor's cell-addressed form back into cells and renders them under the publisher's
  own column labels. `A145=HIV.D8 … E145=Input Option …` becomes
  `HIV.D Care-Treatment - data element HIV.D.DE144 DTG` followed by labelled facts.
  Decision-table rows are rendered as a `When:`/`Then:` split, derived from the position
  of the publisher's own `Output Type` column rather than assumed. Labels for the
  thirteen Annex B decision tables are read out of the release itself — their header rows
  were approved as evidence, so the labels cannot drift from what is being served — and
  the Annex A data dictionary, Annex B schedules, and Annex C indicator sheets are
  declared, because their header rows were quarantined as `HEADER_OR_FOOTER`. A table
  with no schema still renders without coordinates, and an ambiguous header is skipped
  rather than resolved.
- **Defect 2, near-duplicate pollution — fixed, and it was not "near".** The cause is
  exact: the Annex A `all` worksheet is a verbatim copy of all thirteen topic worksheets
  with one extra column recording which tab each row came from. QA's exact-content
  deduplication could not see it, because that extra column changes `content_search`.
  **2,080 of 5,145 approved records (40.4%) are second copies**, and the release holds
  3,065 distinct passages. Suppression is exact rather than thresholded: the fingerprint
  covers every substantive cell, the `Tab` column is declared structural, and the measured
  collision count matches the copy count exactly, so nothing merges that should not.
  Suppression runs before top-k, the highest-ranked copy survives, and the suppressed ID
  is recorded against the passage that displaced it.
- **Defect 3, role labels overstate support — mitigated at serving; the durable fix is at
  QA time.** See below.

### Finding: role labelling is wrong at the point it is written

`qa_classification._base_roles` assigns roles by *source asset*, not by content. Every
Annex A row gets `PRIMARY_SUPPORT` because Annex A is the data dictionary; every Annex B
row gets it because Annex B is decision support. So a code-list entry defining `DTG` as
an input option, a row of column headings, and a section title all arrive labelled as
stating a recommendation.

Measured over the release: **4,664 of 5,145 approved records (90.7%) carry
`PRIMARY_SUPPORT`, and 4,244 of those (91.0%) cannot bear a recommendation** — 4,211
data-dictionary entries, 16 section titles, 13 table headers, 4 caption rows. The
release's genuinely recommendation-bearing surface is **420 records**, not 4,664. That is
the sharpest statement yet of why this corpus does not answer clinical questions, and it
is independent evidence for narrative-only materialization.

The durable fix is the classifier, and it is deliberately **not** made here. `evidence_roles`
is inside `CorpusEvidenceRecord`, so re-deciding it changes every affected evidence digest
and therefore the manifest, the release, the Qdrant collection, and the comparability of
every benchmark report produced against it. That is a re-release, not a patch, and it
should be done once — alongside narrative materialization — rather than twice.

What serving does instead is stop the abstention gate resting on a claim it never
verified. `ROLES_REQUIRING_RECOMMENDATION` qualifies `PRIMARY_SUPPORT` on the passage's
*form* — `presentation.RECOMMENDATION_BEARING_KINDS`, never a reading of what the passage
says — so a data-dictionary entry, header, caption, or section title cannot satisfy it. An
unrecognised table cannot either, because the view cannot tell what it is and the gate
exists to fail closed. This is the same argument as reproducing the payload-integrity
checks rather than trusting the index. It is narrow by design: only `PRIMARY_SUPPORT` is
qualified, and disqualified claims are reported rather than hidden, so an answer withheld
because of the corpus's labelling says so.

Effect on the three questions, at `top_k=10` with the accepted conflict-aware candidate:

- *"When should ART be started in adults with HIV?"* — ten distinct passages, eight of
  them ART-regimen decision rules; one copy suppressed, one role claim not counted.
  Previously: two indicator rows, two copies of a data-dictionary row about infants, and
  a page of references.
- *"What are the contraindications to dolutegravir?"* — five copies suppressed, six role
  claims not counted. Still answerable at depth 10, but now on two drug-interaction
  decision rules rather than on a code list; at `top_k=5` it abstains with
  `MISSING REQUIRED ROLES: PRIMARY_SUPPORT`, which is the correct outcome and was not
  reachable before.
- *"How often should viral load be monitored during ART?"* — unchanged and clean: the
  `HIV.S.2` schedule entries carrying the 6-month, 12-month, and >1000 copies/mL rules,
  no duplicates, no disqualified claims.

Two consequences worth carrying forward. Which copy of a duplicate pair survives is
decided by rank, not by worksheet, so a citation can point at the `all` worksheet rather
than the topic sheet; both are approved records anchored to real cells, so this is
correct but reads worse.

And serving and the measured system have now diverged in a way they had not before.
`RetrievalBenchmarkRunner` does not deduplicate, so at `top_k=10` it is scored on
rankings that can spend up to half their depth on copies, while serving is not. The
0.9647 development acceptance therefore measures a slightly different system from the one
that answers a question. This is not urgent — suppression can only raise the number of
*distinct* passages at a given depth — but it means a later decision: either the runner
gains the same suppression, which mints a new benchmark under contract 1.6 and requires
re-measuring the comparator floor, or the divergence is recorded as a known and bounded
difference. Do not fold it into an unrelated change.

## Deferred: additional authoritative publishers

Deferred under the current direction; the connector work below is built and parked rather
than pending.

- complete: `who-guidelines-hub` connector over the official WHO publications OData
  catalogue (358 GRC-approved guidelines), with IRIS handle resolution, recorded
  acquisition blockers, and reproducible inventory fingerprints
- parked: `data/trust-roots/who-guidelines-ncd.json`, an authored but `enabled: false`
  NCD expansion candidate. It cannot be reconciled to evidence until narrative-only
  materialization exists
- blocked: PDF-only sources cannot reach evidence. `MaterializationService.materialize`
  requires a structured report, which on the HIV path came from FHIR package processing
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
  Bedrock or Vertex call has been made, and no generation candidate has been benchmarked.
  Vertex is the selected platform. `scripts/ask.py --generate` is the path to the first
  live call and needs only `MEDRAG_VERTEX_PROJECT_ID` plus application-default credentials
- superseded by `a0cb8f2`: `QuestionService._run` is no longer a stub. It runs real
  retrieval, the real role gate, real grounded composition, and canonical detail
  resolution, and `ReleaseServingPipeline` binds collection, vector names, and evidence
  map to one release together
- known limit: the *application* is still not wired. `main.py` constructs
  `QuestionService` with an `active_release_provider` and no `pipeline`, so a deployed API
  still abstains with `RETRIEVAL_PIPELINE_NOT_CONFIGURED` and `scripts/ask.py` remains the
  only path that answers. The seam exists and nothing constructs it at lifespan; that
  needs a settings surface for collection, vector names, and model roots, none of which
  `core/config.py` carries yet. Deliberately not near-term - see
  [mvp-definition.md](mvp-definition.md)
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
- deferred: freezing a candidate and opening the sealed holdout. Under the current
  direction the holdout stays unspent, for three reasons: acceptance binds the release,
  so any later corpus change voids it; the 0.6B candidate is the declared deployment
  floor rather than the intended selection, and the 4B/8B artifacts are not yet acquired;
  and the first-contact findings above indicate the corpus and chunking will change
  before a candidate is worth freezing. The holdout is a one-shot resource and this is
  not the moment to spend it

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
