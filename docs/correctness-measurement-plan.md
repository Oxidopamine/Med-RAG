# Correctness measurement plan

Status: pre-registered analysis plan, revision 3. The four runs of Section 3 were executed on 2026-09-07 before the deposit, under the ordering recorded in Section 11; no label, judge, oracle, checker or statistics output exists. To be deposited
at OSF Registries (embargoed until submission) together with `scripts/cm_statistics.py` and
`scripts/retrieval_overlap.py` before the first run of Section 3 executes. The registered
snapshot is the file as deposited; `rubric_sha256` in every downstream file is the sha256 of
that snapshot, and the DOI, once minted, is recorded in Section 11 as an administrative
amendment that does not change the registered hash. A commit hash inside a private repository
establishes ordering to the author and to nobody else, so the manuscript cites the DOI. The
commit that adds this file also precedes every output it describes, as `94ebd12` preceded
`f3dd9ee` for the stage-1 rule.
Authored: 2026-09-06
Revised: 2026-09-06, revision 2 after four independent reviews (statistics, clinical-journal
editor, IR/NLP, engineering); revision 3 after three verification reviews of revision 2. Every
change is listed in [Section 10](#10-objections-this-plan-was-revised-to-answer).
Scope: the seven measurements that turn the stage-2 coverage run into a paper whose headline
numbers a reviewer can attack and not break: arithmetic reconciliation, baselines with a noise
floor, human labelling of every rendered claim on two axes, an attribution audit of the
grounding check, a validated judge panel, a corpus-side abstention oracle, and the manuscript.
Runs on: one CPU workstation, the Gemini lane on Vertex, and files already on disk. No GPU, no
training, no crowdsourcing.

## Why this document exists

The stage-2 run ([coverage-stage2-gemini-3.7-flash.json](../benchmarks/results/coverage-stage2-gemini-3.7-flash.json))
records what the system did on 164 pre-registered questions. It records nothing about whether
what it did was right. `bucket` is null on every record; the grounding gate that is supposed to
withhold unsupported claims had no occasion to fire, because it tests evidence-ID membership,
never text, and no claim in any run cites an ID outside its own retrieved set; and no baseline
exists against which "46% answerable" means anything.

This plan fixes the analysis before any correctness outcome exists. It does not claim the run
files are unseen: the structural facts below, the arithmetic table in Section 2.1, the
retrieval-recurrence numbers in 5.1 and the stratum sizes in 4.6 were all computed from the
committed runs on 2026-09-06 and are descriptives of a run whose `bucket` is null on every
record. What has not been generated is any label, judge output, oracle output or entailment
score, and no quantity in Section 1.2 is computable from anything now on disk. The manuscript
draws that line explicitly rather than letting one word cover both.

Structural facts, all recomputed from the committed files on 2026-09-06. They are quoted to
fix orders of magnitude and effort; the census this plan labels is a new run (1.2), and every
one of these counts is recomputed from that run before any estimator, budget or subsample is
drawn.

- The stage-2 census is 164 questions, 73 answered, 190 claims, 316 claim-citation pairs citing
  125 distinct evidence IDs, every one of which has full text in
  `data/local/validated-who-smart-hiv-release.json`. 83 claims cite more than one passage
  (54 cite two, 21 three, 4 four, 2 five, 2 six). 50 of the 73 answered records carry a
  multi-citation claim. Pairs per answered record run from 1 to 13, mean 4.33; claims per
  answered record from 1 to 10.
- The pre-registered draw is 149 questions: the n = 150 uniform draw without replacement over
  the 165-item frame, seed 20260827, minus one unusable item. It is a random draw, not an
  ordered prefix. In the stage-2 run it holds 69 answered records, 178 claims and 299 pairs,
  and it is 90.9% of the census, so the two populations are nested and corroborate nothing
  about each other.
- The 316 cited premises split 128 narrative, 64 decision rule, 63 schedule entry, 38
  data-dictionary entry, 23 indicator definition: 188 (59.5%) are table rows, and the 38
  data-dictionary rows (12.0%) are of a kind the system's own taxonomy holds to be not
  recommendation-bearing.
- The 91 abstentions split 77 `MODEL_DECLARED_INSUFFICIENT` after the role gate passed and 14
  `INCOMPLETE_EVIDENCE_ROLE_SET` blocked before any model call.
- The pre-registered bucket definitions in [mvp-definition.md](mvp-definition.md) are about
  support from the cited passages taken together: `ANSWERED_WRONG` is "the claim is not
  supported by what it cites", and a poor citation is `ANSWERED_DEFECTIVE`. Agreement with the
  guideline's recommendation is a different question. This plan labels both and never
  conflates them.
- `GroundedAnswerComposer.compose` returns `NO_EVIDENCE_RETRIEVED` on an empty passage tuple
  before any model call, so a closed-book arm needs a script that calls the model directly.
- A quota failure is today recorded as an abstention: `GeminiGenerationAdapter.generate` wraps
  every exception in `GenerationUnavailableError`, and `compose` catches it and returns
  `reason_code GENERATION_UNAVAILABLE`. One such record sits inside the published
  `coverage-stage1-naive-baseline.json` (`MVPQ-C3-059`), so its stated 21/49 has 48 real
  trials.
- The only same-configuration replicate pair in the repository, the seeded stage-1 pinned run
  and its replicate (`gemini-2.5-flash`, temperature 0, seed 20260827), is 0 of 49 discordant.
  The published stage-1 ablation arms are not on one binding: production ran with seed
  20260827 and the naive arm with no seed.

## 1. Estimands, hypotheses and decision rules

### 1.1 Estimands

Each row below is a label taxonomy. The estimand it feeds is stated in the four attributes a
clinical-informatics reviewer looks for: population, variable, handling of intercurrent events,
and summary measure. Three intercurrent events occur in this design. **Abstention**: no claim
is rendered, so the claim and pair populations are undefined for that question; abstention is
part of the population for every question-level estimand and excluded by construction from
claim- and pair-level ones, which are therefore conditional on the system having answered and
are named that way. **Generation error**: a quota failure or a declined finish is a missing
measurement, excluded from every denominator and reported as a count; a run with error records
on more than 5% of questions is invalid and repeated; an output-budget overrun or a
contract-validation failure is a model behaviour, recorded as its own counted category and
never excluded silently. **`NOT_ADJUDICABLE`**: the label exists but the annotator cannot
resolve it; it is handled by the bounding rule in 4.6, never by silent exclusion.

| Name | Unit | Definition | Who labels |
| --- | --- | --- | --- |
| **Pair attribution** | claim-citation pair | Whether this cited passage's text supports the claim: `ATTRIBUTABLE`, `EXTRAPOLATORY` (partly supported, adds unsupported content), `CONTRADICTORY`, `NO_SUPPORT`. The AttrScore taxonomy plus an explicit no-support class | Human census; entailment checker as a second signal; judge panel as a third |
| **Joint attribution** | claim | The same four labels, judged over the concatenation of every passage the claim cites, in retrieval order. This is the pre-registered notion: `ANSWERED_WRONG` is "not supported by what it cites", the citations taken together | Human census; checker; panel |
| **Guideline agreement** | claim | Whether the claim agrees with the gold `source_statement` for its question: `AGREES`, `PARTIAL` (consistent but omits a condition, population, strength or alternative the recommendation states), `DISAGREES`, `NOT_ADJUDICABLE` (deciding needs clinical knowledge beyond the two texts) | Human census; panel as a second signal |
| **Eligibility drop** | claim | `true` when the gold statement conditions the recommendation on a population or clinical state that neither the claim nor the question supplies. Closed list: pregnancy or breastfeeding; TB or cryptococcal coinfection; weight or age band; renal or hepatic function; prior ART exposure or treatment line; CD4 or viral-load threshold; setting-level epidemiology such as high burden; plus `OTHER` recording the omitted condition verbatim | Human, in the agreement pass |
| **Potential-to-mislead flag** | claim; rated on every **flagged** claim plus a seeded random 30 unflagged claims as a negative control. A claim is flagged when any of five trigger classes holds: joint or any pair attribution is `CONTRADICTORY`, `NO_SUPPORT` or `EXTRAPOLATORY`; agreement is `DISAGREES` or `PARTIAL`; `eligibility_drop` is true; or its record carries `presentation_defect`. Those classes are what 4.6 reports the distribution by; the 30 controls are drawn from claims meeting none of them | Two axes, following the extent-by-likelihood grading of Singhal et al. (Nature 2023), with the four-level extent scale of the AHRQ harm scale rather than that paper's collapsed form: **extent** `NONE`, `MILD`, `MODERATE`, `SEVERE` and **likelihood** `LOW`, `MEDIUM`, `HIGH` that a clinician acting on the claim as rendered reaches that extent. Anchors in Appendix A | Human, provisional; clinician re-rates every flag in the journal pass |
| **Retrievable corpus presence** | abstained question | Whether a record stating the recommendation the question was drawn from is reachable in the 5,145-record release by this dense model and this BM25 under the release filter: `PRESENT` with the evidence ID, or `ABSENT` | Oracle retrieval plus judge; human on all gate-blocked abstentions and a seeded random subsample of the model-declared ones |

`EXTRAPOLATORY`, `PARTIAL` and `eligibility_drop` are in the trigger set because an unconditional
statement of a conditioned recommendation is the inappropriate-treatment-or-omission case this
axis exists to catch, and under a contradiction-only trigger it would never be rated.

Two derived quantities keep the pre-registered contract intact. The **bucket** of each answered
record is derived mechanically from joint attribution by the rule in 4.4. The **support** of
each claim is derived from its pair and joint labels by the same rule, so that a claim fully
supported by one citation and also citing an irrelevant passage is a citation defect, as the
pre-registration says, and not a wrong answer.

Four labels are the weakest in this plan and are named as such: the potential-to-mislead flag,
`question_valid`, `presentation_defect` and `eligibility_drop` are non-clinician judgements by
the person who built the system and authored the questions, and the last two move
`ANSWERED_DEFECTIVE` through 4.4. They are reported as "potential to mislead, rated by a
non-clinician, provisional" and "author-rated", never as clinical harm or independent
validity, and the first two enter no confirmatory quantity. The single annotator is the author.
That is stated in the abstract, in the caption of the headline table, and in the ethics
paragraph. The estimand is agreement with the guideline's text as read by one non-clinician,
and the manuscript never calls it clinical correctness before the clinician pass of 4.7.

### 1.2 Confirmatory quantities, fixed before any run

Everything else in this plan is exploratory and labelled so. Q1, Q2 and Q3 are estimation
targets reported with intervals and no hypothesis test. Q4 and Q5 are the only tests. **The
confirmatory family is stated per paper**: in the Findings paper it is Q5 alone, reported
unadjusted, because no agreement label exists at that submission and Q4 appears there as an
exploratory estimate with an interval and no p-value; in the journal paper it is Q4 on
clinician-adjudicated labels and Q5, and Holm is applied over exactly those two.

| # | Quantity | Population | Pre-stated expectation | Reported as |
| --- | --- | --- | --- | --- |
| Q1 | Count of `ANSWERED_WRONG` records, derived from joint attribution | the 149-question draw of the labelled census run | The pre-registered rule stands: one occurrence stops the MVP. Given SourceCheckup's 50-90% unsupported rate and Liu et al.'s 51.5%, the prior is that the count is not zero | Count; the pre-registered `p_wrong` bound over the 149 questions; and, as a named diagnostic, the count over answered records in the draw with the exact one-sided bound at zero occurrences or a Wilson interval otherwise |
| Q2 | Claim-level unsupported rate: share of claims whose joint attribution is `CONTRADICTORY` or `NO_SUPPORT` | all claims of the census run | Above zero; the ID-membership gate reported zero | Rate with a record-clustered bootstrap 95% interval; the pair-level rate is reported beside it as citation precision, exploratory |
| Q3 | Sensitivity of the entailment checker for detecting non-attributable claims, at the threshold that keeps 90% of `ATTRIBUTABLE` claims (specificity 0.90), scored claim-level against joint human attribution | all claims of the census run, premise = the concatenated cited set | A checker is declared a usable second gate only if the lower limit of the interval on that sensitivity exceeds 0.60; HHEM-2.1-Open reports 76.55% balanced accuracy on prose, so a materially lower value on tabular guideline premises is the finding | Sensitivity with a record-clustered bootstrap interval in which the operating point is re-selected inside every resample; AUC secondary |
| Q4 | Question-level guideline agreement, production versus closed-book, intention-to-serve | all 164 questions | Unknown direction; the corpus may add nothing over parametric knowledge | Paired difference with a Tango score interval and an exact McNemar p. Exploratory in the Findings paper; confirmatory in the journal paper on clinician-adjudicated labels, Holm-adjusted with Q5 there |
| Q5 | Answered-versus-abstained, production versus naive baseline, same session | all 164 questions | The minimum detectable effect is computed and recorded before the naive file is opened; the likely outcome is a bounded null | Paired difference with a Tango score interval and an exact McNemar p; the same test on the two production replicates beside it as the negative control |

Decision rules:

- **Q1 keeps the pre-registered rule and its denominator, and names both denominators in one
  sentence.** `p_wrong` is the `ANSWERED_WRONG` rate over questions asked, as
  [mvp-definition.md](mvp-definition.md) defines it and as `zero_occurrence_upper_bound(questions_run)`
  in `run_mvp_coverage_stage1.py` implements it. Zero occurrences are reported as 0 of 149 with
  the exact one-sided 95% upper bound 1 - 0.05^(1/n) = 1.99%, and, in the same sentence, as 0 of
  the answered records in the draw with the same exact one-sided bound at that n (4.25% if the
  count is 69, as it was at stage 2; never the two-sided Wilson limit, which is 5.27% there),
  because a bound quoted over a denominator that includes the abstentions reads as stronger
  than it is. One or more occurrences are reported as the finding, with the potential-to-mislead
  flags of each and the exact lower bound, and the stop-the-MVP consequence is stated as met,
  which for the manuscript means the MVP readiness claim is withdrawn and the paper reports a
  defect rate.
- **Q4 has a fixed unit and a fixed 2×2.** The unit is the question, because the arms produce
  different claim granularity. A question's arm-level outcome is success if the arm rendered
  at least one claim, at least one claim is `AGREES`, and no claim is `DISAGREES`; an arm that
  rendered no claim scores failure and is never an exclusion; `PARTIAL` alone is failure. A
  question is excluded from the paired test only when an arm rendered at least one claim and
  every claim it rendered is `NOT_ADJUDICABLE`, and the count of exclusions is printed with the
  result. Because "no claim is `DISAGREES`" is monotone decreasing in claim count and the arms
  are not matched on it, mean claims per answered question is reported per arm beside the 2×2,
  and a verbosity-standardised sensitivity is reported as in 4.4. Two further pre-specified
  sensitivity definitions are reported: "any claim `AGREES`" and "no claim `DISAGREES`". The
  subset of questions both arms answered conditions on a post-treatment variable and is
  reported only as a descriptive 2×2. If `NOT_ADJUDICABLE` exceeds 20% of claims in either arm,
  no agreement rate is a headline figure in either paper until a clinician has adjudicated that
  stratum.
- **No quantity is dropped after it is seen.** If a result is unflattering it is the result.
  This sentence is the honesty policy and it is binding.
- **Denominators are fixed now and named per quantity.** The draw carries every interval that
  concerns the pre-registered rule; the census is reported beside it as counts. Q2 and Q3 use
  the census's claims because the entailment audit needs every claim, with the draw's claims as
  a pre-specified sensitivity. No finite-population correction is applied to either, because
  the inferential target is guideline recommendations of this construction against this corpus
  in general, not the 165 in the frame; the intervals are model-based under a Bernoulli
  superpopulation and the manuscript says in the same sentence that they do not license
  generalisation to other guidelines, other corpora or clinician-authored questions. The one
  exception is the model-declared abstention subsample in Section 7, whose target is the
  abstentions this run produced. Every count in this document taken from the stage-2 run is a
  placeholder: `cm_statistics.py` recomputes the answered count, the abstention split, the
  draw's answered count, the stratum sizes, the premise split and the derived subsample sizes
  from production A before any estimator, budget or subsample is drawn, and any figure that
  moves by more than 10% is entered in Section 11.
- **The labelled census is named here.** The confirmatory human census is labelled on
  **production replicate A** of Section 3, so that Q1 to Q5 all rest on runs produced in one
  session with one binding. The stage-2 run remains the descriptive run whose structural facts
  this document quotes; its claims are labelled afterwards, if time allows, as a cross-day
  replication of the labels, and are never mixed into a confirmatory quantity. The label file
  records the census run by path, published name and `question_set.sha256`.

### 1.3 What is exploratory

All reported with intervals and no p-value, and the Results text says so where each appears:
per-chapter and per-Ely-form rates; every headline recomputed with the 9 `NEAR_DUPLICATE_OF`
questions removed, because a near-duplicate pair is one recommendation counted twice and the
independence assumption behind the intervals and the tests does not hold across it; the 8
`GENERAL_POPULATION_ADVICE` questions as a named subgroup crossed with `question_valid`;
lexical-coupling terciles; the bucket-by-agreement cross-tabulation; the verbosity-standardised
record rate; the gate-blocked adjudications; every judge validity statistic; the
potential-to-mislead distribution; the eligibility-drop count; the retrievable-presence split;
the naive arm's unsupported rate on the questions production withheld; the retrieval sweep;
the `DATA_DICTIONARY_ENTRY` citation share; and the lane contrast already in the repository
(production `gemini-2.5-flash` against production `gemini-3.7-flash` at stage 1 is 6 of 49
discordant, twice the production-versus-naive discordance of 3 of 49).

### 1.4 Pre-specification housekeeping

- **Frozen at a deposit.** `rubric_sha256` in every label, judge, oracle and statistics file is
  the sha256 of this document as deposited at OSF; a file whose hash differs is reported as
  labelled under a revised rubric. Recording the DOI in Section 11 is an administrative
  amendment logged there and does not change the registered hash.
- **Frozen analysis code.** `scripts/cm_statistics.py` computes every number in 1.2, 3.5, 4.5,
  4.6, 5.2, 6.2 and 7.3 from the label, judge, oracle, checker and run files, fails loudly on a
  missing input, and is deposited with this document before any label exists. None of the nine
  scripts this plan names exists at the time of writing; the deposit is not made until
  `cm_statistics.py` and `retrieval_overlap.py` do.
- **Software.** Python as pinned in the repository; `scipy` and `statsmodels` are not importable
  in the working tree today and are installed as step 1 of 9.2, with `pip freeze` written to
  `data/local/cm/environment.txt` and released; scikit-learn is not needed, because AUC is
  computed from the Mann-Whitney U statistic in `scipy.stats`.
- **Intervals.** Two-sided 95% unless named one-sided. Wilson means the score interval without
  continuity correction as `wilson_interval` in `run_mvp_coverage_stage1.py` implements it,
  cross-checked once against `statsmodels.stats.proportion.proportion_confint(method="wilson")`.
  Where a proportion's observed count is 0 or its complement, the reported limit is the exact
  one-sided binomial bound for every quantity, not only the bootstrap. Bootstrap means 10,000
  percentile resamples over records with seed 20260906. Multinomial simultaneous intervals are
  Goodman's with the Bonferroni chi-square over k = 5 cells, with Sison-Glaz as a pre-specified
  sensitivity because Goodman's is conservative at a small or empty cell. Paired differences
  carry Tango's score interval with Newcombe's interval as a cross-check. Proportions are
  reported to three decimals with n/N beside them.
- **Multiplicity.** As stated at the head of 1.2, per paper. Nothing in 1.3 is adjusted in
  either paper, and the Results text says so.
- **No interim looks.** Confirmatory labels are read once, after the census and the re-test are
  complete; no confirmatory quantity is computed on a partial census.
- **Deviations.** Every departure from this document is recorded in Section 11 with the date,
  the reason, and whether the outcome was known at the time, and the manuscript reproduces that
  section in the appendix. An empty log is itself reported.

## 2. Recommendation 1: reconcile the arithmetic and make the checker catch it

### 2.1 What is wrong today

Recomputed from the committed files on 2026-09-06:

| Figure | README says | Committed files say |
| --- | ---: | ---: |
| Chapter 2, stage 1 | 7/7 | 6/7 (`MVPQ-C2-026` abstained) |
| Chapter 3, stage 1 | 1/3 | 2/3 |
| Chapter 7, stage 1 | 3/3 | 2/3 (`MVPQ-C7-318` abstained) |
| Chapter 3, census | 6/11 = 54.5% | 8/11 = 72.7% |
| Stage-1 column total | 21 | 20, matching the stated 20/49 |
| Stage-2 claims; combined with the naive run | 187; 241 | 190; 244 |
| Stage-2-authored questions answered | 0.4435 | 53/115 = 0.4609 |
| Naive baseline, stage 1 | 21/49 = 0.429 | 21 of 48 real trials; `MVPQ-C3-059` is a `GENERATION_UNAVAILABLE` quota failure scored as a refusal |
| Stage-1 ablation binding | one lane | production seed 20260827, naive seed null: the two arms were not produced under one binding |
| API tests | 499 | 505 in the working tree, 439 at `ea36aa5`; 62 of the working-tree tests are in files not yet tracked |
| `p_wrong_zero_occurrence_upper_bound_95` in the shipped stage-2 JSON | 0.0181 | a bound over an unclassified run |

The chapter-3 correction inverts the prose around the table: chapter 3 is the best-performing
chapter, not one that "fell back toward the middle". `check_readme_figures.py` passed throughout
because it has no per-chapter check, no claim-count check, and its test-count check is a regex
against the literal string `499`.

### 2.2 Changes

1. **README sections 7.2 and 7.7.** Replace the four chapter cells, the claim counts, the batch
   figure and the surrounding prose. Rewrite the chapter paragraph around what the corrected
   table shows. State in 7.7 that the published ablation is confounded on two counts, the seed
   and the quota failure, and that the paired runs of Section 3 supersede it. Replace "fired 0
   times in 244 claims" with the exact statement: no claim in either run cites an evidence ID
   outside its own retrieved set, 0 of 190 with the mechanism live and 0 of 54 in the naive arm
   where it was disabled, so the predicate had no occasion to fire.
2. **`scripts/check_readme_figures.py`.** In `recomputed_checks()`, load the stage-1 and stage-2
   files and the question set, recompute every chapter cell for both columns, the claim totals
   (`sum(len(generation.claims))` over answered records, plus the naive run), and the 53/115
   figure, and append each as a `(label, needle)` tuple in the existing style. Because the
   checker's needle test is a plain substring match against the whitespace-collapsed README, a
   bare cell such as `6/7` would match anywhere; each chapter needle is therefore the full
   table row. Leave the test count in `CONSISTENT`, which is the only mechanism that catches a
   second document restating it, set to the count CI collects at HEAD and bumped in the same
   commit that changes it; it is 439 at `ea36aa5` and will be higher at the commit that lands
   this plan, because 62 currently untracked tests become tracked. Do not make the checker run
   pytest: `main()` calls `recomputed_checks()` twice and `test_stated_figures.py` runs the
   checker from inside pytest, so a collection there would nest four full collections in every
   CI run. Add a CI step after the test job that compares the collected count with the README
   line and fails on disagreement.
3. **`scripts/run_mvp_coverage_stage1.py` and `scripts/publish_evidence.py`.** `summarize()`
   today counts only `gate_passed` and `gate_reason_counts`; it gains an answered count, an
   abstained count and an error count, and emits `p_wrong_zero_occurrence_upper_bound_95_questions`
   and `..._answered` unconditionally, both pure functions of those counts; a unit test asserts
   0.0181 and 0.0402 on the stage-2 record list. `redact_coverage` nulls both keys, with
   `"p_wrong_bound_withheld": "no correctness classification exists for this run"` under
   `_redaction`, whenever every `bucket` is null, which is the case for every published run
   today; a second test asserts that on the published stage-2 file. Re-run the publisher so
   the committed files change, and let `test_published_evidence_is_current` prove they match.
4. **Error records are not abstentions, anywhere.** `summarize()` excludes error records from
   the answered and abstained denominators and refuses to emit any `p_wrong` bound while one
   exists. `score_mvp_coverage.system_outcome` and `build_claim_audit_worksheet.render` both
   branch on `generation.get("abstained")` and would read an error record as answered; each
   gains an explicit `"error" in generation` branch with a unit test. README section 3.5 lists
   the seven members of `AbstentionReason` as the code has them, says that
   `RETRIEVAL_PIPELINE_NOT_CONFIGURED` is a serving-status code from `question_service.py`, and
   states the policy: a quota failure is a missing measurement, never an abstention.
5. **`docs/qa-promotion-separation.md`.** Its status line says assembly does not use the
   `DECIDED` state and five composite tests are skipped; `release_assembly_service.py` calls
   `promote_decided` and `test_release_assembly.py` runs 10 of 10. Update the status line.
6. **Test layout for released artifacts.** `test_published_sealed_reports_self_verify` globs
   every non-`coverage-` JSON in `benchmarks/results/` and dereferences `document["content"]`,
   and `test_published_coverage_runs_carry_no_corpus_text` asserts `removed_field_count > 0`
   for every `coverage-*.json`. So: every non-run artifact of this plan goes to a new
   `benchmarks/analysis/` directory with its own README, added to the checker's `SCOPE`; and the
   coverage test's assertion is relaxed to `>= 0` with an explicit assertion that no passage
   carries `rendered_text`, because the closed-book run retrieves nothing and redacts nothing.

Done when both checker scripts exit 0 with the new checks counted and `cd apps/api && python
-m pytest tests/unit/test_stated_figures.py -q` passes.

Effort: one day. API calls: none.

## 3. Recommendation 2: baselines and a noise floor

### 3.1 Why four runs

A single production run cannot support a comparison with anything. Four runs, all on the same
164 questions, the same collection
`corpus_cr_b6155a25415b25f3ba787b3b036da10d--vp-7d236ba6d3b956445782e60a`, the same vectors,
one session, one binding:

| Run | Purpose | Mechanism | Gemini calls |
| --- | --- | --- | --- |
| **Production A** | The labelled census; the paired partner for the naive run | Existing harness, production profile | 164 |
| **Production B** | The same-session replicate: noise floor and negative control | Existing harness, second invocation | 164 |
| **Naive baseline** | Every pipeline mechanism off, at a sample size that can bound the effect | Existing harness, `--naive-baseline` | 164 |
| **Closed-book** | Does the corpus add anything over the model's parametric knowledge | New script; the composer refuses an empty passage set | 164 |

The naive row disables the five pipeline mechanisms; it does not remove the model's own
refusal. `SYSTEM_PROMPT` still offers `sufficient_evidence: false`, and at stage 1 the naive arm
passed the gate on 49 of 49 questions yet answered only 21, because 27 of its abstentions were
`NO_CLAIM_SURVIVED_GROUNDING`, which with `discard_ungrounded_claims` off fires only when the
model returned no claims at all. Q5 therefore measures the pipeline's marginal cost on top of a
model that already declines on its own, and the manuscript says so wherever the ablation is
reported. A genuinely unguarded arm would need a prompt without the insufficiency affordance;
that arm is named as unrun.

The stage-2 run is not re-used as a paired partner: it was produced on another day against
endpoints that can change under the author, unseeded, with two quota failures handled by hand.

### 3.2 Generation binding

The stage-2 binding has six fields: `model_id gemini-3.7-flash`, `max_output_tokens 8192`,
`temperature 0.0`, `seed null`, `gcp_region global`, `thinking_budget null`. Every run of this
study pins the first three and the last two, and **sets the seed to 20260906**: seeded, as the
stage-1 arms were at 20260827, because the one seeded replicate pair in the repository is 0 of
49 discordant and an unseeded floor would inflate the noise Q5 is read against.
`thinking_budget` is pinned by absence: no environment variable holds it, `gemini_parameters()`
never sets it, and a run whose binding records a non-null value is discarded. The harness reads
the rest from the environment in `ask.py`'s `gemini_parameters()`:

```bash
export MEDRAG_VERTEX_PROJECT_ID=<project>
export MEDRAG_VERTEX_REGION=global
export MEDRAG_GEMINI_MODEL_ID=gemini-3.7-flash
export MEDRAG_GEMINI_SEED=20260906
```

Authentication is `gcloud auth application-default login`, as the repository documents; the
memory notes record that `gcloud` is not on PATH on this machine and that the two
`MEDRAG_VERTEX_*` variables are read from the process environment, not from `.env`.
`MEDRAG_GEMINI_MODEL_ID` must be set explicitly: the default is the floating alias
`gemini-flash-latest`, which is how the first stage-1 run became unattributable. An unseeded
fifth production run is an optional exploratory sensitivity that measures the lane as stage 2
actually used it.

### 3.3 The harness runs

Before anything else, the smoke test: one question through `scripts/ask.py --generate
--generation-provider gemini`. A quota failure must be found on one call, not on the 300th.

The reconstructed stage-2 invocation, which no document in the repository states in
copy-pasteable form. Every flag except `--run-label` and `--notes`, which the harness change
below introduces, parses against today's `build_parser()`, and every artifact path is present
on disk:

```bash
python scripts/run_mvp_coverage_stage1.py \
  --questions benchmarks/questions/mvp-coverage-who-hiv-v2.json \
  --bundle data/local/validated-who-smart-hiv-release.json \
  --vectors data/local/benchmark-source-derived/qwen3-0.6b-release-vectors.json \
  --collection corpus_cr_b6155a25415b25f3ba787b3b036da10d--vp-7d236ba6d3b956445782e60a \
  --output data/local/cm/production-a.json \
  --run-label production-a --notes "<start and end wall clock>" \
  --top-k 10 --candidate-limit 100 --rrf-k 60 --passage-text 4000 \
  --generate --generation-provider gemini \
  --embedding-backend verified-local \
  --dense-model-root models/local/qwen3-embedding-0.6b \
  --dense-model-manifest data/local/model-manifests/qwen3-embedding-0.6b-cpu-float32.json \
  --dense-artifact-sha256 9ec38140d99f44343a3cdc19a0fc249843e37b86764fd411e168487b2d364b6a \
  --sparse-model-root models/artifacts/qdrant-bm25-unicode-v1 \
  --sparse-model-manifest data/local/model-manifests/qdrant-bm25-who-smart-hiv-v1.json \
  --sparse-artifact-sha256 126656d608bca79f24cde2e5707b55fae76f6b15ddc9ee9078788cf6d9261000
```

`--passage-text 4000` is free: it is not part of `retrieval_configuration`, so it changes no
binding, and it stops the run file from truncating what the labeller reads. The naive run is
the same command with `--naive-baseline` and `--run-label naive-164`; production B is the same
command with `--run-label production-b`. The four runs execute back to back in one session in
the order A, naive, B, closed-book, each about 26 minutes at the stage-2 rate of 9.4 seconds per
question, with start and end wall clock written through `--notes`.

Three harness changes precede the runs, each with a unit test:

- **A quota failure must not be recorded as an abstention.** The fix is in `compose_answer`,
  not in `main()`, because the exception never reaches `main()`. `GENERATION_UNAVAILABLE` wraps
  every backend exception, so the abstention message (which carries the error text) is
  classified by cause: `RESOURCE_EXHAUSTED` is retried up to five times with exponential
  backoff and then recorded as `{"error": "<message>", "abstained": null}`; a declined finish
  is recorded as an error without retry; an output-budget overrun (`MAX_TOKENS`) and a
  contract-validation failure are recorded under their own `error_class` and counted as model
  behaviours, not excluded. `summarize()` reports each class (2.2 items 3 and 4). Add
  `--only-question-ids ID[,ID...]` so errored questions can be re-run into a separate file; the
  merge is by `question_id`, and `summarize()` is re-run over the merged record list, because
  `write_output` recomputes the summary from whatever records it is handed.
- **Name the run.** `run_kind` is the literal `MVP_COVERAGE_STAGE_1` on every file. Add
  `--run-label`, written to a new top-level `run_label` field, and `--notes`, written to
  `notes`; the harness writes no `notes` field today, and the stage-2 file's note was hand-edited
  into a JSON that `publish_evidence.py --check` byte-compares.
- **Label the naive arm where the chain acted.** The naive run answers questions production
  withheld; that is the only place the chain's benefit is observable. The naive arm's claims on
  the questions selected for abstention review in 4.2 (every gate-blocked question and the
  random subsample of the model-declared ones) are labelled in the same passes as the census,
  on both axes, about 90 claims at stage-2 proportions and no additional calls. The pre-stated
  quantity, exploratory: of the questions the chain withheld, the share of the naive arm's
  answers whose joint attribution is `CONTRADICTORY` or `NO_SUPPORT`, estimated with the
  stratified estimator of 7.3, because the gate-blocked questions are a census stratum and the
  model-declared ones a random subsample; a single pooled interval would mis-weight the census
  stratum by about four and a half times. A coverage cost without this number measures only
  what the chain costs and never what it buys.

### 3.4 The closed-book arm

`scripts/run_closed_book.py` reads the question set, sends each question to Gemini with no
passages, and writes a run file with the harness's top-level shape so every downstream script
reads it unchanged. It cannot go through `GroundedAnswerComposer`, and it does not go through
`GeminiGenerationAdapter` either, because that adapter is hard-wired to the `ModelAnswer`
schema, whose claims require at least one `evidence_id`. It uses `scripts/gemini_json.py`, a
helper that reproduces `GeminiGenerationAdapter._request_config` (system instruction,
`response_mime_type application/json`, a caller-supplied `response_json_schema`,
`max_output_tokens`, `temperature`, `seed`, timeout), applies the adapter's
`_without_decoding_bounds` to the caller's schema (Gemini rejects `maxLength`, `minItems`,
`pattern` and their kin, and the adapter strips them in `gemini_answer_schema`, not in
`_request_config`), reproduces `_reject_declined`, retries a `RESOURCE_EXHAUSTED` with
exponential backoff up to five times, and writes a sha256 of the request config into every
output so drift between the research lane and the serving lane is detectable. The judge and
oracle scripts use the same helper. It is loaded by path like `ask.py` and is not production
code.

