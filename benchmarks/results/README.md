# Measurement evidence

The run outputs behind the numbers stated in the root [README](../../README.md). Every file
here is produced by [`scripts/publish_evidence.py`](../../scripts/publish_evidence.py) from the
local run store, and `python scripts/publish_evidence.py --check` fails if any of them has
drifted from the run it was published from.

Two classes of file, under two different rules.

## Sealed retrieval reports — published verbatim

These carry `report_sha256` over their own `content` and contain **no source text**: case IDs,
evidence IDs, ranks, scores, and metrics only. They are copied byte-for-byte so the digest stays
verifiable, and the publisher refuses to copy a report whose digest does not round-trip.

Verify any of them:

```python
import json, sys
sys.path.insert(0, "apps/api")
from app.schemas.corpus import canonical_sha256

report = json.load(open("benchmarks/results/retrieval-v7-candidate-accepted.json"))
assert canonical_sha256(report["content"]) == report["report_sha256"]
```

| File | What it backs |
|---|---|
| `retrieval-v7-candidate-accepted.json` | The **ACCEPTED** candidate. Hybrid answerable complete-evidence **0.9647** (Wilson lower 0.9343), required-role recall 0.9647, zero leakage, zero failures. README §7.1 |
| `retrieval-v7-candidate-accepted-earlier-run.json` | A second ACCEPTED execution of the identical suite eight minutes earlier. Identical quality metrics; hybrid p95 **1,672.7 ms**, which is the p95 the README quotes. Published so the two runs are visible rather than merged |
| `retrieval-v7-comparator-floor.json` | The measured comparator floor: hybrid **0.8275**, Wilson 95% [0.7763, 0.8689]. README §7.1 and D9 |
| `rerank-control-pre-rerank.json` | Reranker control, 275-case suite: complete-evidence **0.9855**, nDCG **0.8900**, MRR **0.8673**, recall 0.9927, dense-lane recall 0.9291. README §7.4 |
| `rerank-pool-20.json` | complete-evidence 0.9673, nDCG 0.6618, MRR 0.5703 |
| `rerank-pool-50.json` | complete-evidence 0.9673, nDCG 0.6291, MRR 0.5249 |
| `rerank-pool-100.json` | complete-evidence 0.9673, nDCG 0.6234, MRR 0.5162 |
| `rerank-len512-control.json` | Control for the 512-token test: answerable nDCG **0.8118**, answerable MRR **0.7641** |
| `rerank-len512-candidate.json` | The 512-token reranker: answerable nDCG **0.4391**, MRR **0.2914**, r-precision **0.1100**. Doubling the document budget made ranking worse, which is what refutes the truncation explanation. README §7.4 |

Every reranker report carries `"outcome": "REJECTED"`, including the pre-rerank control — the
control is rejected against the *acceptance policy in force at the time*, not against the
reranked arms. Read the mode summaries, not the outcome field, when comparing them.

## Coverage runs — published redacted

Each retrieved passage in a coverage run carries `rendered_text`, which is WHO source content
that the release marks `render_allowed: false`. Publishing it would redistribute the corpus.
The redaction removes exactly `rendered_text` and `rendered_text_truncated` and nothing else;
each file records what was removed under `_redaction`. Outcome, gate reason, evidence IDs,
roles, qualified roles, fused scores, duplicate suppression, latency, model binding, claim text
and verification counts all survive, so every published number recomputes from these files.

Model-composed **claim text is kept**: it is the system's own output and the thing being
measured, not publisher content.

| File | n | Answered | What it backs |
|---|---:|---:|---|
| `coverage-stage1-unattributed.json` | 49 | 21 | The first stage-1 run, 0.4286. **Model binding was not recorded**, so this run cannot be attributed to a lane. Published as the reason binding is now mandatory |
| `coverage-stage1-gemini-2.5-flash.json` | 49 | 24 | 0.4898 — the **0.490** in README §7.2's lane comparison |
| `coverage-stage1-gemini-3.7-flash.json` | 49 | 20 | 0.4082 — the **0.408** in §7.2, and the production row of the §7.7 ablation |
| `coverage-stage1-naive-baseline.json` | 49 | 21 | Same 49 questions, **every safety mechanism disabled**: 0.4286. The "every mechanism off" row of §7.7 |
| `coverage-stage2-gemini-3.7-flash.json` | 164 | 73 | Stage 2, full frame. Yields **69/149 = 0.4631** on the pre-registered prefix and **73/164 = 0.4451** as a census. README §7.2 |

### Recomputing the headline coverage figure

The pre-registered prefix is marked per item in
[`../questions/mvp-coverage-who-hiv-v2.json`](../questions/mvp-coverage-who-hiv-v2.json) as
`in_preregistered_150`:

```python
import json

questions = json.load(open("benchmarks/questions/mvp-coverage-who-hiv-v2.json"))
run = json.load(open("benchmarks/results/coverage-stage2-gemini-3.7-flash.json"))

prefix = {q["question_id"] for q in questions["items"] if q["in_preregistered_150"]}
scored = [r for r in run["results"] if r["question_id"] in prefix]
answered = [r for r in scored if (r.get("generation") or {}).get("claims")]

print(len(answered), "/", len(scored))   # 69 / 149
```

The `generation` field distinguishes the three outcomes, and the guard above is not
defensive padding — it is load-bearing. Across the full frame of 164: **73** records carry a
`generation` object with `claims` (answered), **77** carry one without (the model declared the
passages insufficient), and **14** have `generation: null` (blocked at the role gate before any
model call, with the reason in `gate_reason`). Indexing `["claims"]` directly raises on 91 of
the 164.

`bucket` is `null` on every record in every run. That is not an omission in the publication —
it is limitation **L4**: no question has been classified into the correctness buckets, so these
files record that the system *rendered claims*, never that it answered *correctly*.

## What is not here

- **The sealed holdout suite and any run against it.** It is a one-shot, custody-controlled
  resource and it remains unspent (D10).
- **Vector batches and release bundles.** Hundreds of megabytes, and they carry corpus content.
- **Runs that back no stated claim.** Several exploratory coverage and retrieval runs exist
  locally; publishing the ones a number depends on is the point, and publishing the rest would
  bury them.
