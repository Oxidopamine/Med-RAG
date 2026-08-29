<div align="center">

# Sentinel Evidence

**A fail-closed, provenance-bound retrieval and answer system for infectious disease practice guidelines**

*Research artifact — not authorized for patient care. Do not enter protected health information.*

[![Status](https://img.shields.io/badge/status-research%20prototype-blue)](docs/roadmap.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](apps/api/pyproject.toml)
[![Node](https://img.shields.io/badge/node-20%2B-339933)](package.json)
[![Corpus](https://img.shields.io/badge/corpus-WHO%20SMART%20HIV-orange)](docs/corpus-steward.md)
[![Serving](https://img.shields.io/badge/serving-RESEARCH__UNACTIVATED-lightgrey)](#7-reproducibility)

</div>

---

## Abstract

Retrieval-augmented generation over clinical guidelines is usually evaluated as a retrieval
problem and deployed as a text-generation product. This repository takes the opposite position:
the hard part is not producing an answer but **refusing to produce one**, and the system's
contribution is an end-to-end chain in which every rendered clinical claim is bound by
construction to an authenticated source artifact, an exact locator, a cryptographically attested
corpus release, and a deterministic safety policy that fails closed at every stage.

The implementation instantiates that chain over one real corpus — the WHO SMART Guidelines
Digital Adaptation Kit (DAK) for HIV — from authenticated acquisition through signed release
validation, model-pinned index construction, sealed benchmark acceptance, hybrid retrieval, and
grounded answer composition. It then does the thing such systems are rarely subjected to: it
**measures what the corpus can actually answer** under a pre-registered decision rule, and reports
that the headline retrieval score (0.9647) and the clinical answerable coverage (0.4631) are
measurements of two different things.

The negative results are the informative ones and are reported in full: 40.4% of the release is
exact duplicate content invisible to content-hash deduplication, 91.0% of the records labelled
recommendation-bearing structurally cannot bear a recommendation, and coverage concentrates on the
business processes the kit operationalizes — 29.6% on coinfections and comorbidities against 67.9%
on testing and diagnosis. Each is traced to a structural property of the source rather than to a
ranking defect. Two of the three are then checked against the other WHO adaptation kits, where the
duplication turns out to be specific to HIV and the labelling defect turns out to be general.

A fourth result is reported against the architecture rather than for it: switching every safety
mechanism off changes coverage by one question out of 49. The chain is nearly free, and on this
corpus it is also largely untested, because the failure it exists to prevent does not occur here.

---

## Table of contents

1. [Problem statement and scope](#1-problem-statement-and-scope)
2. [Contributions](#2-contributions)
3. [System architecture](#3-system-architecture)
4. [Design decisions and their rationale](#4-design-decisions-and-their-rationale)
5. [Evaluation](#5-evaluation)
6. [Threats to validity and known limitations](#6-threats-to-validity-and-known-limitations)
7. [Reproducibility](#7-reproducibility)
8. [Repository map](#8-repository-map)
9. [Data governance and licensing](#9-data-governance-and-licensing)
10. [Citation and references](#10-citation-and-references)

---

## 1. Problem statement and scope

A clinical guideline assistant has one catastrophic failure mode: **a wrong answer carrying a real
citation**. It is worse than no answer, worse than a visibly broken answer, and worse than an
abstention, because the provenance apparatus that should protect the reader is exactly what makes
the wrong claim credible. Every architectural decision in this repository descends from treating
that failure as the thing to design against.

The system enforces one invariant end to end:

> A clinical claim is never rendered unless authorized evidence, exact provenance, applicability,
> counter-evidence retrieval, and every policy-required verification check pass.

**Scope.** The current vertical is WHO SMART HIV only — one validated release of 5,145 approved
records, answered end to end. Corpus breadth is deliberately deferred: build-side rigor was far
ahead of product surface, and every additional benchmark refinement had lower marginal return than
reading real outputs. Framing the product as a *WHO HIV guidelines assistant* rather than a
*clinical guidelines assistant* is both the true claim and the stronger one.

**Explicitly out of scope**, and not reachable from the current state: clinical validation,
clinician approval, sealed-holdout acceptance, release activation, deployment.

---

## 2. Contributions

| # | Contribution | Detail |
|---|---|---|
| **C1** | A **two-plane architecture** separating an offline, cryptographically attested corpus build plane from a serving plane that cannot mutate corpus state, with a signed release as the only interface between them | [§3.1](#31-two-plane-separation) |
| **C2** | **Anchor-replay QA**: every evidence record's locator is replayed against preserved source bytes before the record can enter a release; any replay failure, incomplete classifier output, duplicate approved content, or empty approval set fails the release closed | [§3.3](#33-build-plane-the-corpus-steward) |
| **C3** | A **presentation-as-view** layer that makes cell-addressed spreadsheet evidence readable without re-materializing it, preserving digest, anchor, and citation stability | [D4](#4-design-decisions-and-their-rationale) |
| **C4** | An **evidence-role completeness gate qualified by passage form**, which blocks generation before any model call and cannot be satisfied by evidence whose structural form cannot carry a recommendation | [D5](#4-design-decisions-and-their-rationale) |
| **C5** | A **grounded answer lane in which model output is a proposal**: the model's sufficiency signal can lower an outcome to abstention but can never raise one to an answer, and any claim citing evidence outside the retrieved set is discarded whole rather than repaired | [D7](#4-design-decisions-and-their-rationale) |
| **C6** | A **sealed benchmark contract** with a measured (not chosen) comparator floor, per-stratum Wilson lower bounds, sample mass allocated by measured discriminative headroom, and a custody-controlled holdout that remains unspent | [§5.1](#51-retrieval-benchmark-development-suite) |
| **C7** | A **pre-registered two-stage coverage measurement** whose question set is sampled from the corpus's *parent* guideline rather than from the corpus itself, instantiated through a published clinical-question taxonomy, with an explicit lexical-leakage screen | [§5.2](#52-answerable-coverage-pre-registered-two-stage-measurement) |
| **C8** | Three **negative structural findings** about operational guideline corpora — exact duplication invisible to content hashing, asset-level role labelling, and coverage concentrated on operationalized business processes — two of them re-measured across four WHO adaptation kits, where the duplication proves HIV-specific and the labelling defect proves general | [§5.3](#53-corpus-structure-findings) |
| **C9** | An **ablation of the safety chain against itself**, showing it costs one answer in 49 and that its claim-grounding stage has never fired in 241 observed claims — the cost objection answered, and the chain's own coverage recorded as untested | [§5.4](#54-what-the-safety-chain-costs-measured-against-itself) |

---

## 3. System architecture

### 3.1 Two-plane separation

```mermaid
flowchart LR
  subgraph BUILD["BUILD PLANE — corpus-steward (offline, signed)"]
    direction TB
    A[Trust root and<br/>publisher registry] --> B[Inventory<br/>reconciliation]
    B --> C[Structured source<br/>processing]
    C --> D[Evidence<br/>materialization]
    D --> E[Anchor-replay QA]
    E --> F[Index build<br/>and validation]
    F --> G[Sealed benchmark<br/>acceptance]
  end

  subgraph SERVE["SERVING PLANE — FastAPI + Next.js (online, read-only)"]
    direction TB
    H[Question] --> I[Hybrid retrieval:<br/>dense + sparse, weighted RRF]
    I --> J[Payload-integrity check<br/>against canonical evidence]
    J --> K[Presentation view and<br/>exact duplicate suppression]
    K --> L{Evidence-role<br/>completeness gate}
    L -->|incomplete| M[Abstain before<br/>any model call]
    L -->|complete| N[Grounded composition]
    N --> O{Claim-level<br/>grounding}
    O -->|survives| P[Cited claims]
    O -->|nothing survives| M
  end

  G -.->|"signed release bundle<br/>(the only interface)"| H
```

The planes run as separate process entry points. `corpus-steward` — a console script with ~55
subcommands — holds every mutation of corpus state; the API holds none. **No build-side command
can move the active-release pointer except the one signed activation gate**, and no serving
request can write corpus state at all.

### 3.2 Authority boundary

The authority boundary is the **authenticated source artifact, canonical evidence object,
structured clinical semantics, and deterministic safety policy**. Model output is never accepted
as citation metadata or source truth.

PostgreSQL is the canonical registry (52 tables, 16 Alembic migrations). **Qdrant is a rebuildable,
release-specific projection and never a source of truth**: passage text is read from canonical
evidence records, the index carries only filterable metadata, and a point whose payload disagrees
with the canonical record is a corrupted index that both the serving and benchmark paths fail
closed on.

### 3.3 Build plane: the corpus steward

Each stage emits an immutable artifact and an Ed25519 attestation over it, and each has a distinct
non-zero exit status so that a *signed policy block* is never mistaken for a crash.

| Stage | Gate enforced | Fail-closed signal |
|---|---|---|
| **Acquisition** | Registered publisher, HTTPS domain allowlist, redirect revalidation, size limit, SHA-256 content addressing | IP literals, user info, non-HTTPS ports, cross-publisher redirects, private networks, encrypted or malformed PDFs, hash mismatch |
| **Inventory reconciliation** | Exact accounting — every configured item included or explicitly excepted, with signed, bounded exception records | Incomplete inventory |
| **Structured processing** | Bounded, non-extracting FHIR package validation; package identity, lifecycle, version, licence, controlling-narrative and dependency-closure gates | exit 5 |
| **Input resolution** | Independently preserved narrative assets and digest-verified transitive package closure | exit 6 |
| **Materialization** | Per-asset acquisition, redistribution and rendering policy; deterministic PDF-page and XLSX-row extraction with source anchors; complete coverage accounting | exit 7 |
| **Evidence QA** | Replay of every PDF/XLSX anchor against preserved bytes; immutable complete approve/quarantine decision batch; three signed release attestations | Replay failure, incomplete classification, duplicate approved content, zero approved records |
| **Index construction** | Sealed, model-pinned vector batch; bundle ↔ PostgreSQL ↔ QA-ledger reconciliation; exhaustive point, payload, vector, dimension and collection validation; dense and sparse self-retrieval smoke tests | Any mismatch |
| **Benchmark acceptance** | Sealed sparse/dense/hybrid ablations against a frozen comparator floor and per-stratum Wilson lower bounds | exit 8 |
| **Activation** | Signed activation decision requiring a matching, unexpired sealed-holdout acceptance record | Blocked; pointer unchanged |

QA reserves a release-specific collection **name** only — it creates no index and does not activate.
Index construction validates and signs, and does not activate either. Activation is one database
transaction that moves the singleton pointer, transitions release state, and emits the outbox event
together.

### 3.4 Serving plane

```text
question
  -> deterministic bounded expansion (no generative query drift)
  -> release + approval + lifecycle filtered dense/sparse search over one collection
  -> weighted RRF (k = 60) with deterministic tie-breaking and isolated lane failures
  -> payload-integrity check against canonical evidence
  -> presentation view: render, fingerprint, classify form
  -> exact duplicate suppression, then top-k selection (k = 10)
  -> evidence-role completeness gate, qualified by passage form
  -> grounded answer composition, or abstention before any model call
```

Question jobs pin the active release at submission, so an activation cannot move an in-flight
request to another corpus.

Lifecycle is a **filter, never a ranking signal**: `SERVABLE_LIFECYCLE_STATES` is an allowlist
containing only `EFFECTIVE`, so a state added later is excluded until someone decides it is safe to
answer from. `PARTIALLY_SUPERSEDED` is excluded because serving cannot tell *which part* of a record
was superseded — and a superseded edition can be near-identical in text to the current one, so
neither fusion nor duplicate suppression can be trusted to prefer the right member. The constraint
is inert on a single-version release and load-bearing the moment a multi-edition publisher is
materialized.

### 3.5 Safety semantics

- **Tri-state applicability.** Clinical context carries explicit *present* and *known-absent* sets.
  Silence about a required or excluded fact evaluates to `UNKNOWN`, which is not eligible for
  unconditional rendering. The synthetic cases in `data/fixtures/foundation_applicability.json`
  are executed by the safety suite to preserve this behaviour.
- **Verification coverage.** Candidate evidence must be canonical and covered by *every*
  evidence-scoped required check. Missing, failed, or unresolved checks all withhold the claim.
  Duplicate required checks are ambiguous and therefore fail. Evidence returned for a renderable
  claim is limited to that claim's nominated candidates.
- **No implicit equivalence.** Related measurements such as eGFR and creatinine clearance remain
  distinct unless a separately validated calculation path is introduced.
- **Abstention taxonomy.** `NO_EVIDENCE_RETRIEVED`, `INCOMPLETE_EVIDENCE_ROLE_SET`,
  `MODEL_DECLARED_INSUFFICIENT`, `NO_CLAIM_SURVIVED_GROUNDING`, `RETRIEVAL_PIPELINE_NOT_CONFIGURED`.
  An abstention that misreports its reason is treated as a correctness defect even though it
  withholds an answer, because it sends a reader to the wrong next step.

### 3.6 Client boundary

The Next.js page and layout are server components; the interactive evidence workspace is a narrow
client boundary, with presentation panels separated from the hook that owns question submission,
SSE progress, polling fallback, and stale-run cancellation.

FastAPI's OpenAPI document is checked in and generates the TypeScript HTTP contract, consumed
through `openapi-fetch`; strict Zod schemas validate HTTP and SSE payloads at runtime. This
two-layer boundary prevents either compile-time schema drift or malformed network data from being
treated as trusted UI state, and CI fails the build if the generated contract drifts from the API.

### 3.7 Backend module boundaries

| Module | Responsibility |
|---|---|
| `schemas` | Frozen, `extra="forbid"` API and domain contracts with canonical range, identity, and contradiction validation |
| `lifecycle` | Version DAG validation, conflict-safe record identity, evidence-scope currentness |
| `persistence` | Async SQLAlchemy registry models over the versioned PostgreSQL schema |
| `ingestion` | Publisher policy, safe acquisition, immutable storage, layout extraction, trust classification, quarantine |
| `semantics` | Formal tri-state eligibility/context comparison |
| `verification` | Deterministic required-check policy, duplicate-check rejection, per-evidence coverage, fail-closed gate |
| `reasoning` | Orchestration, serving retrieval, presentation view, grounded composition — cannot override failed verification |
| `corpus_steward` | The entire build plane: connectors, ledgers, crypto, QA, indexing, benchmarking, CLI |
| `api` | Transport and dependency wiring only |

---

## 4. Design decisions and their rationale

Each decision below was taken deliberately, with the rejected alternative recorded. This section is
the intended entry point for a reader evaluating the design rather than running the code.

### D1 — The serving retriever is *not* the benchmark runner

`ServingRetrievalService` is deliberately separate from `RetrievalBenchmarkRunner`.

**Why.** The runner *is* the measured system: the accepted development report and the frozen
comparator floor were produced by that exact code path, so reshaping it to serve free-text questions
would silently invalidate comparability with every prior report. The two also have different jobs —
serving has no gold set, needs a latency budget, and treats role completeness as a decision that
gates abstention rather than a number reported afterwards.

**Cost, accepted and recorded.** Safety behaviour must be duplicated rather than shared: the
payload-integrity checks are reproduced in both paths, because failing closed on a corrupted index
is a property both need. **Known divergence:** the runner does not deduplicate, so at `top_k = 10`
it is scored on rankings that can spend up to half their depth on copies while serving is not. This
is recorded as a bounded, known difference rather than silently patched, because equalising it would
mint a new benchmark contract and require re-measuring the comparator floor.

### D2 — Qdrant is a projection; PostgreSQL is the registry

**Why.** An index is rebuildable; a citation is not. Passage text never comes from the vector store,
and a payload that disagrees with the canonical evidence record aborts the request.

**Rejected.** Storing passage text in the index for latency. The saving is real and the failure mode
— serving text that no longer matches what the release binds — is exactly the catastrophic case.

### D3 — Signed stage attestations with distinct exit statuses

**Why.** A pipeline that fails closed is only trustworthy if a *policy* block is distinguishable from
a *parser* crash. Exit 5 (structured processing), 6 (input resolution), 7 (materialization/licensing)
and 8 (benchmark acceptance) are recorded, signed decisions.

**Rejected.** Boolean pass/fail logging. It cannot support an audit that asks *which gate refused,
under which trust-root revision, signed by whom*.

### D4 — Presentation is a view, never a re-materialization

XLSX evidence is anchored per cell, so `content_exact` for a spreadsheet row is the cell-addressed
form `A145=HIV.D8 …`. That is the correct anchor — replaying it against the workbook is what the QA
gate does — and it is unusable as passage text.

`app/reasoning/presentation.py` parses that form back into cells and derives three things, all of them
*determinations about existing content* rather than new facts about it:

1. a rendering under the **publisher's own column labels**, read from the release's own approved
   header rows where they survived QA and *declared* where they did not;
2. a **fingerprint** over the substantive cells, which lets a verbatim copy collapse onto the row it
   copies;
3. a classification of **form** — data-dictionary entry, decision rule, schedule entry, indicator,
   header, caption, section title, narrative — decided from structure and never from a role label.

**Why a view.** `content_exact`, anchors, and evidence digests are what the QA ledger decided on and
what the signed release binds. A record re-materialized to read better would invalidate both.
Citation stays bound to `evidence_id`, and the immutable content is carried alongside the view rather
than replaced by it.

### D5 — The role gate is qualified by passage *form*, not by content reading

`REQUIRED_ANSWER_ROLES` is `(PRIMARY_SUPPORT, APPLICABILITY)`. The corpus assigns roles by **source
asset**, not by content, so a code-list entry defining `DTG` as an input option, a row of column
headings, and a section title all arrive labelled `PRIMARY_SUPPORT`.

Serving therefore counts `PRIMARY_SUPPORT` only from a passage whose *form* can carry a
recommendation at all; an unrecognised table cannot, because the view cannot tell what it is and the
gate exists to fail closed. Disqualified role claims are **reported**, so an answer withheld because
of the corpus's labelling says so.

**Deliberately not fixed at source.** `evidence_roles` is inside `CorpusEvidenceRecord`, so
re-deciding it changes every affected evidence digest and therefore the manifest, the release, the
index, and the comparability of every benchmark report produced against it. That is a re-release, not
a patch, and it should be done once — alongside narrative materialization — rather than twice.

**Stated limit.** The gate proves completeness *of kind*, never *of subject*, and cannot be made to
prove subject without becoming a relevance judgement — that is, without becoming a model deciding
what evidence means. What protects the reader instead is claim-level grounding (D7).

### D6 — Deterministic query expansion, and it is not uniformly positive

Expansion is bounded and deterministic, with no generative query drift. Measured across six paired
runs it **rescued one question and broke another**: it lifted the genuinely relevant
dolutegravir–metformin interaction rule from rank 19 to rank 10, and it *displaced* the single
passage stating the 6-month and 12-month viral-load intervals on the one question this corpus
answers well. Its value is not established, and this is recorded rather than resolved by tuning.

### D7 — Model output is a proposal, never a result

- No retrieved evidence means abstention decided **before any model call**.
- Every claim must cite evidence IDs drawn from the set actually retrieved for that question; a claim
  citing anything else is **discarded whole rather than repaired**.
- If nothing survives, the result is an abstention even when the model reported sufficient evidence.
- The model's sufficiency signal can **lower** an outcome to abstention but can never **raise** one to
  an answer — because a model given insufficient context answers more confidently, not less.
- A provider refusal becomes an abstention rather than a silent retry against a different model.

The lane is provider-swappable through a sealed parameter file (Anthropic on Vertex or Bedrock;
a Gemini comparator on the same Vertex project), so changing provider is a configuration change
rather than a code change.

### D8 — The evaluation question set may not come from the corpus

**Why.** The 430-case development suite demonstrates what happens when it does: every query is a
normalized source fragment, five strata measure query-term coverage of 1.000 by their own gold
passage, and the resulting 0.9647 measures near-duplicate lookup rather than clinical retrieval.

The coverage sampling frame is therefore the **parent guideline**, not the DAK: a Digital Adaptation
Kit is by definition an operationalization of a subset of its parent's recommendations, so the
fraction of *parent recommendations* the corpus can answer is exactly the coverage number the
decision needs. The frame is principled rather than convenient — it is the population of things a
clinician could reasonably ask, defined by the publisher, independent of anything built here.

**Rejected instruments**, recorded so the choice is not revisited: HIVMedQA (63 questions, ~42
distinct, licensing-exam vignettes, US exam content against guidance written for a public-health
approach — but its false-premise category is retained for later abstention hardening); RealMedQA
(right method, wrong guidelines and population); MedQA / MedMCQA / PubMedQA / BioASQ (recall and
literature synthesis, not guideline lookup); the NLM Clinical Questions Collection (not obtainable —
both the stated download page and the data.gov record 404 as of 2026-08-27).

### D9 — The comparator floor is measured, not chosen

Every acceptance gate is stated as a **Wilson 95% lower bound of a measured comparator run**, frozen
into a threshold policy *before* the candidate is evaluated, with a `comparator_measured_at` timestamp
that must precede the policy. The non-inferiority margin is **0.02, not 0** — tying a deterministic
lexical control is not evidence that a neural stack earns a 10× latency cost.

Sample mass follows measured headroom rather than uniformity: the comparator scores 1.00 on five
lexical strata and every negative stratum, so 8 of 12 strata cannot discriminate between candidates
at all. Those saturated strata carry 15 cases each; `PARAPHRASED_INTENT`, `TERMINOLOGY`, and
`CONFLICTING_EVIDENCE` carry 60 each.

### D10 — The sealed holdout stays unspent

Development acceptance is not clinical validation. The holdout is a one-shot, custody-controlled
resource, and acceptance binds the release — so any later corpus change voids it. Three reasons keep
it closed: the 0.6B candidate is the declared deployment *floor* rather than the intended selection;
the 4B/8B artifacts are not acquired; and the first-contact findings indicate the corpus and chunking
will change before a candidate is worth freezing.

`scripts/ask.py` and the research serving mode both exist so that **looking at an answer never
consumes a holdout claim**.

### D11 — Research serving is labelled, not faked

Setting `SERVING_ENABLED=true` serves a `VALIDATED` release **without activating it**. The singleton
pointer stays empty, every answer carries `serving_mode: RESEARCH_UNACTIVATED` with a null
`activated_at`, and the workspace renders *"Research serving — not clinically accepted"* rather than
the shield and activation date an activated release earns. Per-record guarantees are unchanged:
approval status, digest agreement, servable lifecycle, and the licence conjunction behind
`render_allowed` run the same code either way.

### D12 — The rendering licence is a decision, recorded

`render_allowed: false` on every WHO asset gated three distinct acts together. They are separated
and decided independently ([docs/rendering-licence.md](docs/rendering-licence.md)):

| Act | What it reproduces | Decision |
|---|---|---|
| **Locator** — "Annex A, `HIV.D` worksheet, row 145, column E" | Nothing. A coordinate. | Not a copyright act; shipped |
| **Passage** — `content_exact` or its presentation rendering | A short excerpt | Served with attribution (branch A) |
| **Page image** — a PDF page with the anchor region highlighted | A full page, and for this corpus a *table* | Blocked pending a WHO permissions request |

The corpus's tabular nature is what separates acts 2 and 3: WHO's publishing policy treats figures,
tables, maps and photos as requiring explicit permission, and this release is almost entirely
spreadsheet, decision-table, and data-dictionary rows. The MVP completion criterion was **narrowed in
writing** as a result — from *the anchor chain terminates in a picture* to *locatable by address and
quotable by excerpt* — rather than being silently declared satisfied.

---

## 5. Evaluation

Two measurements, on the same system, measuring different things. Reading either as the other is the
central interpretive error this section exists to prevent.

### 5.1 Retrieval benchmark (development suite)

**Suite.** `who-smart-hiv-source-derived-development-v7`, contract 1.6 — 430 deterministic cases over
release `CR_b6155a25415b25f3ba787b3b036da10d`, 12 safety strata, `top_k = 10`, sealed and digest-bound
(`suite_sha256 = b16a9df…`). Its holdout sibling exists only in the custody-controlled immutable
registry.

**Candidate.** Qwen3-Embedding-0.6B (dense) + BM25 Unicode (sparse), conflict-aware, weighted RRF.

| Metric | Result | Gate |
|---|---:|---:|
| Answerable complete-evidence rate | **0.9647** (Wilson lower 0.9343) | ≥ 0.7763 |
| Comparator floor (deterministic expanded control) | 0.8275 (Wilson 95% [0.7763, 0.8689]) | must exceed, margin 0.02 |
| Required-role recall | 0.9647 | ungated by design |
| Forbidden-evidence leakage cases | 0 | 0 |
| Candidate failure cases | 0 | 0 |
| p95 latency | 1,673 ms | ≤ 2,000 ms |

**Verdict: ACCEPTED on the development suite, with no blockers — and this is not clinical
validation.**

**How to read 0.9647.** The suite's queries are normalized source fragments. Measured query-term
coverage by the gold passage is **1.000** for `APPLICABILITY`, `CONTRAINDICATION`, `DOSE`,
`MONITORING`, and `NEGATION`. BM25 alone reaches **0.9927** recall; the dense lane reaches **0.9291**.
A high blended score on this suite is evidence that **near-duplicate lookup works**, not that clinical
retrieval works. The `PARAPHRASED_INTENT` stratum exists to counter this — controlled vocabulary
substitution, all source identifiers dropped, accepted only inside an IDF-weighted lexical rank band
deliberately wider than the retrieval depth — and its measured coverage is **0.411**. It is not a
clinician-authored paraphrase and must not be described as one.

### 5.2 Answerable coverage (pre-registered, two-stage measurement)

**Sampling frame.** 321 statement-shaped blocks extracted from *Summary recommendations* of the 2021
WHO consolidated HIV guidelines; filtered to GRADE-rated statements ≥120 characters containing a
deontic verb, minus headings, bullet fragments and footnotes → **165 formal recommendations**. Good
practice statements are excluded deliberately.

**Draw.** 50, uniform without replacement, seed `20260827`. Three items flagged in the data rather
than silently repaired (one anaphoric fragment excluded → **n = 49**, one near-duplicate, two
general-population advice items). Not redrawn to reach a round number: replacing a drawn item is the
kind of small discretion that makes a seed meaningless.

**Extension to stage 2.** The pre-registered stage 2 extends the *same* seeded draw to 150, which is
sound only if the draw nests — the n = 150 sample's first 50 elements must be the n = 50 sample. That
holds at this frame size and is **asserted at runtime rather than assumed**, because it is a property
of `random.sample`'s implementation and not a documented guarantee. Membership of the pre-registered
prefix is therefore a property of the seeded draw *order*; an earlier version of the emitter wrote a
chapter-sorted view under the same key, which silently turned the prefix into "the first 150 by
chapter", disagreed with the real draw on 26 of 165 items, and excluded two stage-1 questions. The
builder now fails loudly if any stage-1 recommendation falls outside the prefix, which is the check
whose absence allowed that. All 165 frame members are authored, so the pre-registered n = 150 and a
full-frame census both come from one run.

**Question construction.** Each recommendation instantiated into one of ten generic clinical question
forms from Ely et al. (BMJ 2000) — 1,396 questions observed from 152 primary-care physicians,
classified into 64 types, of which the ten published types cover ~63%. This gives phrasing an
empirical, citable basis and makes *write the situation, not the wording* structural rather than a
matter of discipline. No recommendation needed `NO_GENERIC_FORM`.

**Lexical-leakage screen.** Content-term overlap against the source recommendation: **mean 0.447,
median 0.500** — against 1.000 on five strata of the source-derived suite. Twelve questions ≥0.60 are
flagged for review rather than auto-rejected, because the metric cannot separate unavoidable clinical
nouns from real leakage.

**Pre-registered decision rule.** Stage 1 at n = 50 decides only if the answer is extreme (Wilson 95%
entirely below 0.25 → the corpus cannot carry the product; entirely above 0.50 → proceed as scoped);
otherwise stage 2 extends to n = 150 and the decision is made on the *shape* of the correct
abstentions, because a corpus answering 35% uniformly and one answering 35% while missing all of
treatment initiation are the same number and different products. `ANSWERED_WRONG` has a separate rule:
**zero observed occurrences at every stage**, one occurrence stops the MVP.

**Stage 1 result (2026-08-27, n = 49).** `p_answered` = 21/49 = 0.4286, Wilson 95% [0.300, 0.567] —
neither entirely below 0.25 nor entirely above 0.50, so the rule sent it to stage 2. That is the
outcome the two-stage design exists to handle, not a failure of it.

**Stage 2 result (2026-08-29, gemini-3.7-flash).** The frame is 165, so the pre-registered n = 150
samples 91% of the population. All 165 were therefore authored, which yields the pre-registered
analysis and a census from one run; the 15 extra questions are a superset of the protocol, not a
change to it.

> **`p_answered` = 69/149 = 0.4631, Wilson 95% [0.385, 0.543]** on the pre-registered n = 150.
> Full-frame census: 73/164 = 0.4451, Wilson 95% [0.371, 0.522].
> The interval is still not entirely above 0.50, so the decision rests on the *shape* of the
> abstentions, exactly as the rule anticipated.

| Outcome (full frame, n = 164) | n |
|---|---:|
| Answered, at least one claim rendered | 73 |
| `MODEL_DECLARED_INSUFFICIENT` — gate passed, model judged the passages insufficient | 77 |
| `INCOMPLETE_EVIDENCE_ROLE_SET` — blocked at the gate, no model call | 14 |
| `NO_CLAIM_SURVIVED_GROUNDING` — claims composed, none survived verification | 0 |

**The gate is not what withholds answers; the model is.** 150 of 164 cleared the role gate (pass rate
0.915, against 0.898 at stage 1) and 77 of those were refused by the model's own sufficiency
judgement — the division `retrieval_service` predicts in its own comment, and the reason a
retrieval-only run cannot approximate this number.

**Two checks that the larger sample makes possible.** The 49 stage-1 questions, re-run inside the
stage-2 pass, reproduced **20/49 = 0.4082** exactly. And the 115 batch-accepted questions score
0.4435 against the 50 individually-read ones at 0.4082, with heavily overlapping intervals — so the
newly authored half is not systematically easier. Lexical leakage does not predict answering either
(mean overlap 0.317 answered against 0.353 abstained, if anything the wrong way), so the question set
is not rewarding itself.

**Coverage by chapter — the load-bearing part.**

| Chapter | Stage 1 | Stage 2 | Share |
|---|---:|---:|---:|
| 2 — HIV testing and diagnosis | 7/7 | 19/28 | 67.9% |
| 4 — ART for people living with HIV | 3/6 | 9/16 | 56.2% |
| 3 — HIV prevention | 1/3 | 6/11 | 54.5% |
| 7 — Service delivery | 3/3 | 12/22 | 54.5% |
| **6 — Coinfections and comorbidities** | 7/24 | **21/71** | **29.6%** |
| 5 — Advanced HIV disease | 0/6 | 4/16 | 25.0% |

The pre-registered expectation holds in direction, and **stage 2 corrects its sharpest number**: the
"0 of 6 on advanced HIV disease" reported at stage 1 was a small-sample artifact and is 4/16 = 25.0%
at the larger n, while the near-ceiling chapters fall back toward the middle. What survives is
chapter 6 — the largest at 71 questions — steady at 29.6% against 29% at stage 1. The durable finding
is that the corpus underperforms on **coinfections and comorbidities**, not that it answers nothing
on advanced disease. Read as *this corpus operationalizes a narrow slice of its parent*, not as a
retrieval defect.

Eight of the 164 are general-population physical-activity recommendations carried in chapter 6 of the
parent guideline; the DAK operationalizes none of them and all eight abstain. Excluding them gives
73/156 = 0.4679.

**Three limits the number cannot be read past**, stated with the result: no question has been
classified into the correctness buckets, so 0.4631 means *"the system rendered claims"*, not *"the
system answered correctly"*; the abstentions are not split into `ABSTAINED_CORRECT` versus
`ABSTAINED_AVOIDABLE`; and the run used a **Gemini lane**, not the sealed Claude candidate, because
the `anthropic-*` Vertex base-model quota is ungranted. Every grounding and abstention rule is shared
across lanes, but the number belongs to the lane that produced it — and lane choice moves it: the
same 49 questions scored 0.490 on gemini-2.5-flash against 0.408 on gemini-3.7-flash, a difference
not significant at that n (McNemar p = 0.219) and a reason to treat any single-lane coverage figure
as lane-conditional.

The residual wrong-answer rate is reported as a **bound, never as "zero"**: with no occurrences, the
one-sided 95% upper bound is ~5.9% at n = 49 and 2.0% at n = 150. This is a screening measurement that
can detect a bad system, not one that can certify a good one.

### 5.3 Corpus structure findings

Three properties of an operational guideline corpus, each measured on the full release, each with a
consequence for system design.

F1 and F2 were originally measured on one release. Both have since been re-measured on the
**published annexes of four WHO adaptation kits** — HIV, antenatal care, family planning,
tuberculosis — so that each can be stated as a property of the artifact class or denied it. The
cross-kit measurement is pre-QA and workbook-level, a different denominator from the release-level
numbers below, and is reported as such rather than reconciled away
([scripts/audit_dak_structure.py](scripts/audit_dak_structure.py)).

**F1 — 40.4% of the release is exact duplicate content that content hashing could not see.** The
Annex A `all` worksheet is a verbatim copy of all thirteen topic worksheets with one extra column
recording which tab each row came from. That column changes `content_search`, so QA's exact-content
deduplication passed it. **2,080 of 5,145 approved records are second copies; the release holds 3,065
distinct passages.** Suppression at serving is exact rather than thresholded — the fingerprint covers
every substantive cell, the `Tab` column is declared structural, and the measured collision count
matches the copy count exactly.

**F1 does not generalize, and the mechanism claim is the part that survives.** Of the four kits, only
HIV publishes an aggregate worksheet: `all` reproduces **2,091 distinct rows across 11 topic sheets**
at 98.8–100% each, while ANC, FP and TB ship none. So the *duplication* is an HIV publishing choice,
not a DAK-class property, and the release-level 40.4% must not be generalized. What does generalize
is the reason it was invisible: measured on the published workbook, exact content hashing sees
**11 duplicate rows (0.2%)**, because the extra `Tab` column changes every row. Content-hash
deduplication is insufficient for this artifact class whether or not a given kit exercises the case.

**F2 — 91.0% of the recommendation-bearing label is not recommendation-bearing.** Roles are assigned
by source asset. **4,664 of 5,145 records (90.7%) carry `PRIMARY_SUPPORT`, and 4,244 of those (91.0%)
structurally cannot bear a recommendation** — 4,211 data-dictionary entries, 16 section titles, 13
table headers, 4 caption rows. The release's genuinely recommendation-bearing surface is **420
records, not 4,664.**

**F2 does generalize, and HIV is the extreme rather than the typical case.** Every kit publishes a
data dictionary many times larger than its decision-support logic, so any labeller assigning roles by
source asset inherits that ratio as a labelling error:

| Kit | Data-dictionary rows | Decision-support rows | Indicator rows | Data-dictionary share |
|---|---:|---:|---:|---:|
| HIV | 4,489 | 521 | 501 | **81.5%** |
| Antenatal care | 1,160 | 742 | 42 | 59.7% |
| Tuberculosis | 714 | 899 | 76 | 42.3% |
| Family planning | 696 | 1,667 | 40 | 29.0% |

The defect is structural across the class at 29–81%, and the HIV release sits at the top of that
range. A serving layer that trusts publisher role labels is wrong on a large minority of records in
the best case here and on four fifths of them in the worst.

**F3 — the corpus answers "what data element records this" far better than "what should a clinician
do".** On *"When should ART be started in adults with HIV?"*, pushing retrieval to depth 30 surfaces
nothing that states a start time. The timing vocabulary is present — "within 7 days" ×56, "same day"
×16 — almost exclusively as *reasons for not initiating*: a coded list of delay reasons each phrased
"did not initiate ART at diagnosis or within 7 days because…". **The recommendation is never asserted;
it exists only as the negative space those fields are defined around.** No ranking can retrieve an
assertion the corpus does not contain.

Together F1–F3 are evidence *for* the narrative-materialization path — the WHO consolidated HIV
guidelines are narrative PDFs and would carry exactly the missing content — rather than evidence
against the architecture.

### 5.4 What the safety chain costs, measured against itself

The claim that the fail-closed chain is worth its cost was, until now, architectural rather than
measured. `AblationProfile` ([app/reasoning/ablation.py](apps/api/app/reasoning/ablation.py)) makes
each of the five mechanisms switchable — duplicate suppression, role qualification by form, the role
gate, the model-sufficiency override, and claim-level grounding — with all-on as the default, so the
measured system is unchanged unless a caller explicitly asks otherwise.

Same 49 questions, same release, same lane; the profile is the only difference:

| Profile | Answered | Wilson 95% |
|---|---:|---|
| Production (all five active) | 20/49 = 0.408 | [0.282, 0.548] |
| Every mechanism off | 21/49 = 0.429 | [0.300, 0.567] |

**McNemar exact p = 1.0.** Turning the entire chain off moves coverage by one question. Underneath:

- **Claim-level grounding never fires.** Zero of 54 baseline claims cited evidence outside the
  retrieved set, and zero of 187 claims in the stage-2 run were withheld at grounding. D7's headline
  protection has not been exercised once in 241 observed claims.
- **The abstention cannot be ablated away.** Overriding the model's sufficiency flag relocates the
  abstention to `NO_CLAIM_SURVIVED_GROUNDING` rather than producing an answer: the model declines to
  *write* claims it cannot support, so the withholding is the model's behaviour, not the pipeline's.
- **The role gate blocks 5, of which 1 would otherwise have been answered.**
- Duplicate suppression removed 198 passages and 211 role claims were disqualified by form — large
  effects on *what a reader sees*, almost none on *whether an answer appears*.

Read honestly this cuts both ways, and both directions are the result. The chain is nearly free, so
the cost objection to fail-closed design does not survive contact with this measurement. It is also
largely **untested**, because the failure modes it exists to prevent — fabricated citations, claims
resting on unretrieved evidence — do not occur on this corpus with these models. That is L5 and L6
turned from a caveat into a number, and it is the strongest argument for the plausible-negative
hardening the roadmap defers rather than for the chain as currently exercised.

Because no correctness classification exists, this compares **rendering behaviour only**. It cannot
say how many of the naive pipeline's extra answers are wrong, which is the quantity that would make
the comparison an argument for the chain rather than a description of it.

### 5.5 Runtime characteristics

| Quantity | Measurement |
|---|---|
| Retrieval, warm, end to end | 246–359 ms (six paired runs) |
| Embedding model resident (Qwen3-0.6B, CPU/fp32) | ~5.4 GiB |
| Query encode | ~680 ms |
| Benchmark p95, accepted candidate | 1,673 ms |
| Release scale | 5,145 approved records / 3,065 distinct passages |

An int8 OpenVINO export path exists for the dense lane; per the runtime contract, an optimized runtime
may **not** reuse the float32 vector-batch attestation and must be re-measured with a newly sealed
batch.

---

## 6. Threats to validity and known limitations

Stated as limits, not as future work. Each is recorded in the design documents at the point it
constrains a claim.

| # | Limit | Consequence |
|---|---|---|
| L1 | The development suite is **source-derived**: queries are normalized source fragments | 0.9647 measures near-duplicate lookup; it is not a clinical retrieval claim |
| L2 | The coverage questions are **model-authored** from a mechanical frame and draw | The frame and draw reproduce; each question is a judgement about how a clinician would ask, and `review_status` gates whether a number may be quoted |
| L3 | Stages 1 and 2 ran on **Gemini lanes**, not the sealed Claude candidate | The number belongs to the lane that produced it and must be re-measured before it is quoted as the product's. Lane choice moved the stage-1 figure from 0.408 to 0.490 |
| L4 | **No correctness classification yet** | `p_answered` counts rendered claims, not correct ones; `ANSWERED_WRONG` is unmeasured and is the stop-the-MVP bucket. This also bounds §5.4: the ablation compares rendering behaviour, not wrongness prevented |
| L5 | The role gate proves completeness **of kind, never of subject** | An off-topic passage can satisfy it; claim-level grounding is what protects the reader |
| L6 | `minimum_insufficient_evidence_accuracy = 1.0` over negatives **detectable by release filter alone** | It currently certifies the filter, not abstention; it will not survive plausible negatives, where the literature puts frontier models below 50% |
| L7 | `generation_context_budget == top_k` | `complete_evidence_at_budget` is currently identical to `complete_evidence_set`; forward-looking infrastructure |
| L8 | Serving deduplicates; the benchmark runner does not | The 0.9647 acceptance measures a slightly different system from the one that answers a question — recorded as bounded, not silently patched |
| L9 | **Query expansion is not established as positive** | It rescued one question and regressed another across six paired runs, and the development-accepted candidate uses it |
| L10 | Role labelling is wrong **at the point it is written** | Serving mitigates; the durable fix is a re-release, deliberately deferred to be done once alongside narrative materialization |
| L11 | Page-image rendering is **licence-blocked** for every WHO asset | The anchor chain terminates in an address and an excerpt, not a picture |
| L12 | Sealed holdout **unspent**; no clinical validation, approval, or activation | Nothing here supports a deployment claim |

---

## 7. Reproducibility

Requirements: Python 3.10+, Node.js 20+, Docker.

### 7.1 Environment

```powershell
Copy-Item .env.example .env
docker compose up -d postgres qdrant
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\apps\api[dev]"
.\.venv\Scripts\python -m alembic -c apps\api\alembic.ini upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir apps/api --reload
```

```powershell
npm install
npm run dev:web    # http://localhost:3000 ; API docs at http://localhost:8000/docs
```

### 7.2 Validate the build/serve seam without a corpus

```powershell
.\.venv\Scripts\python -m app.corpus_steward.cli validate-bundle `
  data/fixtures/corpus-release-v1.json --require-activatable
```

### 7.3 End-to-end synthetic reconciliation

Exercises the full build plane deterministically, with no network acquisition. Do not commit the
generated private key.

```powershell
New-Item -ItemType Directory -Force data\local\keys | Out-Null
corpus-steward keygen --private-key data\local\keys\stage-private.pem `
                      --public-key data\local\keys\stage-public.pem
corpus-steward register-key --public-key data\local\keys\stage-public.pem `
  --key-id local-steward-stage --signer-identity local-corpus-steward --purpose STAGE
corpus-steward register-trust-root data\fixtures\trust-root-synthetic.json
corpus-steward reconcile SYNTHETIC `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage --signer-identity local-corpus-steward
```

The checked-in real connector definition is `data/trust-roots/who-smart-hiv.json`. The full
`resolve-structured-inputs → process-structured → materialize → qa → qdrant-build → qdrant-validate →
qdrant-attest → benchmark-run` sequence is documented in
[docs/corpus-steward.md](docs/corpus-steward.md).

### 7.4 Ask one question end to end

`scripts/ask.py` drives the serving path against a **validated** release without activating it, so no
sealed-holdout claim is consumed to look at an answer.

```powershell
.\.venv\Scripts\python scripts\ask.py `
  --question "How often should viral load be monitored during ART?" `
  --bundle data\local\validated-who-smart-hiv-release.json `
  --vectors data\local\benchmark-source-derived\qwen3-0.6b-release-vectors.json `
  --collection corpus_cr_<release>--vp-<profile> `
  --embedding-backend verified-local `
  --dense-model-root models\local\qwen3-embedding-0.6b `
  --dense-model-manifest data\local\model-manifests\qwen3-embedding-0.6b-cpu-float32.json `
  --dense-artifact-sha256 <digest> `
  --sparse-model-root models\artifacts\qdrant-bm25-unicode-v1 `
  --sparse-model-manifest data\local\model-manifests\qdrant-bm25-who-smart-hiv-v1.json `
  --sparse-artifact-sha256 <digest>
```

Retrieval runs with **no generation credentials**. `--generate` composes an answer and additionally
requires `MEDRAG_VERTEX_PROJECT_ID` plus application-default credentials; an incomplete evidence-role
set abstains before any model call. `--generation-provider gemini` selects the comparator lane.

Read the retrieved passages, not just the metrics. Passages print as the presentation view: rows are
rendered under the publisher's own column labels, verbatim copies are suppressed before top-k, and
`role claims not counted` marks a passage whose declared `PRIMARY_SUPPORT` its form cannot carry.

### 7.5 Research serving through the HTTP API

By default the API abstains with `RETRIEVAL_PIPELINE_NOT_CONFIGURED` — an accurate description of a
deployment with no serving path, not a defect. `SERVING_ENABLED=true` plus the `SERVING_*` block in
`.env.example` wires retrieval and grounded composition into the API. **This does not activate the
release** (see [D11](#d11--research-serving-is-labelled-not-faked)). The embedding model loads
in-process, so startup is slow and the first question is not the one to time.

### 7.6 Checks

```powershell
.\.venv\Scripts\python -m pytest apps/api/tests     # 312 tests incl. executable safety fixtures
.\.venv\Scripts\python -m ruff check apps/api
npm run typecheck:web ; npm run test:web ; npm run build:web
npx playwright install ; npm run test:e2e:web       # cross-browser accessibility coverage
```

CI runs on every branch push, and fails if the checked-in OpenAPI document or generated TypeScript
contract drifts from the API.

### 7.7 Reproducibility artifacts

| Artifact | Location |
|---|---|
| Sealed development suite (430 cases, digest-bound) | [benchmarks/suites/](benchmarks/suites/) |
| Coverage question set, frame, draw, seed, leakage screen | [benchmarks/questions/](benchmarks/questions/) |
| Access, generation, and threshold policies (sealed) | [benchmarks/policies/](benchmarks/policies/) |
| Runtime matrices, artifact-bound | [benchmarks/runtime/](benchmarks/runtime/) |
| Qdrant version-compatibility report | [benchmarks/qdrant_compat/](benchmarks/qdrant_compat/) |
| Frozen synthetic release + JSON Schema | [data/fixtures/](data/fixtures/) |
| Frame/draw rebuild script (fails if either stops reproducing) | [scripts/build_mvp_question_set.py](scripts/build_mvp_question_set.py) |
| Coverage runner (both stages) and scorer | [scripts/run_mvp_coverage_stage1.py](scripts/run_mvp_coverage_stage1.py), [scripts/score_mvp_coverage.py](scripts/score_mvp_coverage.py) |
| Stage-2 question set: full frame, seeded prefix marked per item | [benchmarks/questions/mvp-coverage-who-hiv-v2.json](benchmarks/questions/mvp-coverage-who-hiv-v2.json), [scripts/build_mvp_question_set_v2.py](scripts/build_mvp_question_set_v2.py) |
| Safety-chain ablation profiles | [apps/api/app/reasoning/ablation.py](apps/api/app/reasoning/ablation.py) (`--naive-baseline`, `--ablate`) |
| Cross-DAK structure audit and IRIS annex discovery | [scripts/audit_dak_structure.py](scripts/audit_dak_structure.py), [scripts/discover_dak_annexes.py](scripts/discover_dak_annexes.py) |

---

## 8. Repository map

```text
apps/
  api/                     FastAPI service + corpus-steward build plane  (95 modules, ~35.6k LOC)
    app/schemas/           frozen extra-forbid contracts
    app/lifecycle/         version DAG, scoped supersession
    app/ingestion/         publisher policy, acquisition, extraction, quarantine
    app/semantics/         tri-state applicability
    app/verification/      deterministic required-check gate
    app/reasoning/         retrieval_service, presentation, answer_service, serving pipeline
    app/corpus_steward/    connectors, ledgers, crypto, QA, indexing, benchmarking, CLI
    tests/                 safety + unit suites (~12.4k LOC, 312 tests)
  web/                     Next.js evidence workspace (~17.9k LOC TS/TSX)
    components/evidence-workspace/   panels, anchor/source viewers, SSE orchestration
    lib/                   generated OpenAPI types, Zod contracts, presentation helpers
benchmarks/                sealed suites, policies, question sets, runtime evidence
docs/                      architecture, corpus-steward, roadmap, MVP definition, licence decision
migrations/                16 Alembic revisions over 52 tables
scripts/                   ask.py, coverage pipeline, model export, ingestion utilities
```

### Documentation index

| Document | What it settles |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Implemented boundaries, plane by plane |
| [docs/corpus-steward.md](docs/corpus-steward.md) | The complete build-plane command sequence and its gates |
| [docs/mvp-definition.md](docs/mvp-definition.md) | What "done" means; the pre-registered coverage rule and its result |
| [docs/roadmap.md](docs/roadmap.md) | Decisions taken, findings, and what is deliberately deferred |
| [docs/rendering-licence.md](docs/rendering-licence.md) | The three rendering acts and which are permitted |
| [docs/narrative-only-materialization.md](docs/narrative-only-materialization.md) | The path to the content F1–F3 show is missing |
| [docs/frontend.md](docs/frontend.md) / [docs/frontend-audit.md](docs/frontend-audit.md) | Client workflow and the audit that reshaped the workspace |
| [docs/ingestion.md](docs/ingestion.md) | The authenticated source workflow |

---

## 9. Data governance and licensing

**Source corpus.** WHO SMART Guidelines Digital Adaptation Kit for HIV, second edition, with the 2021
*Consolidated guidelines on HIV prevention, testing, treatment, service delivery and monitoring* and
the 2019 *Consolidated guidelines on HIV testing services* as its parent guidelines. Published by the
World Health Organization under CC BY-NC-SA 3.0 IGO, with WHO's publishing policy layered on top.

**No source content is redistributed in this repository.** Acquisition is content-addressed and
performed locally against registered publisher domains; the checked-in artifacts are trust roots,
connector definitions, digests, sealed policies, and synthetic fixtures. `render_allowed` remains
`false` for page-image reproduction of every WHO asset (see [D12](#d12--the-rendering-licence-is-a-decision-recorded)).

**The evaluation question set is a measurement input, not evidence.** It does not enter the corpus
pipeline, needs no attestation or trust root, and the parent guidelines are read there as a source of
*questions*, not acquired as a source of clinical content.

**Research profile only.** Do not use this stack for PHI or patient care. The disclaimer is
architectural rather than decorative: nothing in the repository can reach an activated release, and
every answer the system can currently produce is stamped `RESEARCH_UNACTIVATED`.

**Code licence.** No licence file is currently present; all rights reserved pending an explicit
choice.

---

## 10. Citation and references

```bibtex
@software{sentinel_evidence,
  title  = {Sentinel Evidence: A Fail-Closed, Provenance-Bound Retrieval and
            Answer System for Infectious Disease Practice Guidelines},
  author = {Alotaibi, Abdullah},
  year   = {2026},
  note   = {Research artifact. Not authorized for patient care.}
}
```

**References**

1. Ely JW, Osheroff JA, Gorman PN, et al. A taxonomy of generic clinical questions: classification
   study. *BMJ* 2000;321(7258):429–432. — the question-form taxonomy used to instantiate the coverage
   set.
2. World Health Organization. *Consolidated guidelines on HIV prevention, testing, treatment, service
   delivery and monitoring: recommendations for a public health approach.* Geneva: WHO; 2021. — the
   coverage sampling frame.
3. World Health Organization. *Consolidated guidelines on HIV testing services.* Geneva: WHO; 2019.
4. World Health Organization. *SMART Guidelines: Digital Adaptation Kit for HIV*, second edition. —
   the corpus under test.
5. Wilson EB. Probable inference, the law of succession, and statistical inference. *JASA*
   1927;22(158):209–212. — the interval every acceptance gate and coverage decision is stated in.
6. HIVMedQA. [arXiv:2507.18143](https://arxiv.org/abs/2507.18143);
   data [Zenodo 15868085](https://zenodo.org/records/15868085) — evaluated and rejected as the
   coverage instrument; category 4 retained for abstention hardening.
7. RealMedQA. [PMC12099375](https://pmc.ncbi.nlm.nih.gov/articles/PMC12099375/) — methodological
   precedent for deriving a question set from guideline recommendations.

---

<div align="center">
<sub><b>Not authorized for patient care.</b> No clinical validation, clinician approval, holdout
acceptance, or activation has taken place.</sub>
</div>