The closed-book prompt is the production prompt with its passage rules removed and nothing
else changed. Production's rules are: use only the supplied passages; cite evidence IDs; set
`sufficient_evidence` false when the passages do not answer; report disagreement between
passages as a typed conflict; do not restate a passage that does not bear on the question. The
first, second and fourth are passage rules and go; the third and fifth keep their sense with
`passages` replaced by `your knowledge`:

```text
You answer clinical questions.

Rules:
- If you do not know the answer, set knows_answer to false and explain what is missing.
  Guessing is a failure; saying you do not know is a correct outcome.
- Do not state a claim unless it bears on the question asked.
```

The arms therefore differ in retrieval and in the absence of the citation and conflict rules,
and the manuscript records both differences. Response schema: `{"knows_answer": bool,
"claims": [{"text": str}], "insufficiency_note": str | null}`; `knows_answer` is named so that
closed-book abstention is never conflated with `MODEL_DECLARED_INSUFFICIENT` downstream. An
optional secondary arm with "from your knowledge of the WHO consolidated HIV guidelines"
appended is the source-primed upper bound, exploratory, budgeted separately.

Closed-book claims are labelled on the agreement axis only, in the passage-free pass of 4.2,
interleaved with production claims by seeded shuffle. Attribution is not defined for a
closed-book claim.

