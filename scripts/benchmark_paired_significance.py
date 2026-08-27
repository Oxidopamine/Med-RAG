import json
from math import comb

ROOT = "C:/Users/arsal/Projects/Med-RAG/"
BENCH = ROOT + "data/local/benchmark-source-derived/"

suite = json.load(
    open(ROOT + "benchmarks/suites/who-smart-hiv-source-derived-development-v7.json")
)["content"]
meta = {
    c["case_id"]: (
        (c["safety_topics"][0] if c["safety_topics"] else None),
        c.get("evidence_expectation"),
    )
    for c in suite["cases"]
}


def bucket(name, mode):
    rows = json.load(open(BENCH + name))["content"]["case_results"]
    return {r["case_id"]: r for r in rows if r["mode"] == mode}


hit = lambda r: r["metrics"]["complete_evidence_set_recalled"]

bm25 = bucket("v7-naive-bm25-floor-report.json", "sparse")
qwen = bucket("v7-conflict-aware-development-report.json", "hybrid")
expo = bucket("v7-bm25-expansion-only-report.json", "hybrid")


def exact_mcnemar(a, b, ids):
    """Two-sided exact binomial test on discordant pairs."""
    b_only = sum(1 for c in ids if hit(a[c]) and not hit(b[c]))
    a_only = sum(1 for c in ids if hit(b[c]) and not hit(a[c]))
    n = b_only + a_only
    if n == 0:
        return b_only, a_only, 1.0
    k = min(b_only, a_only)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2**n)
    return b_only, a_only, min(1.0, 2 * tail)


answerable = [c for c, (_, e) in meta.items() if e == "ANSWERABLE"]
para = [c for c, (t, e) in meta.items() if t == "PARAPHRASED_INTENT" and e == "ANSWERABLE"]
conf = [c for c, (t, e) in meta.items() if t == "CONFLICTING_EVIDENCE" and e == "ANSWERABLE"]

comparisons = [
    ("expansion-only vs Qwen candidate", expo, qwen, answerable),
    ("expansion-only vs BM25 alone", expo, bm25, answerable),
    ("Qwen candidate vs BM25 alone", qwen, bm25, answerable),
    ("  [PARAPHRASED] expo vs Qwen", expo, qwen, para),
    ("  [PARAPHRASED] Qwen vs BM25", qwen, bm25, para),
    ("  [CONFLICT] expo vs BM25", expo, bm25, conf),
]
print(f"{'comparison':<36}{'wins':>6}{'losses':>8}{'p (exact)':>12}")
for label, first, second, ids in comparisons:
    wins, losses, p = exact_mcnemar(first, second, ids)
    flag = "" if p >= 0.05 else "  *"
    print(f"{label:<36}{wins:>6}{losses:>8}{p:>12.4f}{flag}")
print()
print("wins = first system correct where second is wrong; losses = the reverse")
print("* marks p < 0.05 (two-sided exact McNemar on discordant pairs)")
