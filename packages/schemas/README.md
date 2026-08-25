# Shared corpus contracts

The release-bundle and signed-activation-decision schemas are generated from the frozen
Pydantic contracts in `apps/api/app/schemas/corpus.py`. The Qdrant vector-batch and signed
index-attestation schemas are generated from
`apps/api/app/corpus_steward/index_schemas.py`. Retrieval benchmark suites and reports are
generated from `apps/api/app/corpus_steward/benchmark_schemas.py`. Sealed local model
artifact manifests are generated from `apps/api/app/corpus_steward/model_artifacts.py`.
The checked-in schemas and synthetic fixtures allow the web, API, and steward processes to
develop independently.

Regenerate after an intentional contract change:

```powershell
.\.venv\Scripts\python -m app.corpus_steward.cli export-schema `
  packages/schemas/corpus-release-bundle-1.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-activation-schema `
  packages/schemas/signed-activation-decision-2.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-materialization-schema `
  packages/schemas/corpus-materialization-result-1.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-index-vector-schema `
  packages/schemas/qdrant-vector-batch-1.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-index-attestation-schema `
  packages/schemas/qdrant-index-attestation-1.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-benchmark-suite-schema `
  packages/schemas/retrieval-benchmark-suite-1.2.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-benchmark-report-schema `
  packages/schemas/retrieval-benchmark-report-1.2.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-benchmark-acceptance-schema `
  packages/schemas/signed-benchmark-acceptance-1.2.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-candidate-schema `
  packages/schemas/retrieval-candidate-1.0.0.schema.json
.\.venv\Scripts\python -m app.corpus_steward.cli export-model-artifact-schema `
  packages/schemas/model-artifact-manifest-1.0.0.schema.json
```

Do not change the meaning of an existing version. Additive compatible changes require
a minor version; breaking identity, provenance, lifecycle, or activation changes require
a new major version and compatibility tests for every supported serving client.
