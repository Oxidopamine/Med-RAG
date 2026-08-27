# MVP definition and the coverage measurement

Status: decided; stage 1 run 2026-08-27 on the Gemini lane — p_answered 0.4286,
Wilson 95% [0.300, 0.567], which the pre-registered rule sends to stage 2. Buckets
not yet classified.
Authored: 2026-08-27
Scope: what "done" means for the WHO SMART HIV MVP, and the one measurement that decides
whether this corpus can carry a product at all

The roadmap sets the direction — one narrow corpus, answered end to end — and an ordered
list of six activities. It does not say when the list is finished, and "read 50 real
outputs" has no completion criterion. Work with no finish line expands to fill whatever
time it is given, and this project's failure mode is already known to be spending effort
where verification is cheap rather than where the answer is unknown. This document fixes
the finish line and pre-registers the decision that the reading exists to make.

## What done means

The MVP is complete when, against a question set the corpus did not author:

1. **no answer is confidently wrong.** A wrong answer with a real citation attached is
   this system's catastrophic failure and the reason the whole evidence-binding
   architecture exists. Zero observed occurrences, with the residual rate stated as a
   bound rather than as "zero" (see below).
2. **every abstention names a true reason.** `NO_EVIDENCE_RETRIEVED` when nothing
   matched, `INCOMPLETE_EVIDENCE_ROLE_SET` when what matched cannot carry a
   recommendation. An abstention that misreports why is a correctness defect even though
   it withholds an answer, because it sends a reader to the wrong next step.
3. **every rendered claim resolves to a passage a reader can locate.** The anchor chain
   terminates in a picture, under whatever the licence decision permits.

   **Narrowed 2026-08-27 by the licence decision.** Branch A was taken
   ([rendering-licence.md](rendering-licence.md)): locators and passage excerpts are
   served with attribution, page images stay behind `render_allowed: false` pending a
   WHO permissions request that has not yet been filed. So for this MVP the criterion
   reads *locatable by address and quotable by excerpt*, and the anchor chain does
   **not** terminate in a picture. That is a narrower claim than the sentence above and
   is recorded here rather than silently satisfied. It reverts to the full criterion if
   and when WHO grants the branch-C permission.
4. **answerable coverage is known, stated with its uncertainty, and above the floor
   fixed below.**

Explicitly not part of done: clinical validation, clinician approval, holdout acceptance,
activation, deployment. Those are separate gates and none of them is reachable from here.

## The question set

**The corpus must not author the questions.** The 430-case development suite already
demonstrates what happens when it does: every query is a normalized source fragment, five
strata measure query-term coverage of 1.000 by their own gold passage, and the resulting
0.9647 measures near-duplicate lookup rather than clinical retrieval. Sourcing the MVP
questions from Annex B decision-table names, or from any other corpus surface, reproduces
that defect at smaller scale and would produce a coverage number near 1.0 by construction.

**The sampling frame is the parent guidelines, not the DAK.** The DAK states what it
operationalizes: *Consolidated guidelines on HIV prevention, testing, treatment, service
delivery and monitoring: recommendations for a public health approach* (2021) and
*Consolidated guidelines on HIV testing services* (2019). A digital adaptation kit is by
definition an operationalization of a subset of its parent guideline's recommendations.
So the parent recommendation statements are the superset, the DAK is the subset, and
**the fraction of parent recommendations this corpus can answer is exactly the coverage
number the MVP decision needs.** The frame is principled rather than convenient: it is
the population of things a clinician could reasonably ask this system, defined by the
publisher, independent of anything built here.

Sampling is uniform over recommendation statements with a recorded seed.

**The question is written from the clinical situation, not from the recommendation's
wording.** This is the same trap as the 430-case suite, one level up: a question derived
by rewording its own recommendation inherits that recommendation's distinctive vocabulary,
and if the corpus operationalized it, the match is lexical rather than clinical. Write the
situation and the decision — *"a patient on ART has a viral load above 1000; what now?"* —
using vocabulary a practitioner would use unprompted. Do not carry over the
recommendation's characteristic terms. A question that would only be phrased that way by
someone who had already read the recommendation is not a sample from the population being
estimated.

