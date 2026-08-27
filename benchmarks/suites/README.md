# Retrieval benchmark suites

`synthetic-v1.content.json` is the editable input and `synthetic-v1.json` is its sealed,
digest-bound form. It exercises the runner against `data/fixtures/corpus-release-v1.json`
but is not evidence for choosing a clinical retrieval model.

Seal a synthetic/manual suite with:

```powershell
corpus-steward benchmark-seal-suite benchmarks\suites\candidate.content.json `
  --output benchmarks\suites\candidate.json
```

The sealed contract binds the exact release manifest, cases, graded gold evidence,
minimum complete evidence sets, required roles, retrieval filters, forbidden evidence,
ablations, top-K/weighted-RRF parameters, candidate configuration, candidate mode, and
overall plus safety-stratum acceptance thresholds. Version 1.3 adds
`AUTOMATED_SOURCE_DERIVED` provenance, canonical source-evidence digests, deterministic
generation-policy and record bindings, source-version/lifecycle filters, and candidate-neutral
suite construction. The final candidate is bound in the execution ledger and report, not while
cases are generated.

The checked-in synthetic suite is bound to
`models/configs/synthetic-candidate-v1.json`. It remains plumbing coverage and must never
be used as a clinical acceptance holdout.

`who-smart-hiv-source-derived-development-v7.json` contains 430 deterministic development
cases from the validated WHO SMART HIV release. Its holdout sibling is stored only in the
custody-controlled immutable registry and was deliberately not exported here.

Sample mass is deliberately unequal across strata. The five verbatim-fragment topics carry 15
cases each because they measure near-duplicate lookup and separate almost no systems; the
topics that do separate systems - `TERMINOLOGY`, `CONFLICTING_EVIDENCE`, and
`PARAPHRASED_INTENT` - carry 60 each.

Eleven of the twelve safety topics build their query from a normalized source fragment, so the
question repeats the target passage almost verbatim; measured query-term coverage by the gold
passage is 1.000 for `APPLICABILITY`, `CONTRAINDICATION`, `DOSE`, `MONITORING`, and `NEGATION`.
Those strata therefore measure near-duplicate lookup and reward exact lexical matching. The
`PARAPHRASED_INTENT` stratum exists to counter that: its queries are built by controlled
vocabulary substitution, drop every source identifier, and are accepted only when at most
`MAX_PARAPHRASE_LEXICAL_OVERLAP` of their terms appear in the passage and the passage's
IDF-weighted lexical rank in the release falls inside
`[MIN_PARAPHRASE_LEXICAL_RANK, MAX_PARAPHRASE_LEXICAL_RANK]` - findable, but never already
first. Measured coverage for that stratum is 0.411. The band is deliberately wider than the
retrieval depth, so roughly half of its gold records sit outside the top `top_k` under a purely
lexical ranking; a band that fits inside the retrieval depth leaves recall structurally unable
to discriminate and moves only precision-like metrics. It is deliberately not a
clinician-authored paraphrase and must not be described as one.

The rank band matters. An earlier revision of this rule required the source to be the single
best lexical match, which selects precisely the lexically unambiguous records and produced the
*easiest* stratum in the suite rather than a discriminating one. A stratum meant to expose a
weak semantic lane must not be gated on a lexical criterion that guarantees lexical success.

For candidate-pool ablations, `benchmark-derive-development-suite` re-seals only this
automated development suite with the candidate's output depth, pool limit, RRF constant, and
lane weights. It preserves every case and acceptance gate, pins the candidate digest, and
rejects sealed-holdout input. `--register` records the derived suite, immutable development
parent, candidate, access policy, and artifact in the development-derivation lineage ledger;
unregistered variants fail closed before execution.

`benchmark-generate-source-derived` verifies the canonical release, frozen access/threshold/
generation policies, secret partition-seed digest, safety-topic sample targets, and disjoint
source evidence before registering both suites. Development execution is repeatable and
audited. A sealed-holdout suite can be executed exactly once, by its custodian identity, after
the final candidate is sealed; the execution claim permanently binds that candidate digest and
vector batch even if the run fails.