### 3.5 Statistics, stated before the runs

- **Noise floor.** Between production A and production B, the per-question disagreement rate on
  answered-versus-abstained with a Wilson interval, and the share of answered questions whose
  claim count changed. One replicate pair is one realisation of run-to-run variability, and the
  manuscript says so. The stage-2-versus-A comparison is reported beside it as an upper bound
  that also contains day-to-day drift, a seed difference and two hand-handled retries; it is not
  an input to anything. Run noise does not bias the exact tests, whose null is symmetric
  discordance; its role is to explain the limited power of Q5 and to caveat single-question
  claims.
- **Negative control.** The same exact McNemar is run on A against B and reported beside Q5. A
  significant result there invalidates the Q5 comparison and is reported as such.
- **Minimum detectable effect for Q5, with its parameterisation and anchor stated.** McNemar's
  power depends on discordance between the compared arms, not between replicates. The anchor is
  the stage-1 production-versus-naive pair at fixed `gemini-3.7-flash`: 3 discordant of 49 (1
  production-only, 2 naive-only). The production-only cell is `MVPQ-C3-059`, whose naive
  generation is the quota failure 1.1 classes as a missing measurement, so the error-corrected
  anchor is 2 of 48 (pi0 = 0.042) and the uncorrected one 3 of 49 (pi0 = 0.061). Primary
  parameterisation: the "naive answers, production does not" direction is held at pi0/2 and the
  effect delta is added to the other direction, p_b = pi0/2 + delta, p_c = pi0/2, power by
  enumerating the trinomial over (b, c) unconditionally against the exact two-sided rejection
  set at alpha 0.05 and n = 164. Enumerated on 2026-09-06: at the corrected anchor, power 0.48
  at delta 0.05, 0.62 at 0.06, 0.74 at 0.07, 0.83 at 0.08, 0.89 at 0.09, **an MDE of eight
  percentage points at 80% power**; at the uncorrected anchor, 0.41, 0.53, 0.65, 0.75, 0.82,
  an MDE of nine points, reported beside it as the more conservative figure. Secondary
  parameterisation, at the uncorrected anchor only: p_b + p_c is fixed at pi0, the discordant
  total is left random and the trinomial enumerated unconditionally, and only the direction
  varies; power 0.44 at 0.04, 0.70 at 0.05, 0.92 at 0.06, an MDE near five points. That
  parameterisation caps delta at pi0 and drives p_c toward zero as delta grows, which is the
  whole source of its optimism, and it cannot express any effect above 4.2 points at the
  corrected anchor, which is why it is reported at the uncorrected one and labelled as such.
  Both are recorded in `data/local/cm/mde.json` with the timestamp before the naive file is
  opened. This is a data-dependent design parameter fixed before the comparison arm is
  unblinded, and it is labelled that way, not as fixed a priori.