**Record each question's source chapter or topic at sampling time.** Stage 2 decides on
the shape of `ABSTAINED_CORRECT` — which clinical areas the corpus systematically cannot
reach — and that analysis is impossible after the fact without the labels. Recovering them
later means re-deriving the mapping by hand for every abstention, so capture the chapter
with the question or the stage-2 rule has nothing to run on.

**The question set is not evidence and does not enter the corpus pipeline.** It is a
measurement input. It needs no attestation, no digest binding, and no trust root. The
consolidated guidelines are read here as a source of questions, not acquired as a source
of clinical content; acquiring them as evidence is the narrative-materialization path and
is a different piece of work with different gates. Do not conflate the two, and do not
let the absence of a governed acquisition path block the authoring of a question set.

### Which edition to sample

**Sample the 2021 consolidated guidelines and the 2019 HTS guidelines — the editions the DAK
second edition names as its parents.** WHO has continued to publish targeted updates rather
than replacing the 2021 consolidation: guidance on lenacapavir for prevention in July 2025, and
*Updated recommendations on HIV clinical management* on 7 January 2026
([9789240119468](https://www.who.int/publications/i/item/9789240119468)), which WHO states will
be folded into a forthcoming revision of the ART chapter.

Sampling from post-DAK material would count recommendations published after the corpus was
built as unanswerable. That is true but is a **freshness** limit, not a corpus-topology limit,
and it does not bear on the decision this measurement exists to make. Mixing the two inflates
the unanswerable count with the wrong kind of miss. Freshness is a real product question and
gets measured separately, later, against a stated corpus date.

### Question construction

Each sampled recommendation is instantiated into one of the generic clinical question forms in
[benchmarks/questions/ely-generic-question-types.md](../benchmarks/questions/ely-generic-question-types.md),
drawn from Ely JW, Osheroff JA, Gorman PN, et al., *A taxonomy of generic clinical questions:
classification study*, BMJ 2000;321(7258):429-432 — 1,396 questions observed from 152 primary
care doctors, classified into 64 types. The ten published types cover roughly 63% of observed
questions.

This does two jobs at once. It gives the phrasing an empirical, citable basis instead of
resting on the author's intuition about how clinicians talk, and it makes the
situation-not-wording rule above structural rather than a matter of discipline: you fill a
form's slots with a clinical situation, so there is no step at which the recommendation's
vocabulary could be carried across. Record `NO_GENERIC_FORM` when a recommendation fits none of
the ten, and report the rate.

### What was considered and rejected

Recorded so the choice is not revisited from scratch, and so the write-up can say what was
examined.

- **HIVMedQA** ([arXiv 2507.18143](https://arxiv.org/abs/2507.18143); data
  [Zenodo 15868085](https://zenodo.org/records/15868085), CC-BY-4.0) is the closest existing
  artifact: an HIV benchmark built by a team including a general practitioner and an infectious
  disease clinician. **Rejected as the coverage instrument** on three grounds. It is 63
  questions across four categories — eleven basic-knowledge, ten from USMLE Step 1, twenty-one
  vignettes from USMLE Steps 2 and 3, and a fourth category that is Category 3 with false
  information injected — so roughly 42 are distinct, well under the n = 50 stage-1 sample.
  Categories 2 and 3 are licensing-exam items reformatted to open-ended, which test diagnostic
  reasoning over vignettes rather than point-of-care guideline lookup. And the setting is US
  exam content evaluated by a Swiss group, against guidelines written for a public health
  approach in resource-limited settings.
- **HIVMedQA Category 4 is retained for step 4.** Questions carrying deliberately false
  premises are exactly the plausible-negative shape the current suite cannot generate, and the
  licence permits reuse. Do not fold it into the coverage sample.
- **RealMedQA** ([PMC12099375](https://pmc.ncbi.nlm.nih.gov/articles/PMC12099375/)) is the
  closest methodological precedent: 230 question-answer pairs filtered from about 1,200,
  derived from NICE guideline recommendations, written by six UK medical students alongside
  GPT-3.5 generation. Wrong guidelines and wrong population for use as data, but it establishes
  that deriving a question set from guideline recommendations is a published method rather than
  an improvisation, and that human-plus-model authoring with human filtering is an accepted
  construction.
- **Exam and literature benchmarks** — MedQA, MedMCQA, PubMedQA, BioASQ — are the wrong task.
  They test recall and literature synthesis, not guideline lookup, and none is HIV-specific.
- **NLM Clinical Questions Collection.** Questions observed from clinicians at the point of
  care between 1991 and 2003; the Iowa subset alone is 1,062 questions from 48 physicians.
  Intended here as a phrasing-calibration corpus. **Status: not obtainable.** The stated
  download page (`nlm.nih.gov/databases/download/CQC.html`) and the data.gov catalogue record
  both return 404 as of 2026-08-27; an Internet Archive snapshot from 2023-10-05 exists but was
  not retrievable, so its inventory is unverified. Its role is filled by the Ely taxonomy,
  which is the distilled form of the same observational tradition and is fully published. Do
  not cite a question count for the CQC without recovering the source; an earlier draft of this
  plan carried an unsourced figure.

## The coverage measurement

Two quantities, measured on the same sample, with different decision rules.

### Answerable coverage

`p_answered` — the fraction of sampled questions the system answers rather than abstains
on. Classify before reading, not after:

| bucket | meaning |
|---|---|
| `ANSWERED_CORRECT` | answered, and the claim is supported by the cited passage |
| `ANSWERED_DEFECTIVE` | answered and supported, but the presentation, citation, or completeness is poor |
| `ANSWERED_WRONG` | answered, and the claim is not supported by what it cites — the only unsafe cell |
| `ABSTAINED_CORRECT` | abstained, and the corpus genuinely does not hold the answer |
| `ABSTAINED_AVOIDABLE` | abstained, and the corpus does hold the answer — a retrieval or labelling defect, not a coverage limit |

`p_answered` counts the three `ANSWERED_*` buckets. `ABSTAINED_AVOIDABLE` is scored
against the system, not against the corpus, and is the bucket that feeds item 4 of the
priority order.

**The measurement is two-stage, because n = 50 cannot resolve a threshold near the middle
of the range.** At n = 50 an observed 0.30 carries a Wilson 95% interval of
[0.191, 0.438]; that cannot distinguish 0.25 from 0.44, and a re-release decision taken on
it would be a coin flip wearing a number. Every other gate in this project is stated as a
Wilson lower bound; this one is held to the same standard.

- **Stage 1, n = 50.** Decide only if the answer is extreme.
  - Wilson 95% interval entirely below **0.25** → the corpus cannot carry the product.
    Narrative materialization moves from roadmap item 6 to blocking. An observed
    8/50 = 0.16 gives [0.083, 0.285] and does not quite clear this; 0.14 or below does.
  - Wilson 95% interval entirely above **0.50** → the MVP proceeds on this corpus as
    scoped. An observed 30/50 = 0.60 gives [0.462, 0.724] and does not quite clear it;
    0.64 or above does.
  - Otherwise → stage 2.
- **Stage 2, extend to n = 150.** At n = 150 an observed 0.35 gives [0.275, 0.426]:
  precise enough to locate the answer within the middle band, not precise enough to make
  the band disappear. So stage 2 does not get a mechanical rule. It produces a located
  estimate, and the decision is then made on **the shape of `ABSTAINED_CORRECT`** — which
  clinical areas the corpus systematically cannot reach — because a corpus that answers
  35% uniformly and one that answers 35% while missing all of treatment initiation are the
  same number and different products.

Do not extend past 150 hoping the number resolves itself. If the band persists, the
finding is that coverage is middling, and that is an answer.

### Wrong answers

`p_wrong` — the `ANSWERED_WRONG` rate. **The rule is zero observed occurrences at every
stage.** One occurrence stops the MVP and becomes the next piece of work, regardless of
what coverage says.

Report the residual as a bound, never as "zero": with no occurrences, the one-sided 95%
upper bound is **5.8% at n = 50**, **2.0% at n = 150**, and 1.0% at n = 300. None of those
is a clinical safety claim, and the MVP definition above does not pretend otherwise. It is
a screening measurement that can detect a bad system, not one that can certify a good one.

## Stage 1 inputs, built 2026-08-27

The frame, the draw, and a draft question set exist.
[benchmarks/questions/mvp-coverage-who-hiv-v1.json](../benchmarks/questions/mvp-coverage-who-hiv-v1.json)
holds them; [scripts/build_mvp_question_set.py](../scripts/build_mvp_question_set.py) rebuilds
the frame and the draw from the parent PDF and fails if either stops reproducing.

**Frame.** 321 statement-shaped blocks extracted from *Summary recommendations* (printed
xv-xlii of the 2021 consolidated guidelines), of which 191 carry a GRADE rating. Filtering to
graded statements of at least 120 characters containing a deontic verb, and dropping headings,
bullet fragments and footnotes, leaves a frame of **165 formal recommendations**. Good practice
statements are excluded deliberately: the coverage claim is about the guideline's formal
recommendations, which are countable, unambiguous, and what a DAK operationalizes.

**Draw.** 50 at seed `20260827`, uniform without replacement over the frame sorted by id.
Chapter proportions track the frame, so the sample is dominated by chapter 6 as the guideline
itself is.

| chapter | frame | drawn |
|---|---|---|
| 2 HIV testing and diagnosis | 28 | 7 |
| 3 HIV prevention | 11 | 3 |
| 4 ART for people living with HIV | 16 | 6 |
| 5 Advanced HIV disease | 17 | 7 |
| 6 Coinfections and comorbidities | 71 | 24 |
| 7 Service delivery | 22 | 3 |

**A pre-registered expectation, recorded before the measurement runs.** Half the sample is
chapter 6 — TB, viral hepatitis, cervical cancer, STIs, noncommunicable disease, mental health
— and the DAK's business processes cover testing, PrEP, care and treatment visits, PMTCT,
infant diagnosis, diagnostics, referral and reporting. If coverage comes in low, expect chapter
6 to be most of the reason. **That does not invalidate the number.** Uniform sampling over the
guideline's own recommendations is what makes the estimate unbiased, and a clinician asking
this product about TB coinfection is asking a fair question. It does mean the stage-2 shape
analysis is the load-bearing part rather than a formality, and that a low result should be read
as *this corpus operationalizes a narrow slice of its parent* rather than as *this corpus is
bad*.

**Questions.** 49 of 50 usable, each instantiated into an Ely generic form. Every recommendation
fitted one — no `NO_GENERIC_FORM` was needed. The distribution is forms 6 (how should I manage
x, 20), 3 (what test is indicated, 15), 1 (drug of choice, 6), 5 (how should I treat x, 5) and 4
(what is the dose, 3). The diagnostic-cause forms went unused, as
[the type list](../benchmarks/questions/ely-generic-question-types.md) predicted they would for
a prevention-and-treatment guideline.

**Three artifacts, flagged in the data rather than silently repaired.**

- `WHO2021-C5-123` is an anaphoric fragment — *"This may be considered at a higher CD4 cell
  count threshold of <200 cells/mm3"* — carrying a GRADE rating but no referent, because its
  parent statement sits in a preceding block. Marked `UNUSABLE_FRAGMENT` and excluded, so
  stage 1 runs at **n = 49**. Not redrawn: replacing a drawn item to reach a round number is
  the kind of small discretion that makes a seed meaningless.
- `WHO2021-C7-318` restates `WHO2021-C2-026` almost verbatim — adolescent disclosure
  counselling appears in both chapter 2 and chapter 7. Marked `NEAR_DUPLICATE_OF`. The
  guideline genuinely repeats itself, so the frame contains real duplicates and a topic can be
  slightly over-weighted by that alone.
- `WHO2021-C6-145` and `-147` are general physical-activity advice folded into the
  noncommunicable disease section, not HIV-specific decisions. Marked
  `GENERAL_POPULATION_ADVICE`.

**Lexical-leakage screen.** Each question's content terms were measured against the
recommendation it was written from — the same quantity that reads 1.000 on five strata of the
430-case source-derived suite. This set measures **mean 0.447, median 0.500**, with twelve
questions at or above 0.60 flagged `REVIEW_HIGH_SOURCE_OVERLAP`. The metric cannot separate
unavoidable clinical nouns from real leakage: a question about cryptococcal meningitis has to
say "cryptococcal meningitis", and the highest scorer is *"Which children with HIV need
co-trimoxazole prophylaxis?"*, which is how a clinician would actually ask. Treat a high score
as a review priority, not a verdict — and treat the mean as evidence that the set is not a
paraphrase of its own source, not as proof that it is clinically natural. Nothing here tests
naturalness; only a reader can.

**Review status: DRAFT.** The questions are model-authored. Under the division agreed for this
step, the frame and the draw are mechanical and reproducible, but each question is a judgement
about how a clinician would ask, and none has been reviewed by the project owner. Rewrite any
that reads like it was written by someone who had already read the recommendation. Set
`review_status` to `REVIEWED` before running stage 1; a coverage number measured on unreviewed
questions inherits whatever bias the drafting introduced.

## Stage 1 result, run 2026-08-27

Run by [scripts/run_mvp_coverage_stage1.py](../scripts/run_mvp_coverage_stage1.py)
against the reviewed set at n = 49, on the WHO SMART HIV release
`CR_b6155a25415b25f3ba787b3b036da10d`, Qwen3-0.6B dense plus BM25 sparse, top-k 10, no
query expansion. Raw record: `data/local/mvp-coverage-stage1-run.json` (untracked).

**`p_answered` = 21/49 = 0.4286, Wilson 95% [0.300, 0.567]. The rule sends this to
stage 2** — the interval is neither entirely below 0.25 nor entirely above 0.50, which
is the outcome the two-stage design was built to handle rather than a failure of it.

Where the 28 non-answers came from:

| outcome | n |
|---|---:|
| answered, at least one claim rendered | 21 |
| `MODEL_DECLARED_INSUFFICIENT` — gate passed, model judged the passages insufficient | 21 |
| `INCOMPLETE_EVIDENCE_ROLE_SET` — blocked at the gate, no model call | 5 |
| `NO_CLAIM_SURVIVED_GROUNDING` — claims composed, none survived verification | 2 |

**The gate is not what withholds answers; grounding is.** 44 of 49 cleared the role
gate and 21 of those were then refused downstream. That is the division
`retrieval_service` predicts in its own comment — `is_answerable` tests role
completeness rather than topical relevance, and "a claim citing an off-topic passage is
discarded whole at grounding". The architecture behaved as designed. It also means a
retrieval-only run cannot approximate this number: the gate-pass rate was 0.898, which
is an upper bound so loose it excludes nothing.

### The shape, which is the load-bearing part

The pre-registered expectation was that chapter 6 would account for most of a low
result. It does, and chapter 5 is worse.

| chapter | answered | share |
|---|---:|---:|
| 2 HIV testing and diagnosis | 7/7 | 100% |
| 7 Service delivery | 3/3 | 100% |
| 4 ART for people living with HIV | 3/6 | 50% |
| 3 HIV prevention | 1/3 | 33% |
| 6 Coinfections and comorbidities | 7/24 | 29% |
| 5 Advanced HIV disease | 0/6 | 0% |

This is the predicted pattern and it is sharper than predicted. The DAK's business
processes — testing, PrEP, care and treatment visits, PMTCT, infant diagnosis,
diagnostics, referral, reporting — are answered at or near ceiling. Everything the DAK
does not operationalize falls away, and advanced HIV disease is answered **zero times in
six**. Read this as *this corpus operationalizes a narrow slice of its parent*, which is
the reading the definition pre-registered, not as a defect in retrieval.

### What this result does not yet establish

Three limits, none of which the number above can be read past.

- **No question has been classified.** `p_answered` as defined counts
  `ANSWERED_CORRECT`, `ANSWERED_DEFECTIVE` and `ANSWERED_WRONG` together, and every
  `bucket` in the run file is null. The 0.4286 is "the system rendered claims", not "the
  system answered correctly". **`ANSWERED_WRONG` is the stop-the-MVP bucket and requires
  reading all 21.** The 5.9% residual bound at n = 49 applies only if that reading finds
  zero.
- **The 28 abstentions are unsplit.** Whether this is a corpus ceiling or a retrieval
  defect depends entirely on the `ABSTAINED_CORRECT` / `ABSTAINED_AVOIDABLE` division,
  and that division is also the input to priority item 4.
- **This is the Gemini comparator, not the sealed candidate.** The `anthropic-*` Vertex
  quota is still ungranted, so the production Claude lane has not run this set. Every
  grounding and abstention rule is shared, but the number belongs to the lane that
  produced it and must be re-measured on Claude before it is quoted as the product's.

## Corrected priority order

Replaces the six-item list in the roadmap. Changes are marked.

1. ~~**One live generation call against Vertex.**~~ **Done 2026-08-27, on the Gemini
   lane.** The plumbing is de-risked: the SDK reaches Vertex, grounded composition
   returns cited claims, the verification path reports rendered/supported/withheld, and
   49 questions ran end to end without a plumbing failure. *Still open:* the Claude lane
   has never made a live call, because the `anthropic-*` base-model quota on the Vertex
   project is unset and returns 429. That is a quota grant, not a code defect, and it is
   the one thing standing between this result and a measurement on the sealed candidate.
2. **Build the question set** from the parent guidelines, as above. *Moved ahead of the
   reading.* The earlier ordering — read first, author later — is right when authoring is
   expensive and reading is cheap. Extraction from published recommendation statements is
   neither expensive nor an act of imagination, so the argument for reading first
   dissolves and the reading gets a real sampling frame instead of an engineer's guesses.
3. **Run the coverage measurement.** Stage 1, then stage 2 if needed. This is the
   decision point for everything below it.
4. **Harden abstention against plausible negatives.** The negatives come free from step 3:
   `ABSTAINED_CORRECT` cases are HIV questions, naturally phrased, genuinely unanswerable
   from this release — exactly what the current suite cannot produce, since its negatives
   are detectable by release filter alone. *Filter by the bucket:* `ABSTAINED_AVOIDABLE`
   cases are retrieval defects and must not enter the negative set, or the set certifies
   the wrong thing.
5. **Evidence cards and exact highlighting**, per the licence decision. Already built and
   uncommitted; see below.
6. **Corpus breadth via narrative materialization** — or promoted to blocking by step 3.

*Removed from the near-term list, then done 2026-08-27:* wiring `ReleaseServingPipeline`
into `main.py`. The condition this was deferred under — "do it when there is someone to
show" — was met, so it is wired behind `SERVING_ENABLED`, off by default, serving a
validated release without activating it and labelling every answer
`RESEARCH_UNACTIVATED`. The costs are as predicted: the embedding model loads in-process
at roughly 5.4 GiB resident. Verified end to end through the HTTP API on all three
outcomes — a six-claim answer, a model refusal, and a gate block — matching what stage 1
produced for the same questions through `scripts/ask.py`.

## Two decisions that cost nothing and are being deferred anyway

**The licence.** CC BY-NC-SA 3.0 IGO, attributed non-commercial display. Either make the
call or scope the MVP explicitly to restricted rendering, which `AnchorViewer` already
handles completely and honestly — a marked region over an empty frame is the correct
rendering of "we know exactly where this is and may not show it to you". What costs
something is neither branch: it is roughly 1,100 lines of finished work waiting on a
decision nobody has scheduled.

**Commit the working tree.** A complete anchor viewer, its tests, the servable-lifecycle
consolidation, and the e2e coverage are uncommitted. That is downside risk with no
corresponding upside.

## Stays untouched until after step 3

The sealed holdout, the QA-time role classifier, the serving/benchmark deduplication
divergence, and the parked NCD connector. Every one binds to a corpus that step 3 may
condemn, and doing any of them now means doing it twice. Batch all four into the
re-release that follows — which is also when it will be known whether that re-release
carries narrative materialization.
