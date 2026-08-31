"""Publish measurement evidence from the local run store into the repository.

The headline numbers in the README were, until this script existed, backed only by
files under `data/local/`, which is git-ignored. A reader could not check them. This
copies the runs that back a stated result into `benchmarks/results/`, under two rules:

**Sealed retrieval reports are copied verbatim.** They carry `report_sha256` over their
own content and contain no source text -- only case IDs, evidence IDs, ranks, scores and
metrics. Copying them byte-for-byte keeps the digest verifiable, so this script
recomputes it and refuses to publish a report whose digest does not round-trip.

**Coverage runs are redacted before publication.** Each retrieved passage carries
`rendered_text`, which is WHO source content, and the release marks it
`render_allowed: false`. Publishing it would redistribute the corpus, which
`README.md` §12 says this repository does not do. The redaction drops exactly
`rendered_text` and `rendered_text_truncated` and nothing else, so every field the
measurement depends on -- outcome, gate reason, roles, scores, claim text, verification
counts -- survives. Model-composed claim text is kept: it is the system's own output and
the thing being measured, not publisher content.

Run from the repository root:

    python scripts/publish_evidence.py            # publish
    python scripts/publish_evidence.py --check    # verify what is published is current
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.schemas.corpus import canonical_sha256  # noqa: E402

LOCAL = REPO_ROOT / "data" / "local"
BENCH = LOCAL / "benchmark-source-derived"
OUT = REPO_ROOT / "benchmarks" / "results"

# Redacted because every passage carries `rendered_text` (WHO source content,
# render_allowed: false). Mapped to the README claim each one backs.
COVERAGE_RUNS: tuple[tuple[str, str, str], ...] = (
    (
        "mvp-coverage-stage1-run.json",
        "coverage-stage1-unattributed.json",
        ("Stage 1, n=49, 21/49 = 0.4286. Model binding NOT recorded by the runner; "
        "this run cannot be attributed to a lane and is published as the reason the "
        "binding is now mandatory."),
    ),
    (
        "mvp-coverage-stage1-pinned.json",
        "coverage-stage1-gemini-2.5-flash.json",
        "Stage 1 re-run on a pinned lane, 24/49 = 0.4898. The 0.490 in README §7.2.",
    ),
    (
        "mvp-coverage-stage1-gemini-3.7-flash.json",
        "coverage-stage1-gemini-3.7-flash.json",
        ("Stage 1 on gemini-3.7-flash, 20/49 = 0.4082. The 0.408 in README §7.2 and the "
        "production row of the §7.7 ablation."),
    ),
    (
        "mvp-coverage-stage1-naive-baseline-37.json",
        "coverage-stage1-naive-baseline.json",
        ("Same 49 questions with every safety mechanism disabled, 21/49 = 0.4286. The "
        "'every mechanism off' row of the §7.7 ablation."),
    ),
    (
        "mvp-coverage-v2-gemini-3.7-flash-complete.json",
        "coverage-stage2-gemini-3.7-flash.json",
        ("Stage 2 full frame, n=164, 73 answered. Yields 69/149 = 0.4631 on the "
        "pre-registered prefix and 73/164 = 0.4451 as a census. README §7.2."),
    ),
)

# Copied verbatim: no source text, and `report_sha256` must round-trip.
SEALED_REPORTS: tuple[tuple[str, str, str], ...] = (
    (
        "v7-conflict-aware-development-report.json",
        "retrieval-v7-candidate-accepted.json",
        ("The ACCEPTED candidate. Hybrid answerable complete-evidence 0.9647 "
        "(Wilson lower 0.9343), required-role recall 0.9647. README §7.1."),
    ),
    (
        "v7-conflict-aware-runner150.json",
        "retrieval-v7-candidate-accepted-earlier-run.json",
        ("A second ACCEPTED execution of the identical suite eight minutes earlier, with "
        "identical quality metrics and hybrid p95 1,672.7 ms. This is the run the "
        "README's p95 figure comes from."),
    ),
    (
        "v7-deterministic-comparator-report.json",
        "retrieval-v7-comparator-floor.json",
        ("The measured comparator floor: hybrid 0.8275, Wilson 95% [0.7763, 0.8689]. "
        "README §7.1 and D9."),
    ),
    (
        "qwen3-0.6b-pre-rerank-development-report.json",
        "rerank-control-pre-rerank.json",
        ("Pre-rerank control on the 275-case suite: complete-evidence 0.9855, "
        "nDCG 0.8900, MRR 0.8673, recall 0.9927. README §7.4."),
    ),
    (
        "qwen3-0.6b-rerank-pool-20-development-report.json",
        "rerank-pool-20.json",
        "Reranked, pool 20: complete-evidence 0.9673, nDCG 0.6618, MRR 0.5703.",
    ),
    (
        "qwen3-0.6b-rerank-pool-50-development-report.json",
        "rerank-pool-50.json",
        "Reranked, pool 50: complete-evidence 0.9673, nDCG 0.6291, MRR 0.5249.",
    ),
    (
        "qwen3-0.6b-rerank-pool-100-development-report.json",
        "rerank-pool-100.json",
        "Reranked, pool 100: complete-evidence 0.9673, nDCG 0.6234, MRR 0.5162.",
    ),
    (
        "v3-conflict-aware-development-report.json",
        "rerank-len512-control.json",
        ("The control for the 512-token reranker test: answerable nDCG 0.8118, "
        "answerable MRR 0.7641. README §7.4."),
    ),
    (
        "v3-rerank-len512-development-report.json",
        "rerank-len512-candidate.json",
        ("The 512-token reranker: answerable nDCG 0.4391, MRR 0.2914, r-precision "
        "0.1100. Doubling the document budget made ranking worse, which is what "
        "refutes the truncation explanation. README §7.4."),
    ),
)

REDACTED_FIELDS = ("rendered_text", "rendered_text_truncated")


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def redact_coverage(document: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Strip corpus passage text, leaving every measured field intact."""
    removed = 0
    for result in document.get("results", []):
        for passage in (result.get("retrieval") or {}).get("passages", []):
            for field in REDACTED_FIELDS:
                if field in passage:
                    del passage[field]
                    removed += 1
    document["_redaction"] = {
        "removed_fields": list(REDACTED_FIELDS),
        "removed_field_count": removed,
        "reason": (
            "rendered_text is WHO source content carried at render_allowed: false. "
            "Publishing it would redistribute the corpus. Every field the measurement "
            "depends on is retained."
        ),
    }
    return document, removed


