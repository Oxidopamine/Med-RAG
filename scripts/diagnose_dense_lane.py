import json
import re
import statistics

ROOT = "C:/Users/arsal/Projects/Med-RAG/"
BENCH = ROOT + "data/local/benchmark-source-derived/"

suite = json.load(
    open(ROOT + "benchmarks/suites/who-smart-hiv-source-derived-development-v7.json")
)["content"]
cases = {c["case_id"]: c for c in suite["cases"]}
rows = json.load(open(BENCH + "v7-naive-bm25-floor-report.json"))["content"][
    "case_results"
]
dense = {r["case_id"]: r for r in rows if r["mode"] == "dense"}
hit = lambda r: r["metrics"]["complete_evidence_set_recalled"]

# Strip the fixed instruction prefix each generator template prepends, leaving the
# part of the query that actually varies per case.
PREFIX = re.compile(r"^Retrieve [^:]*:\s*")
# An "opaque" token: an identifier-shaped alphanumeric with no lexical meaning.
OPAQUE = re.compile(r"^(HIV\.[A-Z0-9.]+|[A-Z]{1,4}\d[A-Z0-9.\-]*|[A-Z0-9]{2,}\.\d+)$")

answerable = [
    cid
    for cid, c in cases.items()
    if c["evidence_expectation"] == "ANSWERABLE" and c["safety_topics"]
]
by_topic = {}
for cid in answerable:
    by_topic.setdefault(cases[cid]["safety_topics"][0], []).append(cid)

print(f"{'stratum':<24}{'n':>4}{'opaque% of body':>18}{'body words':>12}{'mean gold':>11}{'dense':>8}")
for topic in sorted(by_topic, key=lambda t: -sum(hit(dense[c]) for c in by_topic[t]) / len(by_topic[t])):
    ids = by_topic[topic]
    fracs, lengths, golds = [], [], []
    for cid in ids:
        body = PREFIX.sub("", cases[cid]["question"])
        toks = body.split()
        if toks:
            fracs.append(sum(bool(OPAQUE.match(t)) for t in toks) / len(toks))
            lengths.append(len(toks))
        golds.append(len(cases[cid]["gold_evidence"]))
    d = sum(hit(dense[c]) for c in ids) / len(ids)
    print(
        f"{topic:<24}{len(ids):>4}{statistics.mean(fracs):>17.0%}"
        f"{statistics.mean(lengths):>12.1f}{statistics.mean(golds):>11.2f}{d:>8.3f}"
    )
