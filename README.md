# Guideline Evidence QA

Research-only clinical guideline evidence engine. The system is being built around a strict invariant: a clinical claim is never rendered unless authorized evidence, exact provenance, applicability, counter-evidence retrieval, and every policy-required verification check pass.

> Not authorized for patient care. Do not enter protected health information (PHI).

## Current slice

This first slice establishes:

- typed source, evidence, clinical context, and verification contracts;
- lifecycle DAG and scoped supersession rules;
- deterministic applicability and claim-safety gates;
- asynchronous question APIs with non-clinical progress events;
- an evidence-first Next.js workspace;
- fail-closed abstention while no approved corpus is configured.

The implementation deliberately does not generate medical answers yet.

## Run locally

Requirements: Python 3.10+, Node.js 20+, Docker.

```powershell
Copy-Item .env.example .env
docker compose up -d postgres qdrant
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\apps\api[dev]"
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir apps/api --reload
```

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

See [docs/architecture.md](docs/architecture.md) and [docs/roadmap.md](docs/roadmap.md) for the implemented boundary and next milestones.