def answered_count(document: dict[str, Any]) -> int:
    return sum(
        1 for r in document.get("results", []) if (r.get("generation") or {}).get("claims")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify published files match the local runs instead of writing them",
    )
    arguments = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    written: list[str] = []

    for source_name, published_name, _ in COVERAGE_RUNS:
        source = LOCAL / source_name
        if not source.exists():
            problems.append(f"missing local run: {source}")
            continue
        document = json.loads(source.read_text(encoding="utf-8"))
        before = answered_count(document)
        document, removed = redact_coverage(document)
        after = answered_count(document)
        if before != after:
            problems.append(f"{source_name}: redaction changed answered count")
            continue
        for result in document.get("results", []):
            for passage in (result.get("retrieval") or {}).get("passages", []):
                if any(field in passage for field in REDACTED_FIELDS):
                    problems.append(f"{source_name}: source text survived redaction")
        payload = _dump(document)
        target = OUT / published_name
        if arguments.check:
            if not target.exists() or target.read_text(encoding="utf-8") != payload:
                problems.append(f"stale or missing: {target.relative_to(REPO_ROOT)}")
        else:
            target.write_text(payload, encoding="utf-8", newline="\n")
            written.append(f"{published_name}  (n={len(document['results'])}, "
                           f"answered={after}, {removed} text fields removed)")

    for source_name, published_name, _ in SEALED_REPORTS:
        source = BENCH / source_name
        if not source.exists():
            problems.append(f"missing local report: {source}")
            continue
        raw = source.read_text(encoding="utf-8")
        document = json.loads(raw)
        recomputed = canonical_sha256(document["content"])
        if recomputed != document["report_sha256"]:
            problems.append(f"{source_name}: report_sha256 does not round-trip")
            continue
        target = OUT / published_name
        if arguments.check:
            if not target.exists() or target.read_text(encoding="utf-8") != raw:
                problems.append(f"stale or missing: {target.relative_to(REPO_ROOT)}")
        else:
            target.write_text(raw, encoding="utf-8", newline="\n")
            written.append(f"{published_name}  (digest verified {recomputed[:16]}…)")

    for line in written:
        print("published", line)
    if problems:
        print("\nPROBLEMS:", file=sys.stderr)
        for problem in problems:
            print(" -", problem, file=sys.stderr)
        return 1
    print(f"\n{len(written) or 'all'} artifacts OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
