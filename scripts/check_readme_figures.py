"""Fail if a stated figure stops matching the run that produced it, or disagrees with itself.

The README restates numbers that also live in `docs/`, and both restate numbers that come from
runs under `benchmarks/results/`. Duplication is not the problem; silent divergence is. This
script makes divergence loud, in two directions:

**Recomputed.** Each entry in `RECOMPUTED` derives a value from a published artifact and asserts
the prose spells it exactly that way. If a run is re-executed and a number moves, the documents
that quote it fail until they are updated.

**Consistent.** Each entry in `CONSISTENT` names a quantity and the only spelling it may have.
Every markdown file in scope is scanned for near-miss spellings of that quantity -- a different
value written against the same label -- which is how `benchmarks/qdrant_compat/README.md` came to
claim "14 of 16" while its own table said 13.

    python scripts/check_readme_figures.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "benchmarks" / "results"
QUESTIONS = REPO_ROOT / "benchmarks" / "questions" / "mvp-coverage-who-hiv-v2.json"

SCOPE = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]
SCOPE += [REPO_ROOT / "benchmarks" / "results" / "README.md"]
SCOPE += [REPO_ROOT / "benchmarks" / "qdrant_compat" / "README.md"]


def _load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def _answered(run: dict) -> int:
    return sum(1 for r in run["results"] if (r.get("generation") or {}).get("claims"))


def _hybrid(name: str) -> dict:
    summaries = _load(name)["content"]["mode_summaries"]
    items = summaries.items() if isinstance(summaries, dict) else [
        (m.get("mode"), m) for m in summaries
    ]
    return next(s for mode, s in items if mode == "hybrid")


def recomputed_checks() -> list[tuple[str, str]]:
    """Return (label, string that must appear in README.md)."""
    checks: list[tuple[str, str]] = []

    stage2 = _load("coverage-stage2-gemini-3.7-flash.json")
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    prefix = {q["question_id"] for q in questions["items"] if q["in_preregistered_150"]}
    scored = [r for r in stage2["results"] if r["question_id"] in prefix]
    answered_prefix = sum(1 for r in scored if (r.get("generation") or {}).get("claims"))
    census = _answered(stage2)
    total = len(stage2["results"])
    gate_blocked = sum(1 for r in stage2["results"] if r.get("generation") is None)
    model_refused = total - census - gate_blocked

    checks += [
        ("stage-2 pre-registered", f"{answered_prefix}/{len(scored)} = 0.4631"),
        ("stage-2 census", f"{census}/{total} = 0.4451"),
        ("stage-2 answered row", f"| Answered, at least one claim rendered | {census} |"),
        (
            "stage-2 model-refused count",
            f"model judged the passages insufficient | {model_refused} |",
        ),
        ("stage-2 gate-blocked count", f"blocked at the gate, no model call | {gate_blocked} |"),
    ]

    production = _answered(_load("coverage-stage1-gemini-3.7-flash.json"))
    naive = _answered(_load("coverage-stage1-naive-baseline.json"))
    lane_25 = _answered(_load("coverage-stage1-gemini-2.5-flash.json"))
    checks += [
        ("ablation production", f"{production}/49 = 0.408"),
        ("ablation all-off", f"{naive}/49 = 0.429"),
        ("lane comparison", "**0.490 on gemini-2.5-flash against 0.408 on gemini-3.7-flash**"),
    ]
    if lane_25 != 24:
        checks.append(("lane 2.5 answered count", f"__EXPECTED_24_GOT_{lane_25}__"))

    accepted = _hybrid("retrieval-v7-candidate-accepted.json")
    comparator = _hybrid("retrieval-v7-comparator-floor.json")
    earlier = _hybrid("retrieval-v7-candidate-accepted-earlier-run.json")
    checks += [
        (
            "accepted complete-evidence",
            f"**{accepted['answerable_complete_evidence_set_rate']:.4f}**",
        ),
        ("accepted Wilson lower",
         f"Wilson lower {accepted['answerable_complete_evidence_set_confidence']['lower']:.4f}"),
        ("comparator floor", f"{comparator['answerable_complete_evidence_set_rate']:.4f}"),
        (
            "comparator Wilson",
            f"[{comparator['answerable_complete_evidence_set_confidence']['lower']:.4f},",
        ),
        ("accepted p95", f"{earlier['p95_latency_ms']:,.1f} ms"),
    ]

    control = _hybrid("rerank-control-pre-rerank.json")
    checks += [
        ("rerank control complete-evidence", f"**{control['complete_evidence_set_rate']:.4f}**"),
        ("rerank control nDCG", f"**{control['mean_ndcg_at_k']:.4f}**"),
        ("rerank control MRR", f"**{control['mean_reciprocal_rank']:.4f}**"),
    ]
    len512 = _hybrid("rerank-len512-candidate.json")
    len512_control = _hybrid("rerank-len512-control.json")
    checks += [
        ("len512 nDCG", f"{len512['answerable_mean_ndcg_at_k']:.4f}"),
        ("len512 MRR", f"{len512['answerable_mean_reciprocal_rank']:.4f}"),
        ("len512 r-precision", f"{len512['answerable_mean_r_precision']:.4f}"),
        ("len512 control nDCG", f"**{len512_control['answerable_mean_ndcg_at_k']:.4f}**"),
        ("len512 control MRR", f"**{len512_control['answerable_mean_reciprocal_rank']:.4f}**"),
    ]
    for pool in (20, 50, 100):
        summary = _hybrid(f"rerank-pool-{pool}.json")
        if abs(summary["complete_evidence_set_rate"] - 0.9673) > 5e-5:
            checks.append((f"pool {pool} complete-evidence", "__POOL_RATE_MOVED__"))
    return checks


# (label, the only spelling allowed, regex finding any spelling of the same quantity)
CONSISTENT: tuple[tuple[str, str, str], ...] = (
    (
        "Qdrant contract areas passing",
        "13",
        r"\b(\d+) of 16 contract areas\b",
    ),
    (
        "API test count",
        "499",
        r"\b(\d+) tests incl\. executable safety fixtures\b",
    ),
    (
        "Alembic migration count",
        "20",
        r"\b(\d+) Alembic (?:migrations|revisions)\b",
    ),
    (
        "registry table count",
        "54",
        r"\b(\d+) tables\b",
    ),
    (
        "corpus-steward subcommand count",
        "56",
        r"\b(\d+)\s+(?:`corpus-steward` )?subcommands\b",
    ),
    (
        "approved record count",
        "5,145",
        r"\b(\d[\d,]*) approved records\b",
    ),
    (
        "distinct passage count",
        "3,065",
        r"\b(\d[\d,]*) distinct passages\b",
    ),
)


def main() -> int:
    readme_raw = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    # Prose wraps at column 100, so a quoted figure can straddle a newline.
    readme = re.sub(r"\s+", " ", readme_raw)
    failures: list[str] = []

    for label, needle in recomputed_checks():
        if re.sub(r"\s+", " ", needle) not in readme:
            failures.append(f"[recomputed] {label}: README does not contain {needle!r}")

    for label, expected, pattern in CONSISTENT:
        for path in SCOPE:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(pattern, text):
                if match.group(1) != expected:
                    where = path.relative_to(REPO_ROOT)
                    failures.append(
                        f"[consistency] {label}: {where} says {match.group(1)!r}, "
                        f"every document must say {expected!r} -- {match.group(0)!r}"
                    )

    if failures:
        print("Stated figures disagree with the artifacts or with each other:\n", file=sys.stderr)
        for failure in failures:
            print(" -", failure, file=sys.stderr)
        print(
            "\nEither the prose is stale, or a run was re-executed and every document "
            "quoting it needs updating.",
            file=sys.stderr,
        )
        return 1

    print(f"figures OK: {len(recomputed_checks())} recomputed, "
          f"{len(CONSISTENT)} quantities consistent across {len(SCOPE)} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