- **A non-significant Q5 is reported as an interval, never as the MDE.** The bounded null comes
  from the 95% Tango score interval for the paired difference, in the form "the interval for the
  coverage cost of the chain is [-a, +b], so this design excludes costs larger than b points".
  The MDE appears in the design paragraph only.
- **Q4** as defined in 1.2, over all 164 questions, A against closed-book.
- Both tests use `statsmodels.stats.contingency_tables.mcnemar(exact=True)`; the multiplicity
  rule is the one at the head of 1.2.

### 3.6 The retrieval sweep

A reviewer's first question about a 46% coverage figure is whether it is a property of the
corpus, the retriever or the generation policy. A retrieval-only sweep answers part of it, costs
no Gemini calls, and changes no rendered claim, so it invalidates no label.
`scripts/retrieval_sweep.py` uses the oracle's raw `query_points` path (7.2) over the same
collection and release filter, for K in {10, 20, 50, 100} and lane in {sparse, dense, hybrid at
`rrf_k` 60}. `ServingRetrievalService` has no flag to disable a lane, so the sweep does not go
through it; the benchmark runner's `RetrievalMode.SPARSE`, `DENSE` and `HYBRID` are the
reference implementation and are read, not modified. Reported, exploratory: recall at K per
lane of the evidence IDs production A cited on its answered questions; recall at K of the IDs
the Section 7 oracle marks `PRESENT` for the abstentions, so that a steep rise from K = 10 to
50 says the abstentions are a retrieval ceiling and a flat line says they are a corpus limit;
and coverage of the gate's required roles at each K, since the gate is applied at top_k 10 and
its blocks may be an artefact of the cut. A generation sweep at a different K changes every
claim and is Horizon 2.

Effort: two days including harness changes and the sweep. API calls: 656 plus retries, plus 164
for each optional arm.

## 4. Recommendation 3: label every rendered claim

### 4.1 Instrument

`scripts/build_claim_audit_worksheet.py` already accepts `--run`, `--output`,
`--buckets-template` and `--passage-text` (default 700). It renders `rendered_text` from the run
file, where the stage-2 harness truncated it at 600 characters: 912 of the 1,640 passage
records are marked truncated, including 188 of the 215 distinct cited pairs. It sorts answered
records most-flagged-first by `screen_claim`'s `NUMERAL_NOT_IN_SOURCE`, `WIDE_CITATION` and
`SOURCE_TRUNCATED` flags, prints those flags beside each claim, prints a hand-entry `bucket`
field, and renders only the first five of ten retrieved passages in its abstention sections. A
census labelled from that worksheet would be primed by a lexical detector of the same family
as the lexical floor Section 5 evaluates against the labels, in an order chosen by that
detector, and its abstention labels would be defined over half the retrieved set. Changes, all
tested:

- `--run PATH[:ARM]`, repeatable, with `ARM` one of `production`, `closed_book`, `naive`, so the
  agreement pass can merge and interleave claims from three run files; the arm is written to
  the claims template and never to the rendered worksheet.
- `--questions PATH`: load the question set so each record shows its gold `source_statement`;
  the run file carries none.
- `--bundle PATH`, **mandatory for the census**: parse the release with
  `CorpusReleaseBundle.model_validate_json` (this adds an `apps/api` import to a script that is
  stdlib-only today), build `PassagePresenter.for_release(bundle.evidence)` over the full
  evidence list because table-schema discovery scans every record, and render each cited
  passage with `presenter.render(record).text` in full, which is the string the composer sent.
  Re-run `screen_claim` against the full text so its flags stop over-firing. The label file
  records per pair which text source was used, and the census is invalid if any pair used the
  run-file fallback.
- `--shuffle-seed N`: emit records, or claims under the agreement pass, in a seeded random
  order. This replaces the flag-count sort.
- `--no-screen`: suppress every `screen_claim` flag and the `bucket` field, so the annotator
  sees no automated verdict and assigns no bucket by hand. The census worksheets are built with
  `--no-screen`; the screens remain available for an exploratory pass run after the census is
  closed.
- `--pass {agreement,attribution,abstention}`: the agreement worksheet shows the question, the
  gold statement and the claim text with no citations and no passages; the attribution
  worksheet shows the question, each claim and its cited passages with no gold statement; the
  abstention worksheet shows, for gate-blocked and sampled model-declared questions only, all
  ten retrieved passages and the gold statement together, and contains no answered-record
  claim.
- Remove the `[:5]` truncation in the abstention sections.
- `--claims-template PATH`: write the claim-level label skeleton of 4.3. `--buckets-template`
  stays, because `score_mvp_coverage.py --buckets` consumes it and `derive_buckets.py` writes it.

Worksheets stay in `data/local/` and are never committed: they carry WHO passage text that the
release marks `render_allowed: false`.

### 4.2 Rubric and procedure

Three passes, because attribution and agreement contaminate each other, because the
closed-book arm cannot be concealed in a worksheet that shows citations, and because
`question_valid` needs the gold statement while attribution must not see it.

**Pass A, agreement.** Claim text, question and gold `source_statement` only. Production A
claims, closed-book claims, and the naive arm's claims on the withheld questions are
interleaved at claim level by the shuffle seed, so the arm is concealed. On the questions
production abstained, every Pass A claim is necessarily non-production, so blinding is partial
there by construction and the manuscript says so. Labels: guideline agreement, eligibility drop
and, per record, `question_valid`.

**Pass B, attribution.** Production A claims with their full cited passages, at least 24 hours
after Pass A, in a freshly shuffled order, no gold statement. Labels: pair attribution for
every citation, joint attribution per claim, and per record `presentation_defect`. The
abstention adjudications (every gate-blocked record, and the sampled model-declared ones) are a
separately shuffled section of this pass that shows all ten retrieved passages and the gold
statement together and carries no answered-record claim, so no claim label is ever made in a
view holding both.

**Pass C, potential to mislead.** Every flagged claim and the 30 controls in one shuffled list,
with the trigger status and all earlier labels hidden; the rater records extent, likelihood
and one sentence from the claim, the question and the gold statement alone. A control the
rater can identify as clean bounds nothing.

All passes record `started_at` and `finished_at`; the manuscript states that only the
agreement axis is arm-blinded and that the attribution pass is unblinded to arm by
construction.

The rubric is written before labelling starts, piloted on the stage-1 naive-baseline run's 54
claims, which sit outside every census population so no record is labelled twice, and frozen.
If the pilot changes the rubric, the changed version's sha256 becomes the frozen one and the
change is entered in Section 11. The rubric:

1. **Pair attribution.** Read the passage. `ATTRIBUTABLE`: everything the claim asserts is
   stated by or follows directly from the passage. `EXTRAPOLATORY`: the passage supports part
   of the claim and the rest is not in it. `CONTRADICTORY`: the passage says the opposite of
   some part of the claim. `NO_SUPPORT`: the passage does not bear on the claim. Judge whether
   the passage states or entails each quantity the claim asserts, not whether the numeral
   appears in it: "twice daily" against "every 12 hours" is `ATTRIBUTABLE`; "1000 copies/mL"
   against a passage with no threshold is at most `EXTRAPOLATORY`. The numeral-presence screen
   in `screen_claim` is deliberately kept out of this rubric so that Section 5's lexical floor
   is not scored against a reference built from itself.
2. **Joint attribution.** The same four labels over the concatenation of every cited passage in
   retrieval order.
3. **Guideline agreement.** Read the gold statement. `AGREES`, `PARTIAL`, `DISAGREES`,
   `NOT_ADJUDICABLE` as defined in 1.1. Label `NOT_ADJUDICABLE` and move on; do not guess.
4. **Eligibility drop.** The closed list of 1.1. A claim that omits a condition the question
   already states is not a drop.
5. **Potential to mislead.** Extent and likelihood as in 1.1, on the flagged set defined there,
   with the anchors of Appendix A and one sentence of reasoning.
6. **`UNLABELABLE`.** A pair whose passage cannot be read, or a claim whose text is empty or
   malformed, is coded `UNLABELABLE`, reported as a count, and never silently dropped.

Per record: `presentation_defect` (`true` when the record is supported but a reader would be
misled by ordering, redundancy or missing context) and `question_valid` (would a clinician
plausibly ask this, and does the gold statement answer it), each with a note.

For each gate-blocked record: read the retrieved passages and the gold statement and label
`gate_right` (`true` when nothing retrieved could carry the recommendation) and
`dak_has_answer` (`true` when a retrieved passage does carry it and the gate still blocked). For
a seeded random subsample of the model-declared-insufficient records, drawn by simple random
sampling without replacement, of size m = 20, raised to 30 if wall-clock allows and recorded
before any label is written: `any_retrieved_passage_answers` and `question_valid`.

### 4.3 Label file

`data/local/cm/labels-<annotator>-<yyyymmdd>.json`, released to `benchmarks/analysis/`; it
carries no passage text. Appendix B is its data dictionary. Shape:

```json
{
  "schema_version": 1,
  "run": {"local": "data/local/cm/production-a.json",
          "published": "benchmarks/results/coverage-production-a.json",
          "question_set_sha256": "..."},
  "annotator": "author", "rubric_sha256": "...",
  "passes": {"agreement": {"started_at": "...", "finished_at": "..."},
             "attribution": {"started_at": "...", "finished_at": "..."},
             "mislead": {"started_at": "...", "finished_at": "..."}},
  "records": {
    "MVPQ-C2-003": {
      "arm": "production",
      "claims": [
        {"index": 1,
         "pair_attribution": {"EV_c8d9...": "ATTRIBUTABLE"},
         "pair_text_source": {"EV_c8d9...": "bundle"},
         "joint_attribution": "ATTRIBUTABLE",
         "agreement": "AGREES", "eligibility_drop": false, "eligibility_condition": null,
         "mislead": null, "note": ""}
      ],
      "presentation_defect": false, "question_valid": true, "note": ""
    }
  },
  "gate_blocked": {"MVPQ-C5-116": {"gate_right": true, "dak_has_answer": false, "note": ""}},
  "abstained_sample": {"size": 20, "seed": 20260906,
                       "MVPQ-C6-145": {"any_retrieved_passage_answers": false, "question_valid": true}}
}
```

