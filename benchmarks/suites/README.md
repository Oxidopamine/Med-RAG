# Retrieval benchmark suites

`synthetic-v1.content.json` is the editable input and `synthetic-v1.json` is its sealed,
digest-bound form. It exercises the runner against `data/fixtures/corpus-release-v1.json`
but is not evidence for choosing a clinical retrieval model.

Clinical suites must be frozen per corpus release and independently adjudicated. Seal a
suite with:

```powershell
corpus-steward benchmark-seal-suite benchmarks\suites\candidate.content.json `
  --output benchmarks\suites\candidate.json
```

The sealed contract binds the exact release manifest, cases, graded gold evidence,
minimum complete evidence sets, required roles, retrieval filters, forbidden evidence,
ablations, top-K/weighted-RRF parameters, candidate configuration, candidate mode, and
overall plus safety-stratum acceptance thresholds. Version 1.2 also represents explicit
insufficient-evidence cases, binds access/adjudication/threshold-policy revisions, requires
review provenance for clinical cases, and reports agreement plus Wilson 95% intervals for
binomial safety outcomes.

The checked-in synthetic suite is bound to
`models/configs/synthetic-candidate-v1.json`. It remains plumbing coverage and must never
be used as a clinical acceptance holdout.
