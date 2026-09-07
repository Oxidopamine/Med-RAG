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
SCOPE += [REPO_ROOT / "benchmarks" / "analysis" / "README.md"]
SCOPE += [REPO_ROOT / "benchmarks" / "qdrant_compat" / "README.md"]

# The API test count CI collects at HEAD. Bumped in the same commit that changes it, and
# compared against the live collection by the CI step that passes `--collected-tests`.
# The checker never runs pytest itself: `main()` calls `recomputed_checks()` twice and
# `test_stated_figures.py` runs the checker from inside pytest, so a collection here would
# nest four full collections in every CI run.
API_TEST_COUNT = "645"


def _load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def _answered(run: dict) -> int:
    return sum(1 for r in run["results"] if (r.get("generation") or {}).get("claims"))


def _is_error(record: dict) -> bool:
    """A generation failure is a missing measurement, not an abstention (plan 2.2 item 4)."""

    generation = record.get("generation") or {}
    return "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE"


def _claims(run: dict) -> list[tuple[dict, dict]]:
    """(claim, record) for every rendered claim of an answered record."""

    return [
        (claim, record)
        for record in run["results"]
        for claim in ((record.get("generation") or {}).get("claims") or [])
    ]


def _cites_outside_retrieved_set(claim: dict, record: dict) -> bool:
    retrieved = {p["evidence_id"] for p in (record.get("retrieval") or {}).get("passages", [])}
    return not set(claim.get("evidence_ids") or []).issubset(retrieved)


def _chapter_cells(run: dict) -> dict[int, tuple[str, int, int]]:
    """chapter_no -> (chapter name, answered, total), recomputed from the run."""

    cells: dict[int, list] = {}
    for record in run["results"]:
        entry = cells.setdefault(record["chapter_no"], [record["chapter"], 0, 0])
        entry[2] += 1
        if (record.get("generation") or {}).get("claims"):
            entry[1] += 1
    return {chapter: (name, answered, total) for chapter, (name, answered, total) in cells.items()}


def chapter_rows(stage1: dict, stage2: dict) -> list[str]:
    """The full rows of the README chapter table, ordered by stage-2 share, descending.

    Full rows rather than bare cells: the checker's needle test is a plain substring
    match against the whitespace-collapsed README, and a bare `6/7` would match anywhere.
    """

    first = _chapter_cells(stage1)
    second = _chapter_cells(stage2)
    rows = []
    for chapter, (name, answered, total) in second.items():
        s1_answered, s1_total = first.get(chapter, (name, 0, 0))[1:]
        share = 100.0 * answered / total
        rows.append(
            (
                -share,
                chapter,
                f"| {chapter} — {name} | {s1_answered}/{s1_total} | {answered}/{total} "
                f"| {share:.1f}% |",
            )
        )
    return [row for _, _, row in sorted(rows)]


def _outcome(record: dict) -> str:
    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if _is_error(record):
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