### 4.4 Deriving support and buckets

`scripts/derive_buckets.py --labels ... --run ... --output buckets.json` writes the shape
`score_mvp_coverage.py` reads, `{"buckets": {question_id: bucket}}`, matching the skeleton
`--buckets-template` emits; the scorer's `validate()` rejects any bucket whose `ANSWERED_` or
`ABSTAINED_` prefix disagrees with the record's system outcome, so the derivation reads the
run's `generation` field, not the labels alone.

**Per claim, `support`:** `UNSUPPORTED` if joint attribution is `CONTRADICTORY` or
`NO_SUPPORT`; `PARTIAL` if joint attribution is `EXTRAPOLATORY`, or if any pair is
`CONTRADICTORY` or `NO_SUPPORT` while the joint label is not; `SUPPORTED` otherwise. A claim
fully supported by one citation that also cites an irrelevant passage is `PARTIAL`, which the
pre-registration calls a citation defect. 83 of the 190 stage-2 claims cite more than one
passage, so this choice moves nearly half the census and is made here, not at the keyboard.

**Per record:**

- `ANSWERED_WRONG` if any claim is `UNSUPPORTED`.
- otherwise `ANSWERED_DEFECTIVE` if any claim is `PARTIAL`, or `presentation_defect` is true,
  or any claim carries `eligibility_drop`.
- otherwise `ANSWERED_CORRECT`.
- `ABSTAINED_AVOIDABLE` if retrievable presence is `PRESENT`, else `ABSTAINED_CORRECT`: from
  the human labels on the adjudicated abstentions, and from the judge's verdict on the rest
  only if the known-item control cleared the floor of 7.2; every table carrying those two cells
  marks them judge-derived. **Failure branch:** if the floor is not cleared, the judge-derived
  abstention buckets are withheld; `score_mvp_coverage.py` refuses any run carrying a null
  bucket, so the bucket distribution is then reported over the classified records with the
  unclassified abstentions as a named row, and Q1 is computed directly from the `ANSWERED_*`
  labels rather than through the scorer.

**What the buckets cannot see, and what is reported because of it.** The any-claim rule makes
the record-level bucket a function of verbosity: under a constant per-claim unsupported rate
of 5%, the probability that a record is `ANSWERED_WRONG` runs from 0.05 for a one-claim record
to 0.40 for a ten-claim record. The rule is kept because it is the pre-registered contract, and
three things are reported so it cannot be read as a per-claim quality measure: the claim-level
rate Q2 is the primary claim-level estimand; every table giving `ANSWERED_WRONG` by stratum
also gives mean claims per answered record in that stratum, with the confound named in the
caption; and a verbosity-standardised secondary is reported, the record rate each stratum would
show at the pooled per-claim rate, 1 - (1 - p)^k averaged over that stratum's observed k,
stated as the reference curve under independence of claims within a record, an upper reference
and not a prediction, since the record-clustered bootstrap used everywhere else in this plan
exists because that independence does not hold. No arm comparison is made on the record-level
rule alone. Separately, a claim its passages fully support but which disagrees with the
recommendation is `ANSWERED_CORRECT`, faithful to the pre-registered definition and blind to
the cell a clinician cares most about, so the bucket-by-agreement cross-tabulation over
answered records is a pre-specified table and the Results text says in words that a record can
be `ANSWERED_CORRECT` and still disagree.

### 4.5 Reliability

- **Inter-rater, on a subsample.** Intra-rater re-test measures stability, not validity: a
  systematically biased annotator is perfectly self-consistent. A second reader who is not an
  author and did not build the system labels, from the frozen rubric alone and with no contact
  with the author during labelling, a simple random 50 claim-citation pairs on the attribution
  axes and a simple random 50 claims on the agreement axis, drawn without stratification so the
  coefficients estimate the census coefficients directly, plus every gate-blocked record.
  Report percent agreement, the confusion matrix per axis, Cohen's kappa, Gwet's AC1 for the
  agreement axis and ordinally weighted AC2 for attribution, and the k-category
  prevalence-adjusted kappa (k p_o - 1)/(k - 1), each with a record-clustered bootstrap
  interval, following GRRAS; every disagreement is listed in the appendix. At 50 pairs the
  half-width on a coefficient near 0.7 is about 0.2 after clustering, so these statistics are
  reported as descriptive with their intervals and no threshold is attached to them. If no
  second reader is available before submission, the abstract, the headline table caption and
  the limitations say "single annotator, no independent replication", guideline agreement is
  reported as a self-labelled measurement rather than an estimate of accuracy, and the
  clinician pass of 4.7 is named as the step that changes that.
- **Intra-rater re-test.** At least 24 hours after the census, re-label a seeded random 40
  answered records (about 104 claims and 174 pairs at stage-2 proportions) from freshly
  shuffled `--no-screen` worksheets with the earlier labels hidden. Report the same statistics,
  with the unit and n stated per axis: pairs for pair attribution, claims for joint
  attribution, agreement and eligibility drop, records for `presentation_defect`. Gwet's
  coefficients are primary because they do not collapse under the skewed marginals expected
  here; PABAK is described as a rescaling of observed agreement, not an independent statistic.
- **What the re-test is and is not.** It is an upper bound on the inter-rater reliability of
  this rubric and not an estimate of validity. No confidence interval in this paper includes
  annotator error, and the manuscript says so in the paragraph that carries its first
  human-labelled number. A measurement-error sensitivity recomputes every headline rate on the
  re-test labels for the re-labelled records and reports the difference as the
  annotator-instability band.
- **Blinding and timing.** The census is labelled before any judge output, oracle output or
  entailment score exists; the timestamps make that checkable. Per-record labelling time is
  recorded and its median reported.

### 4.6 Statistics

- Bucket distribution over the draw with Goodman simultaneous 95% intervals and Sison-Glaz as
  the sensitivity; counts over the census. Because the two abstained cells are judge-derived
  outside the adjudicated abstentions, the simultaneous interval is computed twice: once
  treating all five cells as observed, and once with the two abstained cells carrying the
  bias-corrected estimate and variance of 7.3 propagated into the multinomial. The second is
  the headline whenever it is wider, and no table reports the first alone.
- `ANSWERED_WRONG` count and both bounds as in 1.2.
- Q2 over all claims with a record-clustered bootstrap; the pair-level rate beside it.
- Guideline agreement over all claims with a record-clustered bootstrap. `NOT_ADJUDICABLE` has
  an informative mechanism, so no complete-case rate is reported alone. Three quantities go
  together: the `NOT_ADJUDICABLE` share with its interval; the `AGREES` rate excluding it,
  named as complete-case; and the identification region, the `AGREES` rate with every
  `NOT_ADJUDICABLE` claim counted first as not-`AGREES` and then as `AGREES`. The headline is
  the region whenever it is wider than the complete-case interval. The same treatment applies
  to every stratified agreement rate.
- Eligibility-drop count and the potential-to-mislead distribution by trigger class, as
  counts, under the provisional name.
- Strata, fixed and small, with their stage-2 sizes stated now and recomputed from production
  A: answered over census by chapter is 19/28, 8/11, 9/16, 4/16, 21/71 and 12/22 for chapters
  2 to 7; answered over census by Ely form is 9/13, 22/41, 0/4, 1/14, 41/90 and 0/2 for forms
  1, 3, 4, 5, 6 and 8, so every question in the frame appears in exactly one stratum and forms
  4 and 8 are reported as zero-answered counts rather than omitted. Wilson intervals per
  stratum and a precision statement, never a power statement: observed power is a function of
  the observed p-value and carries nothing beyond it. Only chapter 6 against the rest admits a
  minimum detectable difference below 0.25 at 80% power; every other contrast is descriptive.
  The `REVIEW_HIGH_SOURCE_OVERLAP` subgroup is 17 questions of which 7 were answered at stage 2,
  carrying 17 claims and 22 pairs; only the question-level answered rate is stratified over
  it, and counts replace rates wherever a cell is under 10.
- The near-duplicate and general-population subgroup analyses of 1.3.

Effort: four days including the pilot, the instrument changes, `derive_buckets.py` and the
re-test; the census itself is about three days of reading. API calls: none.

### 4.7 The clinician pass, journal paper only

One HIV-experienced clinician, not otherwise involved in building the system, re-labels a set
bounded by a stated rule: every claim labelled `DISAGREES` or `NOT_ADJUDICABLE`; a seeded random
30 of the `PARTIAL` claims and a seeded random 30 of the flagged set not already included; and
a seeded random 20 unflagged claims as controls; capped at 90 claims, with sampling fractions
recorded before the file is sent if any stratum exceeds its quota. The time estimate is taken
from the author's median per-claim labelling time recorded in 4.5, not asserted. They see the
claim, the question and the gold statement, with the author's labels hidden and the arm
concealed. Disagreements are resolved by discussion and the adjudicated label stands; the
author's original labels are retained and reported as a secondary agreement analysis. The
clinician-adjudicated agreement rate over the census is the stratified estimator of 7.3
applied to the sampled strata with the finite-population correction on each sampled term and a
record-clustered interval. The journal paper's guideline-agreement and potential-to-mislead
figures are the clinician-adjudicated ones, and only the clinician-adjudicated version may be
called harm; the Findings paper reports neither as a headline. Authorship follows ICMJE
criteria and the contribution is stated. If no clinician is recruited, the journal paper drops
the mislead axis entirely rather than publishing non-clinician ratings, and says so.

## 5. Recommendation 4: the attribution audit of the grounding check

### 5.1 The analytic part

`_ground` computes `set(cited).issubset(retrieved_ids)`. It rejects every citation of an ID
outside the retrieved set and accepts every citation of an ID inside it, whatever the text
says. That is a property of the predicate and goes in the system description as one sentence;
no experiment demonstrates it.

The numbers worth measuring describe how concentrated retrieval is, which bounds only the
cross-question form of mis-citation. Recomputed by hand on 2026-09-06 over the 164 retrieved
sets of ten in the stage-2 run, pending the deposit of `scripts/retrieval_overlap.py`, which
does not exist at the time of writing and is written before the manuscript quotes any of them:
784 distinct evidence IDs fill 1,640 slots; **1,180 of those slots (72.0%) hold an ID that also
appears in at least one other question's set**; 12.2% of question pairs share at least one
passage; the mean probability that one given other question's set contains a particular
retrieved ID is 2.1%. The first figure characterises the corpus and says the membership
predicate is permissive rather than tight; the last is the per-pair collision rate, and an
earlier draft quoted only it. None of these bounds the failure that matters, which is
intra-question: the model citing a passage that is in this question's top ten and does not
support the claim. That is what 5.2 measures.

### 5.2 The empirical part

The human joint-attribution labels are the reference standard; the pair labels are the
secondary. The audit asks whether a sub-1B CPU checker recovers them on premises that are mostly
table rows. It is a diagnostic-accuracy study of an index test against a human reference and is
reported per STARD 2015.

`scripts/entailment_audit.py`:

- Loads the run, the bundle and the label file. Renders each cited passage in full through
  `PassagePresenter`, exactly as the model saw it.
- Scores every pair (premise = rendered passage, hypothesis = claim text) and every claim
  against the concatenation of its cited passages in retrieval order.
- **Instrument, in order of attempt, with the model id, the pinned revision and the
  `transformers` version written into the output file.** First, HHEM-2.1-Open,
  `vectara/hallucination_evaluation_model` at revision
  `8e4a2e6e96c708cc76c2344f7e4757df2515292c` (FLAN-T5-base, Apache-2.0), loaded with
  `AutoModelForSequenceClassification.from_pretrained(..., revision=..., trust_remote_code=True)`
  and scored with `model.predict(pairs)`, which returns one score in [0, 1] per pair. The
  revision is pinned because the same repo id has served an `hhem-1.0-open` branch beside the
  current `main`, and `trust_remote_code` means the scoring code itself comes from that
  revision. This venv runs `transformers 5.15.1` and the card's remote code was written against
  4.x, so HHEM is a candidate, not the plan. Second, a no-remote-code NLI cross-encoder,
  `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` at revision
  `6f5cf0a2b59cabb106aca4c287eed12e357e90eb`, entailment probability as the score. Third,
  `lytang/MiniCheck-Flan-T5-Large` at revision `96eafd01cee2d16cf81aaa2fb226b14f422a37b3`. The
  floor, always reported: a lexical score defined here as the fraction of the claim's content
  tokens (alphabetic, longer than three characters, casefolded, no stemmer, the token rule of
  `source_term_overlap`) that occur as substrings of the concatenated cited text. No claim-to-
  passage overlap exists in the repository today; it is written for this audit.
