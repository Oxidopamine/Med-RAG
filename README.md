# Guideline Evidence QA

Research-only clinical guideline evidence engine. The system is being built around a strict invariant: a clinical claim is never rendered unless authorized evidence, exact provenance, applicability, counter-evidence retrieval, and every policy-required verification check pass.

> Not authorized for patient care. Do not enter protected health information (PHI).

## Current slice

The foundation and authenticated-ingestion slices establish:

- typed source, evidence, clinical context, and verification contracts;
- conflict-safe lifecycle DAG and scoped supersession rules;
- tri-state applicability and evidence-bound claim-safety gates;
- executable synthetic safety fixtures;
- asynchronous question APIs with non-clinical progress events;
- an evidence-first Next.js workspace;
- a persistent publisher/source/version registry with PostgreSQL migrations;
- API-key-protected ingestion and publisher-domain URL enforcement;
- immutable, SHA-256-addressed PDF acquisition;
- PyMuPDF page/span/bounding-box extraction with explicit trust records;
- a fail-closed quarantine and audit workflow;
- versioned corpus release, evidence, locator, verification, inventory, exception, and
  attestation contracts;
- a frozen synthetic release bundle and checked-in JSON Schema for parallel clients;
- immutable canonical release/evidence records, a singleton active-release pointer, and
  transactional outbox events;
- PostgreSQL trust roots, signing keys, idempotent reconciliation attempts, and resumable
  stage ledgers;
- a pagination/conditional-fetch connector SDK, deterministic synthetic connector, and
  official WHO SMART FHIR package connector;
- immutable raw inventory/source artifacts, complete-inventory gates, signed exceptions,
  and Ed25519-verified stage/activation attestations;
- immutable trust-root revisions and a bounded native FHIR package processor;
- complete structured-resource inventories with narrative, lifecycle, license, dependency,
  and controlling-authority gates plus signed structured reports;
- a separate `corpus-steward` process entry point for build-side validation and
  reconciliation;
- fail-closed abstention while no approved corpus is configured.

The implementation deliberately does not generate medical answers yet.

## Run locally

Requirements: Python 3.10+, Node.js 20+, Docker.

```powershell
Copy-Item .env.example .env
docker compose up -d postgres qdrant
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\apps\api[dev]"
.\.venv\Scripts\python -m alembic -c apps\api\alembic.ini upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir apps/api --reload
```

Validate the frozen build/serve seam independently:

```powershell
.\.venv\Scripts\python -m app.corpus_steward.cli validate-bundle `
  data/fixtures/corpus-release-v1.json --require-activatable
```

Exercise a local end-to-end synthetic reconciliation (do not commit the generated private
key):

```powershell
New-Item -ItemType Directory -Force data\local\keys | Out-Null
corpus-steward keygen `
  --private-key data\local\keys\stage-private.pem `
  --public-key data\local\keys\stage-public.pem
corpus-steward register-key `
  --public-key data\local\keys\stage-public.pem `
  --key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --purpose STAGE
corpus-steward register-trust-root data\fixtures\trust-root-synthetic.json
corpus-steward reconcile SYNTHETIC `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward
```

The checked-in real connector definition is
`data/trust-roots/who-smart-hiv.json`. Register it and reconcile `WHO_SMART_HIV` with the
same command shape when network acquisition is intended.

Then resolve its immutable narrative/dependency inputs and process the returned candidate
without reacquiring its source artifact:

```powershell
corpus-steward resolve-structured-inputs RC_<candidate-id> `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward
corpus-steward process-structured RC_<candidate-id> `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward
corpus-steward materialize RC_<candidate-id> `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward
corpus-steward qa CRC_<corpus-candidate-id> `
  --signing-key data\local\keys\stage-private.pem `
  --signing-key-id local-steward-stage `
  --signer-identity local-corpus-steward `
  --bundle-output data\local\validated-corpus-release.json
```

Input-resolution exit status 6 and structured-processing exit status 5 are recorded,
signed policy blocks, not parser crashes. Materialization exit status 7 records a
licensing or evidence-coverage block. Successful materialization produces a signed QA
candidate with retrieval still disabled.
QA replays every evidence anchor, deterministically classifies every record as approved
or quarantined, signs the complete decision batch, and registers a validated release in
one command. It deliberately leaves its Qdrant collection unbuilt and does not activate
the release. A separate `qdrant-build`,
`qdrant-validate`, and `qdrant-attest` sequence builds from a sealed, model-pinned vector
batch and registers the validated index without activating it. Both
`index-produce-vectors` and `benchmark-run` can select either the deterministic
non-clinical baseline or manifest-selected verified-local candidate adapters. The
allowlist includes Qwen3 Embedding, BGE-M3, and Unicode BM25; `production` remains only as
a bounded CLI compatibility alias for `candidate`.
The benchmark command executes sealed sparse/dense/hybrid ablations and returns exit
status 8 when the declared candidate misses an acceptance gate. Activation also requires
a matching, signed, unexpired sealed-holdout acceptance record. See
[docs/corpus-steward.md](docs/corpus-steward.md) for the complete sequence.

In a second terminal:

```powershell
npm install
npm run dev:web
```

Open `http://localhost:3000`. API docs are available at `http://localhost:8000/docs`.

## Checks

```powershell
.\.venv\Scripts\python -m pytest apps/api/tests
.\.venv\Scripts\python -m ruff check apps/api
npm run typecheck:web
npm run test:web
npm run build:web
```

Frontend unit and browser tests can be run with:

```powershell
npm run test:web
npx playwright install
npm run test:e2e:web
```

See [docs/architecture.md](docs/architecture.md), [docs/frontend.md](docs/frontend.md),
and [docs/roadmap.md](docs/roadmap.md) for the implemented boundaries, frontend
workflow, and next milestones.

The authenticated source workflow is documented in [docs/ingestion.md](docs/ingestion.md).
The corpus release and reconciliation slices are documented in
[docs/corpus-steward.md](docs/corpus-steward.md).