def correctness_run_checks() -> list[tuple[str, str]]:
    """The four runs of the correctness measurement plan, once published (plan Section 9.3).

    The README quotes the paired comparison from the released statistics file, so each needle
    is taken from that file, and the file's 2x2 is recomputed from the released runs so the
    statistics cannot drift from the runs they describe.
    """

    names = ("production-a", "production-b", "naive-164", "closed-book-164")
    paths = {name: RESULTS / f"coverage-{name}.json" for name in names}
    statistics_path = REPO_ROOT / "benchmarks" / "analysis" / "cm-statistics-horizon1.json"
    if not (all(path.exists() for path in paths.values()) and statistics_path.exists()):
        return []
    runs = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items()}
    outcomes = {
        name: {r["question_id"]: _outcome(r) for r in run["results"]}
        for name, run in runs.items()
    }
    answered = {
        name: sum(1 for o in om.values() if o == "ANSWERED") for name, om in outcomes.items()
    }
    paired = [
        q for q in outcomes["production-a"]
        if q in outcomes["naive-164"]
        and outcomes["production-a"][q] != "ERROR"
        and outcomes["naive-164"][q] != "ERROR"
    ]
    production_only = sum(
        1 for q in paired
        if outcomes["production-a"][q] == "ANSWERED" and outcomes["naive-164"][q] != "ANSWERED"
    )
    naive_only = sum(
        1 for q in paired
        if outcomes["production-a"][q] != "ANSWERED" and outcomes["naive-164"][q] == "ANSWERED"
    )
    replicate_pairs = [
        q for q in outcomes["production-a"]
        if q in outcomes["production-b"]
        and outcomes["production-a"][q] != "ERROR"
        and outcomes["production-b"][q] != "ERROR"
    ]
    disagreements = sum(
        1 for q in replicate_pairs
        if (outcomes["production-a"][q] == "ANSWERED")
        != (outcomes["production-b"][q] == "ANSWERED")
    )
    statistics = json.loads(statistics_path.read_text(encoding="utf-8"))["sections"]["3.5"]
    q5 = statistics["q5_production_a_versus_naive"]
    noise = statistics["noise_floor"]["answered_versus_abstained_disagreement"]
    checks: list[tuple[str, str]] = [
        ("production A answered", f"production A answered {answered['production-a']} of 164"),
        ("naive arm answered", f"the naive arm answered {answered['naive-164']} of 164"),
        (
            "closed-book answered",
            f"the closed-book arm answered {answered['closed-book-164']} of 164",
        ),
        ("production B answered", f"production B answered {answered['production-b']} of 164"),
        (
            "Q5 discordance",
            f"{production_only} answered by production only and {naive_only} by the naive arm only",
        ),
        (
            "Q5 Tango interval",
            f"Tango 95% [{q5['tango_95'][0]:.3f}, {q5['tango_95'][1]:.3f}]",
        ),
        ("Q5 exact McNemar", f"exact McNemar p = {q5['mcnemar_exact_p']}"),
        (
            "noise floor",
            f"replicates A and B disagree on {disagreements} of {len(replicate_pairs)}",
        ),
    ]
    # The released statistics file must describe the released runs.
    if q5["table"]["first_only"] != production_only or q5["table"]["second_only"] != naive_only:
        checks.append(("Q5 statistics file", "__STATISTICS_FILE_DISAGREES_WITH_RUNS__"))
    if noise["count"] != disagreements:
        checks.append(("noise floor statistics file", "__STATISTICS_FILE_DISAGREES_WITH_RUNS__"))
    return checks


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

    stage1 = _load("coverage-stage1-gemini-3.7-flash.json")
    naive_run = _load("coverage-stage1-naive-baseline.json")
    production = _answered(stage1)
    naive = _answered(naive_run)
    lane_25 = _answered(_load("coverage-stage1-gemini-2.5-flash.json"))
    naive_errors = sum(1 for r in naive_run["results"] if _is_error(r))
    checks += [
        ("ablation production", f"{production}/49 = 0.408"),
        ("ablation all-off", f"{naive}/49 = 0.429"),
        ("lane comparison", "**0.490 on gemini-2.5-flash against 0.408 on gemini-3.7-flash**"),
        (
            "naive real trials",
            f"{naive} of {len(naive_run['results']) - naive_errors} real trials",
        ),
    ]

    # Every chapter cell in both columns, as full table rows (plan Section 2.2 item 2).
    for index, row in enumerate(chapter_rows(stage1, stage2)):
        checks.append((f"chapter row {index + 1}", row))

    # Claim totals and the grounding predicate's occasion to fire. The count of claims
    # citing an ID outside the record's own retrieved set is recomputed, not assumed zero.
    stage2_claims = _claims(stage2)
    naive_claims = _claims(naive_run)
    outside_stage2 = sum(
        1 for claim, record in stage2_claims if _cites_outside_retrieved_set(claim, record)
    )
    outside_naive = sum(
        1 for claim, record in naive_claims if _cites_outside_retrieved_set(claim, record)
    )
    checks += [
        (
            "stage-2 claims outside retrieved set",
            f"{outside_stage2} of {len(stage2_claims)} with the mechanism live",
        ),
        (
            "naive claims outside retrieved set",
            f"{outside_naive} of {len(naive_claims)} in the naive arm",
        ),
    ]

    # The stage-2-authored questions, against the individually read stage-1 ones.
    authored = {
        q["question_id"]
        for q in questions["items"]
        if q.get("stage") != 1 and q.get("usable", True)
    }
    authored_records = [r for r in stage2["results"] if r["question_id"] in authored]
    authored_answered = sum(
        1 for r in authored_records if (r.get("generation") or {}).get("claims")
    )
    authored_rate = authored_answered / len(authored_records)
    checks.append(
        (
            "stage-2-authored answered",
            f"{authored_answered}/{len(authored_records)} = {authored_rate:.4f}",
        )
    )
    if lane_25 != 24:
        checks.append(("lane 2.5 answered count", f"__EXPECTED_24_GOT_{lane_25}__"))

    checks += correctness_run_checks()

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
        API_TEST_COUNT,
        r"\b(\d+) tests incl\. executable safety fixtures\b",
    ),
    (
        "API test count (stack table)",
        API_TEST_COUNT,
        r"\b(\d+) API tests, including\b",
    ),
    (
        "API test count (badge)",
        API_TEST_COUNT,
        r"API%20tests-(\d+)-",
    ),
    (
        "API test count (repository map)",
        API_TEST_COUNT,
        r"LOC, (\d+) tests\)",
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


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="check stated figures against the artifacts")
    parser.add_argument(
        "--collected-tests",
        type=int,
        default=None,
        help="the count pytest collected; CI passes it so the README's figure is compared",
    )
    arguments = parser.parse_args(argv)

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

    if arguments.collected_tests is not None and str(arguments.collected_tests) != API_TEST_COUNT:
        failures.append(
            f"[collection] API test count: pytest collected {arguments.collected_tests}, the "
            f"documents say {API_TEST_COUNT}; bump API_TEST_COUNT and every document in the "
            "same commit"
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