- Records CPU wall-clock per pair and in total; the vendor's 1.5 seconds per 2k tokens is a
  claim to verify.

Operating points, fixed here: **0.5** is the pre-specified operating point for the flagged
rate; the 90%-specificity point for Q3 is estimated by leave-one-record-out, and that selection
is re-run inside every bootstrap resample, so the record-clustered interval on the sensitivity
carries the variability of the operating point and not only of the score; an interval computed
at a threshold fitted once on the full label set would be anticonservative against a decision
rule stated as a lower limit. Reported: Q3 as in 1.2; AUC by the Mann-Whitney identity, with a
record-clustered bootstrap interval, against `ATTRIBUTABLE` versus the rest and against
`CONTRADICTORY`/`NO_SUPPORT` versus the rest, both secondary; the pair-level secondary
stratified by prose premises (128 pairs at stage 2), tabular premises (188) and the 38
`DATA_DICTIONARY_ENTRY` pairs, which the role gate treats as unable to bear a recommendation and
which nothing stops the model citing, a finding reported in its own right; the claim-level
primary stratified by whether the concatenated premise is all prose, all tabular or mixed, with
the mixed cell reported as a count; the 2×2 of checker flag against human label; every
disagreement listed in the appendix by `question_id`, claim index and evidence ID, quoting no
passage text; and the lexical floor's AUC on the same labels. Before any label is read,
`data/local/cm/mde.json` also records the expected half-width of the Q2 interval and of the Q3
sensitivity interval across a prior grid of unsupported rates from 0.05 to 0.35, so a Q3 that
turns out uninformative is a pre-stated outcome.

The framing is that the shipped check verifies provenance and the checker verifies support,
and the paper reports how far apart those two notions are on this corpus with human labels as
the arbiter. Proof-Carrying Answers' "necessary but incomplete" is the theoretical statement of
the same point.

Effort: one to two days, the spread being whether HHEM loads. API calls: none.

## 6. Recommendation 5: the judge panel, validated rather than trusted

### 6.1 Design

Two checkpoints of one vendor's model, because the Claude lane is quota-blocked and no
Anthropic key exists: this is a two-judge same-vendor panel, not a jury, and the manuscript
calls it that. `gemini-2.5-flash` is primary and `gemini-3.7-flash` is a sensitivity analysis,
because 3.7-flash generated the outputs and a model judging its own production is the
self-enhancement bias Zheng et al. describe. The primary is also the more permissive of the
two: at stage 1 it answered 24 of 49 where 3.7-flash answered 20, so it may lean toward
`ATTRIBUTABLE`; the 3.7-flash arm brackets that and the manuscript says so. The checker of
Section 5 is the cross-family third member on the attribution axes. The panel is a second
signal. The human census is the arbiter of every disagreement and is used in every reported
quantity without exception.

`scripts/judge_claims.py --run ... --bundle ... --questions ... --judge gemini-2.5-flash
--arm production --output ...` makes **two calls per record**, never one:

- **Attribution call.** The question, each claim, and the full rendered text of each cited
  passage. Asks for pair attribution, joint attribution and one sentence of rationale each. The
  gold statement is not in the prompt; this is the information the human rubric's steps 1 and
  2 have, which is what makes the agreement statistic interpretable.
- **Agreement call.** The question, each claim, and the gold statement. Asks for guideline
  agreement and eligibility drop. The passages are not in the prompt.

Temperature 0, seed 20260906, model ID and UTC date recorded. The output file stores the
prompt template and the sha256 of each filled prompt, never the filled prompt, because a filled
attribution prompt carries WHO passage text.

### 6.2 Validity checks, pre-specified with pass criteria

| Check | What it measures | Size | Calls |
| --- | --- | --- | --- |
| Human agreement | Gwet's AC2 (attribution) and AC1 (agreement), kappa and raw agreement, human against each judge | all answered records of production A, both calls | 2 per record per judge |
| Stability | the judge against itself with seed 20260907 and the claims within each record in a different seeded order | 30 records, both calls | 60 |
| API determinism | a same-seed identical-input repeat, reported separately | 30 records, both calls | 60 |
| No-gold arm | the agreement call without the gold statement; measures how much "agreement" is the judge echoing the question | 30 records | 30 |
| Mis-citation sensitivity | 30 records in which one claim's cited passage text is replaced, administered inside the per-record attribution call so the judge's unit matches the census: 15 by a passage of the same `kind` retrieved for a different question, 15 by an on-topic passage from this question's own retrieved set that the claim did not cite. The within-question half is the failure the membership gate cannot catch and is the discriminative one; the halves are reported separately | 30 records | 30 |
| Closed-book and naive arms | the agreement call on closed-book claims and the naive arm's withheld-question claims | up to 164 + 34 records | up to 198 |

Pass criteria and consequences: the panel is reported as a corroborating signal only if AC2
and AC1 against the human census are at least 0.60 on both axes; below that, the judge output
is a failed validation and no panel number appears outside the appendix. Stability: percent
agreement at or above 0.90, else the judge is reported as unstable and every label carries the
instability rate. No-gold: if the no-gold call reproduces at least 0.85 of the with-gold
`AGREES` labels, the agreement axis is reported as substantially question echo and the panel's
agreement numbers are withdrawn. Mis-citation: the Wilson 95% limit on the `ATTRIBUTABLE` rate
over the 30 swapped claims; the check passes at 3 or fewer (upper limit 25.6%), fails at 8 or
more, and is reported as indeterminate between. The mis-citation set is built by hand, checked
not to be a near-duplicate of the original passage, and never reused as a grounding experiment.

Effort: one and a half days. API calls, at stage-2 proportions: 146 for the primary judge on
the census, 146 for the sensitivity judge, 180 for the validity checks, up to 198 for the other
arms: about 670.

## 7. Recommendation 6: the corpus-side abstention oracle

### 7.1 Why the parent guideline cannot be the oracle

Every question was drawn from the parent guideline's summary recommendations, and the narrative
extraction of that guideline located 165 of 165 of them. An oracle over the parent returns
"present" for essentially every abstention and measures its own recall, not the corpus. The
question a fail-closed system has to answer is whether the corpus it serves from contained the
recommendation, so the oracle runs over the 5,145-record DAK release.

### 7.2 Method

`scripts/abstention_oracle.py`, for each abstained question of production A and, as the
known-item control, each of its answered questions:

1. Query the live collection through Qdrant directly, sparse lane and dense lane, with both the
   question and the gold `source_statement` as queries, `limit 200` each, never below serving's
   own `candidate_limit` of 100, using the same release filter as serving
   (`corpus_release_id`, `approval_status APPROVED`, servable lifecycle) through a raw
   `query_points` call with `using: sparse` and `using: dense`. `ServingRetrievalService` cannot
   run a lane alone, and BM25 IDF is applied inside Qdrant and not implemented in the
   repository, so neither is reused. **Compute the known-item recall on these four rankings
   before anything else is added**: for an answered question, whether any evidence ID the
   model cited appears in their union. That figure is the oracle's recall floor. Then union the
   four rankings with the ten passages the run actually retrieved, fuse by RRF at k = 60,
   deduplicate by evidence ID, and **carry the top 25 fused candidates** into step 2; the share
   of cited IDs that survive the cut is reported separately as the shortlist's retention rate.
   The shortlist rule is fixed here because it sets the oracle's recall ceiling.
2. Render the shortlist in full and judge it in two calls of at most 15 candidates each, taking
   the union of `PRESENT` verdicts; a single call over a two-hundred-passage context is a needle
   task with a known lost-in-the-middle failure. Output `PRESENT` with the evidence ID, or
   `ABSENT`, with a rationale. The oracle prompt is validated separately from the claim judge,
   by the human reads below. Released oracle outputs carry the prompt template and the sha256
   of each filled prompt, never the filled prompt.
3. Human check: every gate-blocked record and the seeded random m of the model-declared ones,
   labelled in Pass B before any oracle output is viewed.

**Floors, pre-stated.** If the pre-union known-item recall is below 0.90 the shortlist is
widened to 50 and both figures are reported; if it is below 0.80 at 50, the corpus-presence
split is reported as a lower bound on `ABSTAINED_AVOIDABLE` only and the judge-derived buckets
for the unread abstentions are withheld, with the consequence stated in 4.4.

### 7.3 Estimators and reporting

- The abstentions of production A are two strata: the G gate-blocked, human-labelled in full, a
  census contributing no sampling variance; and the M model-declared, of which m (20, or 30 if
  wall-clock allows, recorded before any label is written) are drawn by simple random sampling
  without replacement. The presence rate is the stratified estimator
  p = (G pG + M pm)/(G + M) with Var(p) = (M/(G + M))^2 [pm (1 - pm)/(m - 1)] (1 - m/M); the
  finite-population correction applies here, the stated exception to 1.2. The interval on p is
  the Wilson interval on pm rescaled by M/(G + M) and by the finite-population factor, with the
  correction's effect shown; a Wald interval is not used, because pm near 0 or 1 is the expected
  case. At m = 20 the half-width on pm is about 20 points even so, which is why 30 is preferred.
- **Judge labels on the unread model-declared abstentions are bias-corrected, not trusted.**
  Because the m are a random sample of the M, a design-based difference estimator is valid on
  that stratum: p_corrected = p_judge(M) + (1/m) Σ (human_i - judge_i) over the sample, with
  variance from the within-sample discrepancies. This is the only place prediction-assisted
  estimation is used; no claim-level quantity has a random subsample. The uncorrected
  judge-only split is reported beside it. Judge-versus-human Gwet's AC1 on the G + m
  human-read records must be at least 0.60 for the judge split to be reported as an estimate
  rather than a bound.
- The split crossed with the reason (gate-blocked against model-declared); the oracle's
  pre-union known-item recall and the shortlist retention rate; for the gate-blocked records,
  how many the gate was right about, with a Wilson interval, the one place the paper can show a
  mechanism preventing something; and abstention precision and recall from the 2×2 of
  should-abstain (`ABSENT`) against did-abstain, per arm, with Wilson intervals, because
  MedAbstain shows the two diverge.
- The quantity is named "retrievable corpus presence" everywhere it appears: `ABSENT`
  over-counts `ABSTAINED_CORRECT` by whatever this retriever misses.

Effort: two days. API calls: about 330 judge calls across the abstentions and the answered
controls at stage-2 proportions, plus 656 Qdrant queries and 328 local query embeddings.

## 8. Recommendation 7: the manuscript

### 8.1 Venues, registration and reporting standards

Horizon 1 is the ML4H 2026 Findings track: four pages excluding references, appendices without
limit, non-archival, OpenReview, deadline 2026-09-10 23:59 anywhere on Earth, checked on the
official call on 2026-09-06; the review model is confirmed before submission and an anonymised
artifact mirror is prepared if it is double-blind. Horizon 2 is an archival journal: PLOS
Digital Health first, because it has no length limit, its data and code availability rules are
already satisfiable, and its APC is about $3,043 with waivers; JAMIA Open second, 4,000 words
and about $3,625, which the outline below does not fit without cuts. The Findings posting has
no DOI and is not a publication: it is declared in the journal cover letter as a prior
non-archival presentation, with the OpenReview link, and each journal's policy on prior
workshop presentation is checked directly before submission.

This document is deposited at OSF Registries before the first run of Section 3, and the
manuscript cites the registration DOI and timestamp wherever it says pre-registered; where only
a commit hash exists, the text says "prospectively fixed in a private repository". The derived
artifacts of 9.3 are deposited at Zenodo under one record, which settles the data-availability
statement: the derived data are open, and the WHO guideline text is third-party material
identified by the ISBN and NCBI Bookshelf ID carried in the question set's `frame` block.

