# Ely generic clinical question types

Input for the MVP coverage question set. **Not benchmark material** — this is not release-bound,
not deterministically generated, and does not enter `../suites/`. See
[docs/mvp-definition.md](../../docs/mvp-definition.md) for how it is used and why the MVP
question set deliberately sits outside the frozen generation policy that governs this directory.

## Source

Ely JW, Osheroff JA, Gorman PN, et al. *A taxonomy of generic clinical questions:
classification study.* BMJ. 2000;321(7258):429-432.
[PMC27459](https://pmc.ncbi.nlm.nih.gov/articles/PMC27459/) ·
[PubMed 10938054](https://pubmed.ncbi.nlm.nih.gov/10938054/)

1,396 questions, collected by observing 152 primary care doctors (103 family doctors in Iowa,
49 primary care doctors in Oregon), classified into a four-level hierarchy whose finest level
holds **64 quaternary categories**.

**Verified status.** The ten types below are reproduced from the paper's published table, whose
caption reads "Generic questions derived from questions by primary care doctors in Iowa and
Oregon and their frequencies". The full 64 are **not** in the article text — the authors place
them in BMJ supplementary material ("A list of the taxonomy of generic clinical questions
appears on the BMJ's website"), which has not been retrieved. Do not cite a full-64 list from
this file.

## The ten published types

| # | Generic type | n | % |
|---|---|---|---|
| 1 | What is the drug of choice for condition x? | 150 | 11% |
| 2 | What is the cause of symptom x? | 115 | 8% |
| 3 | What test is indicated in situation x? | 112 | 8% |
| 4 | What is the dose of drug x? | 94 | 7% |
| 5 | How should I treat condition x (not limited to drug treatment)? | 82 | 6% |
| 6 | How should I manage condition x (not specifying diagnostic or therapeutic)? | 67 | 5% |
| 7 | What is the cause of physical finding x? | 67 | 5% |
| 8 | What is the cause of test finding x? | 64 | 5% |
| 9 | Can drug x cause (adverse) finding y? | 59 | 4% |
| 10 | Could this patient have condition x? | 51 | 4% |

These ten account for roughly **63%** of the 1,396 observed questions, so the tail beyond them
is long and thin. The full 64 are not needed: instantiating the ten covers the bulk of how
primary care clinicians actually ask, and a recommendation that fits none of them is a signal
worth recording rather than a gap to paper over.

## How to use it

Each sampled WHO recommendation is instantiated into one of these forms by filling `x` and `y`
with the clinical situation — **not** by rewording the recommendation. The form comes from the
taxonomy; the content comes from the clinical situation the recommendation addresses; the
recommendation's own distinctive vocabulary stays out of the question.

Types 2, 7, 8, and 10 are diagnostic-cause and differential questions. WHO consolidated
guidelines are largely prevention, testing, treatment, service delivery, and monitoring, so
expect most recommendations to instantiate 1, 3, 4, 5, 6, or 9. **Do not force a fit.** If a
recommendation instantiates none of the ten naturally, record it as `NO_GENERIC_FORM` with the
question written plainly, and count how often that happens — a high rate would mean guideline
recommendations and point-of-care questions are less aligned than this method assumes, which is
itself a finding about the product.

## Applicability limit, stated plainly

Ely's cohort is US primary care in the 1990s. WHO consolidated guidelines target a public health
approach in resource-limited settings, and the clinicians who would use this product are not
Iowa family doctors. The taxonomy is being used for the *shape* of clinical questions, which is
the part that travels; the topic mix and setting do not, and no claim is made that they do.
