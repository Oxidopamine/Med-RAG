# Qdrant 1.15.4 vs 1.19.x compatibility and relevance benchmark

Answers the open roadmap item: *"perform a controlled Qdrant 1.15.4 versus 1.19.x
compatibility and relevance benchmark; retain the old pin until collection,
filtering, payload, sparse-IDF, and attestation contracts pass unchanged or are
deliberately versioned."*

The benchmark **reports only**. It does not change `QDRANT_PINNED_VERSION`
(`apps/api/app/corpus_steward/qdrant_index.py`) or the image pin in
`compose.yaml`. Both remain 1.15.4.

## Running it

The two throwaway instances live behind a compose profile so they never start
with the default stack, and they carry no volume, so every run begins on empty
storage and genuinely exercises collection creation:

```sh
docker compose --profile qdrant-compat up -d
python benchmarks/qdrant_compat/compat_benchmark.py
docker compose --profile qdrant-compat down
```

| Instance | Port | Role |
| --- | --- | --- |
| `qdrant-compat-baseline` | 6343 | 1.15.4 baseline |
| `qdrant-compat-candidate` | 6344 | 1.19.0 candidate |
| `qdrant` (untouched) | 6333 | serving instance — the runner refuses to target it |

The harness drives the real production code paths — `QdrantIndexService.build`,
`.validate`, and `_smoke_tests` over `QdrantRESTClient` — and reuses the fixture
builders from `apps/api/tests/unit/test_qdrant_indexing.py` so both servers are
fed a byte-identical bundle, vector batch, and candidate manifest. Building the
fixture once matters: `vector_batch()` stamps `generated_at`, so rebuilding it
per target would change `batch_sha256` and make every digest comparison
meaningless.

`QdrantIndexService` is constructed with `expected_qdrant_version=<observed>`
for the contract probes, because the shipped pin refuses 1.19 outright. That
override is a harness device to see past the gate, not a proposed change.

Full machine-readable output: `qdrant-1.15.4-vs-1.19.0-report.json`.

## Result summary

14 of 16 contract areas pass. Two fail, and neither is a scoring change.

| Contract area | Verdict |
| --- | --- |
| Version gate is fail-closed | PASS |
| Collection creation | PASS |
| `config.params` echo (strict `!=` compare) | PASS |
| Collection metadata round-trip | PASS |
| Payload index parity (14 keyword indexes) | PASS |
| Point/payload/vector round-trip | PASS |
| Scroll response shape | PASS |
| Filtering (8 forms) | PASS |
| Dense cosine scoring | PASS |
| Sparse IDF scoring, release collection | PASS |
| Sparse dot-product control (`modifier=none`) | PASS |
| Sparse IDF scoring, skewed-df corpus | PASS |
| Rebuild determinism, distinct scores | PASS |
| **Rebuild determinism, tied sparse scores** | **FAIL** |
| **Rebuild determinism, duplicate dense vectors** | **FAIL** |
| **Signed attestation reconciliation** | **FAIL** |

### Sparse IDF scoring is unchanged

This was the flagged risk — a sparse-IDF scoring difference would invalidate
existing sealed vector batches. It did not materialise. Every score is
bit-identical between 1.15.4 and 1.19.0, including IDF-weighted values on a
purpose-built corpus with a deliberately skewed document-frequency profile
(term 1 in all 12 docs, terms 2/5 in half, term 3 in a quarter, terms 4/6/8
hapax):

```
idf/broad  1.15.4: (1, 5.5341425) (4, 2.6210809) (8, 2.1027894) (3, 1.2159586)
idf/broad  1.19.0: (1, 5.5341425) (4, 2.6210809) (8, 2.1027894) (3, 1.2159586)
```

The `modifier=none` control arm isolates this: it also matches, so neither the
IDF weighting nor plain sparse dot-product evaluation changed.

### What actually fails: tie-break reproducibility

1.19.0 does not reproduce a stable result order when scores are **exactly
tied**. Over 5 rebuilds of identical data:

| Case | 1.15.4 distinct orders | 1.19.0 distinct orders |
| --- | --- | --- |
| Sparse, distinct scores | 1 | 1 |
| Sparse, 12-way tie | 1 | 5 |
| Dense, 4 duplicate vectors | 1 | 5 |

Scores stay identical in every trial; only the permutation among equal scores
moves. Ordering remains stable whenever scores differ.

This is not cosmetic here. `_smoke_tests` seals `dense_rank` and `sparse_rank`
into `RetrievalSmokeReport` → `IndexValidationReportContent` → `report_sha256` →
the signed attestation. A rank is order-derived, so any tie at the result
boundary makes a sealed report non-reproducible on 1.19 — `validate` would flap
between runs over identical data. The duplicate-dense-vector case is the
realistic trigger: duplicated evidence text produces identical embeddings, and
both then score 1.0 against a self-retrieval probe.

### Attestation reconciliation

An attestation sealed and signed on the baseline still verifies as a stored
object on 1.19 — it is self-contained. But re-validating the same live
collection against 1.19 does not reproduce the digest that signature covers.
The differing report fields are exactly `qdrant_version` and `validated_at`.

`qdrant_version` is inside `IndexValidationReportContent`, hence inside
`report_sha256`. Any version change therefore invalidates every existing signed
index attestation *by construction*. That is the contract working as designed,
not a defect — but it means an upgrade requires a deliberate re-index and
re-attestation of every release, which is the "or are deliberately versioned"
branch of the roadmap item.