The manuscript follows TRIPOD-LLM (Gallifant et al., Nature Medicine 2025;31:60-69), naming
the version and access date and the module applied, with the completed checklist in the
appendix; Section 5 is additionally reported per STARD 2015, and 4.5 per GRRAS.

### 8.2 Outline

1. Title that names the case: one corpus, one lane, one annotator. Working title: "What a WHO
   Digital Adaptation Kit can answer: coverage, guideline agreement, and a claim-grounding check
   that never fires". The precise claim is that one of the five mechanisms, claim-level
   grounding, had no occasion to fire, because no claim in any run cites an evidence ID outside
   its own retrieved set, while duplicate suppression removed 613 passages, role qualification
   disqualified 659 role claims, the role gate blocked 14 questions and the sufficiency signal
   lowered 77 in the stage-2 run; Table 1 reports all five counts from production A and the
   paper never describes the chain as inert.
2. Abstract stating the estimand, the annotator, the pre-registered denominator, Q1 to Q5 with
   their intervals, and the noise floor.
3. Introduction: the field measures coverage or clinician-graded accuracy, rarely both, never
   pre-registered, never on a deployed operational corpus whose structure was audited first.
4. Corpus and system, one paragraph, precise: the DAK; fail-closed serving; grounding is
   evidence-ID membership; the sufficiency signal can only lower an outcome; the verification
   and applicability modules are fixture-tested and not called in serving.
5. Protocol: this document, summarised, with the registration DOI.
6. Figure 1, a flow diagram in the STARD idiom: 165 recommendations in the frame, 1 excluded as
   `UNUSABLE_FRAGMENT`, 164 run, the gate-passed and gate-blocked split, answered and
   model-declared-insufficient, error records, claims and pairs entering the census, with the
   labelled, `NOT_ADJUDICABLE` and `UNLABELABLE` counts on each branch and the 149-question draw
   marked. Every denominator in every table traces to a box in this figure.
7. Results 1, Table 1: bucket distribution over the draw with simultaneous intervals,
   `ANSWERED_WRONG` with both bounds and its flags, agreement with the identification region,
   the bucket-by-agreement cross-tabulation, the closed-book comparison, the noise floor.
8. Results 2, Table 2: attribution audit, human against checker, prose against tabular premises.
9. Results 3: retrievable-presence split with the known-item control and the gate-blocked
   adjudications; the naive arm's unsupported rate on the withheld questions; the ablation as a
   Tango interval with the MDE in the design paragraph; the retrieval sweep.
10. Lexical coupling and contamination, two quantities never called leakage jointly. Coupling:
    question-to-retrieved-passage term overlap, the fraction of a question's content tokens
    (the token rule of `source_term_overlap`) occurring as substrings of the concatenated
    rendered text of its ten retrieved passages, rendered through `PassagePresenter` from the
    bundle and never from the run file's truncated text, one value per question, tercile
    boundaries computed and written to `data/local/cm/coupling.json` before any label is viewed
    and released as numbers because the published run files carry no passage text. Every
    headline is stratified by those terciles; the 17 `REVIEW_HIGH_SOURCE_OVERLAP` items are a
    named subgroup at question level; the question-to-gold `source_term_overlap` is kept as a
    covariate with its non-reproducibility stated. Contamination, whether the 2021 guideline is
    in the model's pretraining data, is what the closed-book arm measures and the coupling
    metric does not.
11. Limitations, as one populated evidentiary leaf of a safety case: single non-clinician
    annotator with a conflict of interest; potential-to-mislead flags and question validity
    rated by the author, a non-clinician; same-vendor panel; general-domain checker on tabular
    text; one lane; one corpus; the 2021 edition; dead safety modules; retrievable rather than
    true presence; private repository this cycle.
12. Ethics, data and licensing, a full section: (a) no patient data, no PHI, no human
    participants, institutional review not sought, stated in the journal's form; (b) the
    annotator is the author and the architect, stated in the abstract, the headline caption and
    the conflict statement; (c) source and licence: WHO SMART Guidelines DAK for HIV, second
    edition, with the 2021 consolidated guidelines as parent, CC BY-NC-SA 3.0 IGO, the WHO
    attribution string in full and the WHO disclaimer for adaptations, the released question set
    labelled with that licence rather than the repository's Apache-2.0 grant; (d) currency:
    agreement is measured against the 2021 edition and WHO HIV guidance has been updated since,
    so agreement is not a claim about current standard of care; (e) processing: WHO-licensed
    passage text is transmitted to Google Cloud Vertex AI for generation, judging and the
    oracle; (f) released claim text is model output, includes statements the study labels
    unsupported, is not clinical advice, and carries that notice; (g) data availability in the
    journal form, with the redaction rule named and executable and the corpus rebuildable from
    the public WHO source; (h) funding: none; APC route and waiver eligibility in the cover
    letter.
13. Appendices: corrected chapter table with intervals; prompt templates, never filled prompts;
    the rubric and Appendix A anchors; every human-judge, human-checker and inter-rater
    disagreement, identified by evidence ID and quoting no passage text; the cross-kit
    structural table; the citation-correction log; the deviations log; the reproduction
    commands; the TRIPOD-LLM and STARD checklists.

### 8.3 Rules for the text

- No WHO corpus passage text, the DAK record content the release marks `render_allowed: false`,
  appears in the manuscript or in any released artifact; the redaction rule in
  `publish_evidence.py` enforces it for run files, and 6.1, 7.2 and 9.3 enforce it for judge
  and oracle outputs by releasing templates and prompt digests only. The 165 GRADE-rated
  recommendation statements in the question set are a different case: they are the sampling
  frame, the study is not reproducible without them, and they are quoted with attribution under
  CC BY-NC-SA 3.0 IGO exactly as NOTICE and README section 12 already state. Model-composed
  claims are the system output and may be quoted.
- Three literature claims are corrected before submission: HIVMedQA category 4 is cognitive-bias
  reframing, not false premise; the "below 50 percent on plausible negatives" line is
  re-anchored on Cancer-Myth's under-43 percent with FaithEval as corroboration and hedged as the
  closest analogue; the "twenty accuracy points" sentence in `generation_schemas.py` is deleted.
- The word "pre-registered" is used for the stage-1 stopping rule and for this document's
  confirmatory set, each with its DOI, and for nothing else.
- Model identifiers, seeds and run dates appear for every generation and judging lane; the
  intended Claude lane is named as unrun.

### 8.4 What Horizon 1 submits

Decided now rather than on the evening of 2026-09-08. The census of Section 4 is about three
days of labelling on its own, Sections 2 and 3 are two and a half days before it, and the
deadline is four days away. Horizon 1 therefore submits the measurement-critique paper: the
cross-kit structural audit, the corrected coverage table of Section 2, the noise floor and the
paired bounded null of Section 3, the analytic and recurrence results of 5.1, and no correctness
claim; Q5 is its only test and is reported unadjusted; the single-annotator design is described
as future work with this registration cited. Sections 4 to 7 run without compression for
Horizon 2. If the census unexpectedly completes before the deadline it is added; a half-labelled
census is never reported as a partial result.

## 9. Compute, schedule and what is released

### 9.1 Budget

| Step | Gemini calls | CPU or wall clock | Human time |
| --- | ---: | --- | --- |
| 1 Arithmetic, tests, publisher | 0 | seconds | 1 d |
| 2 Harness changes, four runs, sweep | 656 + retries; 164 per optional arm | four runs at about 26 min each, 103 min; sweep minutes | 2 d |
| 3 Instrument, pilot, census, re-test | 0 | seconds | 4 d |
| 4 Attribution audit | 0 | minutes, measured | 1-2 d |
| 5 Judge panel and validity checks | ~670 | wall clock | 1.5 d |
| 6 Oracle | ~330 | about 10 min | 2 d |
| 7 Statistics script | 0 | seconds | 1 d |
| 8 Manuscript | 0 | none | 3 d |
| Total | ~1,660 | about three hours | 15-16 d |

This is the Horizon 2 budget. An earlier draft's 7.5 days assumed the statistics layer was free
and the census was a day and a half; neither was true.

### 9.2 Order

1. Install `scipy` and `statsmodels`; write `environment.txt`; smoke-test the Gemini lane; write
   `cm_statistics.py` and `retrieval_overlap.py`; deposit this document and both scripts.
2. Section 2. Nothing else cites a number until it lands.
3. Harness changes with tests; the four runs of Section 3 back to back; recompute every
   stage-2 placeholder from production A; `mde.json` and `coupling.json` written before any
   label exists; the retrieval sweep.
4. Rubric pilot on the stage-1 naive claims, then Pass A, then Pass B at least a day later,
   then Pass C, before any judge, oracle or checker output exists.
5. Sections 5, 6 and 7 scripts, run after the census is closed; the checker is scored against
   finished labels.
6. Re-test after 24 hours; inter-rater subsample if a second reader exists; statistics;
   manuscript.

Horizon 1 executes steps 1 to 3 and the write-up of 8.4 only.

### 9.3 Released with the paper

- The four run files, published to `benchmarks/results/` through `COVERAGE_RUNS` with source
  names `cm/production-a.json`, `cm/production-b.json`, `cm/naive-164.json` and
  `cm/closed-book-164.json`, redacted by `publish_evidence.py`; the coverage test relaxed as in
  2.2 item 6 because the closed-book file redacts nothing.
- Every non-run artifact, in `benchmarks/analysis/` with a README: the label file, judge
  outputs and oracle outputs carrying prompt templates and filled-prompt digests only, checker
  scores, `mde.json`, `coupling.json`, `environment.txt`, the rubric, and the statistics
  script's output as one JSON. None carries passage text.
- New `RECOMPUTED` checks so the manuscript's tables recompute from the released files.

## 10. Objections this plan was revised to answer

Four independent reviews of revision 1 (a biostatistician, a clinical-informatics editor, an
IR/NLP reviewer and the implementing engineer) each returned "not ready"; three verification
reviews of revision 2 returned "ready with edits". Every item below changed the document.

