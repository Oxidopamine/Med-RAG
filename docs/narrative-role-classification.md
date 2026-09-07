# Deciding evidence roles for narrative guideline prose

Status: decided; measured against 165 known recommendations before deciding
Authored: 2026-08-30
Decided: 2026-08-30
Scope: how `evidence_roles` is assigned on the narrative path, and what the serving-side
role gate counts as recommendation-bearing prose

This is the last blocker between a guideline PDF and a servable narrative release.
`qa_classification._base_roles` assigns roles by *source asset*, matching
`ANNEX_A`/`ANNEX_B`/`ANNEX_C`/`MAIN` and returning an empty role set for anything else, so
every narrative unit quarantines as `CLINICAL_CLASSIFICATION_UNRESOLVED` and QA approves
nothing. That is correct fail-closed behaviour for a classifier never taught this artifact
class, and it is the F2 fix [roadmap.md](roadmap.md) says to make **once**, at QA time,
alongside narrative materialization.

It is also the most expensive decision on this path to get wrong. `evidence_roles` lives
inside `CorpusEvidenceRecord`, so re-deciding it changes every affected evidence digest and
therefore the manifest, the release, the index, and the comparability of every benchmark
report produced against it. That is a re-release, not a patch.

## Measured first, decided second

The rule below was scored against real ground truth before being written down, on the
reasoning that this note's predecessor got its boilerplate rule wrong from first principles
and only measurement caught it.

**Ground truth.** `benchmarks/questions/mvp-coverage-who-hiv-v2.json` holds 165 GRADE-rated
recommendation statements extracted from the *Summary recommendations* section of the WHO
2021 consolidated guidelines, under a documented filter: GRADE-rated, ≥120 characters,
containing a deontic verb, minus headings, bullet fragments and footnotes. The document is
on disk. `NarrativeSourceExtractor` turns it into 1,889 block-grouped units. So a candidate
rule can be scored against a known answer, on a real document, with no network and nothing
signed.

| Measurement | Result |
| --- | --- |
| Known recommendations located inside some extracted unit | **165/165** |
| Of those, inside a unit the rule labels | **165/165** |
| End-to-end recall | **1.000** |
| Share of all units labelled | **43.7%** (825/1,889) |
| Precision inside *Summary recommendations* | **0.828** (72/87) — a floor, see below |

**Recall is the clean measurement. Precision is not.** The guideline states recommendations
in its chapters too, and the frame covers only the summary section, so a labelled unit
outside pages 17–44 is not necessarily wrong. Even inside the section the 0.828 is a floor:
inspecting the 15 unlabelled-by-truth units shows most are **good practice statements**,
which the frame excluded deliberately, and sub-threshold chapter continuation headers. Both
are arguably correct labels the ground truth does not cover.

**The one finding that changed the rule.** A first version scored 0.952, and all eight
misses were the same thing: WHO writes conditional recommendations as *"WHO suggests…"*
rather than *"should"* or *"is recommended"*. That is GRADE's own register — "we recommend"
for strong, "we suggest" for conditional — and omitting it silently discards every
conditional recommendation in the corpus. Adding it took recall to exactly 1.000. **The
deontic vocabulary must cover both GRADE registers**, and that is a property of the
guideline-development method rather than of this publisher, so it should hold across
publishers that use GRADE.

**Context for the 43.7%.** F2 measured the DAK at 90.7% of records carrying
`PRIMARY_SUPPORT`, of which 91% structurally could not bear a recommendation. Prose is a
different regime: fewer than half the units are labelled, and every known recommendation is
among them.

## Decisions

Accepted 2026-08-30.

1. **Roles are decided from the passage, never from the asset.** The narrative path has no
   asset-level signal to abuse — one document is the whole corpus contribution — and F2 is
   the record of what happens when roles are inherited from a filename.
2. **The rule errs toward labelling, and the reason is asymmetric.** A missed recommendation
   is a coverage hole: the reader is told the corpus has nothing. A wrongly labelled methods
   paragraph is a *retrieval* problem, not a rendering one, because what protects the reader
   is claim-level grounding — a claim whose cited passages do not support it is discarded
   whole. The role gate proves completeness *of kind*, never of subject, and was never the
   thing standing between a reader and a wrong answer.
3. **The vocabulary covers both GRADE registers.** Strong ("recommend", "should", "must")
   and conditional ("suggest", "may be offered", "be considered"). Measured, not assumed.
4. **Structural exclusions stay structural.** Front matter, reference lists and units below
   a statement-length floor are excluded by shape, not by reading. A rule that tried to
   decide *what a passage means* would be a relevance judgement, which is the thing D5
   already refuses.
5. **Applicability vocabulary must stop being HIV-specific.** `_POPULATION_PATTERNS` and
   `_CARE_SETTING_PATTERNS` are regexes over HIV terms, so a hypertension guideline gets no
   population and no care setting and therefore no `APPLICABILITY` role — which alone would
   block the role gate even with `PRIMARY_SUPPORT` correct. The narrative branch derives
   applicability from the passage's own population and setting language rather than from a
   fixed disease vocabulary.

## The serving half, which is the same decision

`presentation.py` classifies anything not cell-addressed as `PassageKind.NARRATIVE`, and
`RECOMMENDATION_BEARING_KINDS` admits `NARRATIVE` wholesale. On a prose corpus that makes
every table of contents, methods chapter and reference list recommendation-bearing, so
`INCOMPLETE_EVIDENCE_ROLE_SET` can essentially never fire and the serving-side gate stops
being able to fail closed at all.

The serving classifier is a **view** — re-decidable without touching a digest — so it can
be corrected freely later, unlike the QA-time roles. Both halves land together anyway,
because a QA classifier that labels correctly and a serving gate that counts everything
would leave the gate exactly as vacuous as it is now.

## What this does not settle

- **One document, one publisher, one disease area.** The vocabulary finding should
  generalise across GRADE-using publishers; the structural exclusions may not. Re-check
  against the WHO hypertension guideline before signing anything, per work item 8 of
  [narrative-corpus-composition.md](narrative-corpus-composition.md).
- **Correctness is not measured here.** Recall of 1.000 means every known recommendation
  reaches a labelled unit. It does not mean the answers built from those units are right,
  and no number in this note is clinical validation.
