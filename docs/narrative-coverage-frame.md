# Where coverage questions come from when the corpus is the guideline

Status: decided; not yet executed
Authored: 2026-08-31
Decided: 2026-08-31
Scope: the sampling frame for measuring answerable coverage on a narrative corpus

## The problem, and why the HIV method cannot be reused

[mvp-definition.md](mvp-definition.md) fixes the rule that makes the 0.4631 coverage figure
interpretable: **the corpus must not author the questions.** The 430-case source-derived
suite is what happens when it does — every query is a normalized source fragment, five
strata measure query-term coverage of 1.000 by their own gold passage, and the resulting
0.9647 measures near-duplicate lookup rather than clinical retrieval.

For HIV that rule was satisfiable cheaply, because of a structural accident: **the DAK is
an operationalization of a subset of its parent guideline.** The parent is the superset,
the kit is the subset, so questions drawn from the parent are outside the corpus by
construction. Measured leakage was mean 0.447 against 1.000 on the source-derived strata.

**A narrative corpus removes that accident.** The WHO hypertension guideline *is* the
corpus. Draw questions from its recommendations and every question is a paraphrase of a
passage the corpus contains — the source-derived defect, reproduced at smaller scale, and a
coverage number near 1.0 by construction. There is no parent to retreat to.

## Decision: the frame is a different publisher's guideline on the same condition

Questions are drawn from the **recommendation statements of an independent guideline
covering the same clinical topic**, then instantiated through the same Ely generic question
forms already used for the HIV set. For WHO hypertension the natural counterpart is a
national or society guideline on pharmacological treatment of hypertension in adults.

Why this and not the alternatives:

- **It preserves the property that made 0.4631 mean something.** The questions come from
  outside the corpus, so a passage that answers one is answering a question it did not
  author.
- **It measures the thing a reader cares about.** "Does this corpus answer the questions a
  clinician would bring to this topic" is a better question than "does this corpus contain
  its own sentences", and an independent guideline on the same condition is the closest
  available proxy for the former.
- **Disagreement is signal, not noise.** Where two guidelines differ, the corpus will
  abstain or answer differently, and that is a finding about coverage rather than a defect
  in the frame. It must be recorded per question rather than smoothed away.

## What this costs, stated before it is paid

- **The frame is only readable, not redistributable.** Society guidelines are
  all-rights-reserved. Questions derived from them are our own text and are fine to commit;
  the source statements are not, so the committed question set carries the citation and the
  question, and the `source_statement` field that the HIV set carries verbatim must hold a
  reference rather than the sentence. This is a real difference from
  `mvp-coverage-who-hiv-v2.json` and the builder has to enforce it.
- **One frame per condition.** There is no single parent covering a multi-document corpus,
  so coverage is measured per document against its own counterpart guideline and reported
  per document. A single corpus-wide coverage number would average across conditions that
  have nothing to do with each other.
- **Scope disagreement is a confounder.** Two guidelines on hypertension do not cover
  identical ground; a question the counterpart asks and WHO deliberately does not address is
  a true abstention, not a coverage failure. The pre-registration must classify these
  separately — `OUT_OF_SCOPE_FOR_THIS_PUBLISHER` — or the number understates the corpus.

## What is pre-registered before any question is written

Mirroring the HIV protocol, because its value was that the decision rule existed before the
reading:

1. **Frame definition** — the counterpart guideline, its edition, and the filter applied to
   its recommendation statements, fixed in writing.
2. **Seeded draw**, with the nesting property asserted at runtime rather than assumed.
3. **The lexical-leakage screen**, reported as before. The expectation is *lower* leakage
   than the HIV set's 0.447, because the frame and the corpus share no text at all — only
   clinical vocabulary. A leakage figure near the HIV number would mean the two guidelines
   are closer to paraphrases of each other than assumed, and would itself be a finding.
4. **Outcome classes**, extended by one: answered, abstained-correct, abstained-avoidable,
   answered-wrong, and `OUT_OF_SCOPE_FOR_THIS_PUBLISHER`.
5. **`ANSWERED_WRONG` keeps its rule** — zero observed occurrences at every stage, one
   occurrence stops the expansion. That rule is not weakened for a new corpus.

## What this does not settle

The counterpart guideline has not been chosen, and choosing it is a clinical judgement
about which publisher's hypertension guidance is a fair yardstick for WHO's. That decision
should be recorded here with its reasoning before the frame is built, and it is the one
step in this note that should not be made by whoever happens to be writing the code.