| Objection | Answer in this plan |
| --- | --- |
| Q1 quoted one bound (2.0%) over one denominator while its reporting column named another; the pre-registered denominator is questions asked; the diagnostic cell mixed a Wilson limit with an exact bound | Both denominators named in one sentence, 1.99% at 149 and the exact one-sided bound at the answered count, with the pre-registered one primary; zero counts take the exact bound everywhere (1.2, 1.4) |
| The bucket rule convicted a claim on any single unsupported citation, tightening the pre-registered set-valued definition on the one quantity with a stopping rule; 83 claims cite more than one passage | Joint attribution added as its own label; `support` derived per claim; a claim supported by one citation and also citing an irrelevant one is `PARTIAL`, a citation defect (1.1, 4.4) |
| Q4 conditioned on both arms answering, had no claim-to-question rule, then in revision 2 excluded abstentions by a vacuous clause and inherited the verbosity confound | Question-level intention-to-serve outcome; abstention is failure, never an exclusion; mean claims per arm and a verbosity-standardised sensitivity reported beside the 2×2 (1.2) |
| The census run was unnamed, Q4 paired across sessions, and revision 2 hard-coded stage-2 counts as properties of a run not yet made | The census is production replicate A; every stage-2 count is a placeholder recomputed from A before any estimator or subsample, with moves above 10% logged (1.2) |
| The MDE did not reproduce; its input was replicate discordance; its anchor included the quota-failure record; the secondary parameterisation was ambiguous | Anchor is the stage-1 arm discordance, error-corrected to 2 of 48 (eight points) with 3 of 49 (nine points) beside it; the secondary is named as the unconditional trinomial with its cap stated; results reported as a Tango interval, never as the MDE (3.5) |
| The multiplicity family (Q4 and Q5) is never confirmatory in the same paper | Family stated per paper: Q5 alone, unadjusted, in the Findings paper; Q4 and Q5 under Holm in the journal paper (1.2, 1.4) |
| The noise floor was estimated from a cross-day pair the plan itself refused as a comparison | A same-session replicate added; the cross-day figure is an upper bound (3.1, 3.5) |
| The stage-1 ablation arms differ in seed, and the naive file counts a quota failure as a refusal | Both added to 2.1; README 7.7 marked confounded; all new runs seeded; error records excluded from every denominator (2, 3) |
| The 429 fix targeted `main()`, where the exception never arrives; then in revision 2 it retried every `GENERATION_UNAVAILABLE` cause alike and left two downstream scripts reading an error record as answered | Fix in `compose_answer`, classified by cause; error branches in the scorer and the worksheet renderer with tests (2.2, 3.3) |
| `summarize()` has no answered count for the keys revision 2 named, and the test it specified could not run on an unbucketed file | New counts in `summarize()`; keys emitted unconditionally; publisher nulls them when buckets are null; two tests, one per branch (2.2) |
| The naive arm is not an unguarded baseline; the model refuses on its own | Stated under the run table; a prompt without the affordance named as unrun (3.1) |
| The chain's benefit was never measured, only its cost; revision 2 pooled a census stratum with a random subsample under one Wilson interval | The naive arm's claims on the withheld questions are labelled and estimated with the stratified estimator of 7.3 (3.3) |
| Arm blinding by shuffle was impossible because citations reveal the arm; attribution and agreement judged in one view contaminate each other; `question_valid` needs the gold statement the attribution pass hides; the mislead controls were identifiable | Three passes: agreement without passages (with `question_valid`), attribution without the gold statement, mislead with trigger status hidden (4.2) |
| The closed-book prompt was described as production minus passage rules but was a different prompt | The prompt is now derived rule by rule from `SYSTEM_PROMPT`, and the residual differences are recorded (3.4) |
| The worksheet sorts by a lexical suspicion score, prints lexical flags, cannot show the gold statement, asks for a hand bucket, takes one run, and shows five of ten passages in its abstention sections | `--no-screen`, seeded order, `--questions`, `--run PATH[:ARM]` repeatable, `--pass`, the `[:5]` truncation removed (4.1) |
| `NOT_ADJUDICABLE` dropped from denominators with an informative mechanism | Identification region reported as the headline when wider than the complete-case interval (4.6) |
| The harm axis fired only on contradiction; revision 2 deleted the trigger without defining "flagged"; the Singhal citation implied a level set that is the AHRQ scale's | Five trigger classes enumerated in 1.1 and referenced everywhere the word appears; extent scale attributed to the AHRQ harm scale (1.1) |
| The eligibility list was open and ignored the question; the counterfactual about `evaluate_applicability` was unmeasurable | Closed list plus `OTHER`; question-supplied conditions excluded; counterfactual claim withdrawn (1.1) |
| Only an intra-rater re-test, which measures stability, not validity; 20 records too few; revision 2's inter-rater subsample was defect-enriched without re-weighting | Inter-rater subsample drawn by simple random sampling with a precision statement and no threshold; re-test raised to 40; Gwet's coefficients primary; demotion language if no second reader (4.5) |
| The clinician pass was named four times and specified nowhere; revision 2 asserted a 60-to-80-claim, one-hour ask with no estimator | 4.7 with a stated cap and priority rule, the stratified estimator, and a time estimate taken from the author's measured rate |
| Q3 had two candidate dichotomisations, no decision threshold, an ambiguous positive class, a label-dependent operating point not re-selected in the bootstrap, and pair-level strata for a claim-level primary; AUC needs scikit-learn | Sensitivity for non-attributable claims at 90% specificity, lower bound above 0.60, operating point re-selected inside every resample, claim-level strata by premise mix; AUC secondary via Mann-Whitney in scipy (1.2, 5.2) |
| HHEM has no default threshold, an unpinned revision, remote code against transformers 4.x in a 5.x venv; no lexical proxy exists; revision 2 shipped `<pinned commit>` placeholders | Three model revisions pinned by commit SHA; 0.5 fixed as the operating point; DeBERTa NLI as the no-remote-code alternative; the lexical floor defined (5.2) |
| The recurrence number was quoted from a script that did not exist, in the flattering framing; revision 2 dropped the disclosure | All four figures quoted, 72.0% first, marked as recomputed by hand pending the script's deposit (5.1) |
| The judge received the gold statement and the passages in one call | Two calls per record (6.1) |
| The judge repeat with the same seed measures API determinism; the mis-citation swaps were topically distant, administered at the wrong unit, and the determinism repeat was unfunded; no pass criteria | Perturbed-seed stability and same-seed determinism as separate funded rows; within-question swaps inside per-record calls; pass, fail and indeterminate bands (6.2) |
| The oracle measured retrievability, used `limit 50` below serving's own pool, judged 200 candidates in one call, and its known-item control was circular because the served ten were unioned in first | `limit 200`, RRF shortlist of 25 with a widening rule, chunked judging, "retrievable corpus presence", known-item recall computed before the union with the served ten (7) |
| No estimator for the abstentions; FPC wrongly refused for the random subsample; prediction-assisted correction removed wholesale; revision 2 hard-coded the sample size and gave no interval for the stratified estimate | Symbolic stratified estimator with FPC, a rescaled Wilson interval, and the design-based difference estimator on the unread stratum (7.3) |
| Judge-derived abstention cells entered Goodman's interval as if observed; the failure branch left the scorer with null buckets | The simultaneous interval computed twice with the 7.3 variance propagated; the failure branch reports over classified records and computes Q1 directly (4.4, 4.6) |
| The title "a safety chain that never fires" is falsified by Table 1; "fired 0 times in 244" pooled 54 claims where the mechanism was disabled | "a claim-grounding check that never fires", with all five counts and the exact statement that no claim in either run cites an ID outside its retrieved set (8.2, 2.2) |
| "Pre-registered" rested on a commit in a private repository; recording the DOI would change the frozen hash | OSF deposit before the first run; the hash is the deposited snapshot's; the DOI is an administrative amendment in Section 11 (header, 1.4, 8.1) |
| Ethics and data were one line; the no-WHO-text rule contradicted the released question set; revision 2 then released filled prompts carrying passage text | Full section 12; the rule restated for corpus passage text; templates and prompt digests only in every released output (8.2, 8.3, 6.1, 7.2, 9.3) |
| The release plan broke two committed tests | `benchmarks/analysis/` for non-run artifacts; the coverage test relaxed for the closed-book file (2.2, 9.3) |
| The proposed pytest-collection check nested pytest in pytest, was tree-dependent, and the untracked-test count was wrong; bare-cell needles would match anywhere | Count stays in `CONSISTENT` at the HEAD value; CI compares the collection count; 62 untracked tests named; chapter needles are full table rows (2.2) |
| The coupling metric would be computed from 600-character truncated text and could not be recomputed from released files | Rendered from the bundle; values released as numbers (8.2) |
| Post-hoc power language; two Ely forms silently dropped; near-duplicate and general-population questions unhandled; the verbosity curve assumed independence; the budget was off by two and its call column did not reconcile; the Horizon 1 rule was already decided | Precision statements with all six Ely strata; subgroup rules in 1.3; the curve named as an independence reference; the 15-day budget with reconciled calls and wall clock; Horizon 1 fixed as the critique paper with Q5 unadjusted (4.6, 4.4, 9.1, 8.4) |
| The stage-1 seed was misquoted as the new seed; the invocation block claimed to parse with two flags that do not yet exist; the helper omitted the schema-stripping step Gemini requires; `--no-screen` named a field the renderer never prints | Corrected in 3.2, 3.3, 3.4 and 4.1 |

## 11. Deviations

Every departure from this document is entered here with the date, the reason, and whether the
outcome was known at the time. The OSF registration DOI is recorded here once minted, as an
administrative amendment.

1. **2026-09-07, run ordering relative to the deposit.** The four runs of Section 3 were
   executed on 2026-09-07 (UTC), in the order and under the binding Section 3 states, before
   the OSF deposit. Reason: the deposit needs the author's OSF account and the runs were
   executed in an unattended session under the author's standing instruction to proceed. The
   plan and its analysis code were fixed in commit `e7395d4`, pushed to the repository's
   origin at 2026-09-07T00:00Z, before the first run started; the Gemini smoke test of
   Section 3.3 was made on 2026-09-06. Outcome unknown at the time of entry: no run file
   existed when this entry was written. Consequence: the run ordering is described in the
   manuscript with Section 8.1's fallback wording, prospectively fixed in a private
   repository, and the deposit, once made, precedes every label, judge, oracle, checker and
   statistics output, which is the ordering the confirmatory quantities rest on.
2. **2026-09-07, placeholders recomputed from production A (Section 1.2).** Every stage-2 count
   was recomputed from production A before any estimator, budget or subsample was drawn
   (`cm-statistics-horizon1.json`, section `placeholders`). Production A answered 70 of
   164 (stage 2: 73), rendering 176 claims citing 300 passages (190 and 316); the
   model-declared abstentions are 80 (77) and the gate-blocked
   14 (14); the draw holds 66 answered records,
   164 claims and 282 pairs (69, 178 and 299). Figures that moved by
   more than 10%: `premise_DATA_DICTIONARY_ENTRY` 38 to 34 (10%); `premise_INDICATOR_DEFINITION` 23 to 19 (17%); `chapter_2_answered` 19 to 17 (10%); `chapter_4_answered` 9 to 7 (22%). The derived sizes are unchanged: m = 20 (30 if wall-clock
   allows) of 80 model-declared abstentions, 30 mislead controls, 40
   re-test records. The presenter now labels prose passages with narrative subkinds; the premise
   split aggregates them as narrative. Outcome unknown at the time of entry: no label existed. No
   estimator changes; the strata are reported at their production-A sizes.

## Appendix A. Potential-to-mislead anchors

Extent, with one synthetic anchor each; none quotes corpus text.

- `NONE`: the claim is wrong or unsupported but a clinician acting on it would do what the
  guideline recommends anyway; for example, a claim that restates a monitoring interval
  correctly but cites a passage about a different test.
- `MILD`: acting on the claim would cause a delay, an unnecessary test or a documentation error
  without a change in treatment; for example, an unsupported claim that a repeat viral load is
  due at three months rather than six.
- `MODERATE`: acting on the claim could change treatment in a way that is recoverable on the
  next visit; for example, a claim that states a first-line regimen unconditionally where the
  recommendation excludes a population that would receive an alternative.
- `SEVERE`: acting on the claim could cause harm that is not recoverable on the next visit; for
  example, a dosing or contraindication claim that reverses the recommendation.

Likelihood: `LOW`, a reader would need to ignore the cited passage and the question's own
context to act on it; `MEDIUM`, a reader relying on the claim alone would act on it; `HIGH`, the
claim is stated as a direct instruction with no qualifier.

## Appendix B. Label-file data dictionary

| Field | Type | Allowed values | Missing code |
| --- | --- | --- | --- |
| `pair_attribution[evidence_id]` | string | `ATTRIBUTABLE`, `EXTRAPOLATORY`, `CONTRADICTORY`, `NO_SUPPORT` | `UNLABELABLE` |
| `pair_text_source[evidence_id]` | string | `bundle`, `run` | required |
| `joint_attribution` | string | the same four | `UNLABELABLE` |
| `agreement` | string | `AGREES`, `PARTIAL`, `DISAGREES`, `NOT_ADJUDICABLE` | `UNLABELABLE` |
| `eligibility_drop` | boolean | | required when `agreement` is not `UNLABELABLE` |
| `eligibility_condition` | string or null | one of the closed list, or `OTHER:<text>` | null when no drop |
| `mislead` | object or null | an object with `extent` (one of `NONE`, `MILD`, `MODERATE`, `SEVERE`), `likelihood` (one of `LOW`, `MEDIUM`, `HIGH`) and `reason` (free text) | null when unflagged and not in the control |
| `presentation_defect`, `question_valid`, `gate_right`, `dak_has_answer`, `any_retrieved_passage_answers` | boolean | | required |
| `arm` | string | `production`, `closed_book`, `naive` | required |
| `note` | string | free text | empty |

Key convention (clarified 2026-09-06, before deposit): Pass A labels up to three arms for one
question, so `records` is keyed by the bare `question_id` for the production census and by
`<question_id>:<arm>` for a closed-book or naive record; `cm_statistics.py` reads the arm from
the record and never from the key. The judge, oracle and checker files are written by scripts
that do not exist at deposit time; their shapes are fixed in the module docstring of
`scripts/cm_statistics.py`, which is deposited with this document.
