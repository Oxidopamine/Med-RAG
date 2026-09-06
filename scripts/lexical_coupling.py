"""Question-to-retrieved-passage lexical coupling, one value per question, as numbers only.

Section 8.2 item 10 of
[docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
Coupling is the fraction of a question's content tokens (the token rule of
`source_term_overlap`: alphabetic, longer than three characters, casefolded, no stemmer)
occurring as substrings of the concatenated rendered text of its retrieved passages. The
text is rendered through `PassagePresenter` from the release bundle and never from the run
file's truncated copy. Tercile boundaries are computed here and written with the values
before any label is viewed; every headline is stratified by them in `cm_statistics.py`.

Coupling is not called leakage, and it is not contamination: whether the guideline is in the
model's pretraining data is what the closed-book arm measures and this metric does not. The
question-to-gold `source_term_overlap` is carried beside it as a covariate, with its
non-reproducibility stated in the question set.

    python scripts/lexical_coupling.py \\
        --run data/local/cm/production-a.json \\
        --bundle data/local/validated-who-smart-hiv-release.json \\
        --output data/local/cm/coupling.json

The output carries question IDs, scores and boundaries and no passage text, so it is
released as `benchmarks/analysis/coupling.json`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import statistics
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

_TOKEN = re.compile(r"[a-z]+")


def content_tokens(text: str) -> set[str]:
    """Alphabetic tokens longer than three characters, casefolded: `source_term_overlap`'s rule."""

    return {token for token in _TOKEN.findall(text.casefold()) if len(token) > 3}


def coupling(question: str, passage_texts: Sequence[str]) -> float | None:
    """Share of the question's content tokens found as substrings of the concatenated text."""

    tokens = content_tokens(question)
    if not tokens:
        return None
    haystack = "\n".join(passage_texts).casefold()
    return sum(1 for token in tokens if token in haystack) / len(tokens)


def tercile_boundaries(values: Sequence[float]) -> list[float]:
    """The two cut points splitting the values into thirds (inclusive-linear quantiles)."""

    if not values:
        return []
    ordered = sorted(values)
    quantiles = statistics.quantiles(ordered, n=3, method="inclusive") if len(ordered) > 1 else []
    return [round(q, 4) for q in quantiles]


def tercile_of(value: float, boundaries: Sequence[float]) -> int:
    if len(boundaries) < 2:
        return 1
    if value <= boundaries[0]:
        return 1
    if value <= boundaries[1]:
        return 2
    return 3


def bundle_renderer(bundle_path: Path) -> Callable[[str], str | None]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.reasoning.presentation import PassagePresenter
    from app.schemas.corpus import CorpusReleaseBundle

    bundle = CorpusReleaseBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    evidence = {record.evidence_id: record for record in bundle.evidence}
    presenter = PassagePresenter.for_release(evidence.values())

    def render(evidence_id: str) -> str | None:
        record = evidence.get(evidence_id)
        return presenter.render(record).text if record is not None else None

    return render


def outcome_of(record: dict[str, Any]) -> str:
    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


def compute(run: dict[str, Any], render_full: Callable[[str], str | None]) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    missing_text = 0
    for record in run["results"]:
        texts = []
        for passage in (record.get("retrieval") or {}).get("passages") or []:
            text = render_full(passage["evidence_id"])
            if text is None:
                missing_text += 1
                continue
            texts.append(text)
        value = coupling(record["question"], texts)
        questions[record["question_id"]] = {
            "coupling": round(value, 4) if value is not None else None,
            "retrieved": len(texts),
            "outcome": outcome_of(record),
            "source_term_overlap": record.get("source_term_overlap"),
            "chapter_no": record.get("chapter_no"),
        }
    values = [q["coupling"] for q in questions.values() if q["coupling"] is not None]
    boundaries = tercile_boundaries(values)
    for entry in questions.values():
        entry["tercile"] = (
            tercile_of(entry["coupling"], boundaries) if entry["coupling"] is not None else None
        )
    by_outcome: dict[str, list[float]] = {}
    for entry in questions.values():
        if entry["coupling"] is not None:
            by_outcome.setdefault(entry["outcome"], []).append(entry["coupling"])
    return {
        "token_rule": "alphabetic tokens longer than three characters, casefolded, substring match",
        "tercile_boundaries": boundaries,
        "questions": questions,
        "passages_without_bundle_text": missing_text,
        "summary": {
            "questions": len(values),
            "mean": round(statistics.fmean(values), 4) if values else None,
            "median": round(statistics.median(values), 4) if values else None,
            "mean_by_outcome": {
                outcome: round(statistics.fmean(items), 4)
                for outcome, items in sorted(by_outcome.items())
            },
            "tercile_sizes": {
                str(t): sum(1 for q in questions.values() if q["tercile"] == t) for t in (1, 2, 3)
            },
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    result = compute(run, bundle_renderer(arguments.bundle))
    result.update(
        {
            "schema_version": 1,
            "written_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "run": str(arguments.run),
            "run_label": run.get("run_label"),
            "bundle_sha256": hashlib.sha256(arguments.bundle.read_bytes()).hexdigest(),
            "note": (
                "Coupling, not leakage and not contamination: computed and written before any "
                "label is viewed; every headline is stratified by these terciles."
            ),
        }
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"questions {result['summary']['questions']}  mean {result['summary']['mean']}  "
        f"terciles {result['tercile_boundaries']}  sizes {result['summary']['tercile_sizes']}"
    )
    if result["passages_without_bundle_text"]:
        print(f"!! {result['passages_without_bundle_text']} passages had no bundle text")
    print(f"wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
