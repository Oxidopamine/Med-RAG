# Architecture baseline

## Authority boundary

The authority boundary is the authenticated source artifact, canonical evidence object, structured clinical semantics, and deterministic safety policy. Model output is never accepted as citation metadata or source truth.

## First executable path

```text
question
  -> deterministic context preview
  -> non-clinical progress events
  -> evidence availability check
  -> abstention (until an approved corpus is configured)
```

This narrow path exercises the public async contract without implying that retrieval or verification exists before it does. Proposed clinical claims never appear in progress events.

## Backend boundaries

- `schemas`: immutable API and domain contracts
- `lifecycle`: version DAG validation and evidence-scope currentness
- `semantics`: formal eligibility/context comparison
- `verification`: deterministic required-check policy and fail-closed gate
- `reasoning`: orchestration only; it cannot override failed verification
- `api`: transport and dependency wiring

PostgreSQL remains the planned canonical store and Qdrant the rebuildable retrieval index. Their local services are defined now so persistence and ingestion can be added without changing domain contracts.

## Safety behavior

Missing, failed, or unresolved required checks all withhold a claim. A missing canonical evidence ID cannot be treated as a citation. Related measurements such as eGFR and creatinine clearance remain distinct unless a separately validated calculation path is introduced.

