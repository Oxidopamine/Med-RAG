"""Score every claim against what it cites with a small CPU checker, for the attribution audit.

Section 5.2 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
The shipped grounding check verifies provenance, that every cited ID was retrieved; a
checker verifies support, whether the cited text entails the claim. This script produces
the checker's scores so `cm_statistics.py` can report how far apart those two notions are on
this corpus, with the human labels as the arbiter (a STARD 2015 diagnostic-accuracy study
of an index test against a human reference).

It renders each cited passage in full through `PassagePresenter`, exactly as the model saw
it, and scores every pair (premise = rendered passage, hypothesis = claim text) and every
claim against the concatenation of its cited passages in retrieval order.

Instruments, in order of attempt under `--instrument auto`, each pinned to a revision so
the scoring code and weights are the ones named in the plan:

1. HHEM-2.1-Open, `vectara/hallucination_evaluation_model` at
   `8e4a2e6e96c708cc76c2344f7e4757df2515292c`, loaded with `trust_remote_code=True` and scored
   with `model.predict(pairs)`. This venv runs transformers 5.x and the card's remote code was
   written against 4.x, so HHEM is a candidate, not the plan.
2. `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` at
   `6f5cf0a2b59cabb106aca4c287eed12e357e90eb`, entailment probability as the score, no remote
   code.
3. `lytang/MiniCheck-Flan-T5-Large` at `96eafd01cee2d16cf81aaa2fb226b14f422a37b3`, through
   the `minicheck` package when it is installed.

The floor, always computed: the lexical score, the fraction of the claim's content tokens
(alphabetic, longer than three characters, casefolded, the token rule of
`source_term_overlap`) occurring as substrings of the concatenated cited text.

The model id, the pinned revision, the `transformers` version, which instruments were
attempted and why each failed, and the CPU wall-clock per pair are written into the
output. The output carries scores, IDs and premise kinds and no passage text. The operating
points are fixed in the plan, not here: 0.5 for the flagged rate, and the 90%-specificity
point for Q3, which `cm_statistics.py` estimates by leave-one-record-out.

    python scripts/entailment_audit.py \\
        --run data/local/cm/production-a.json \\
        --bundle data/local/validated-who-smart-hiv-release.json \\
        --labels data/local/cm/labels-author-20260910.json \\
        --output data/local/cm/checker-production-a.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]

INSTRUMENTS: dict[str, dict[str, str]] = {
    "hhem": {
        "model_id": "vectara/hallucination_evaluation_model",
        "revision": "8e4a2e6e96c708cc76c2344f7e4757df2515292c",
        "licence": "Apache-2.0",
    },
    "deberta": {
        "model_id": "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
        "revision": "6f5cf0a2b59cabb106aca4c287eed12e357e90eb",
        "licence": "MIT",
    },
    "minicheck": {
        "model_id": "lytang/MiniCheck-Flan-T5-Large",
        "revision": "96eafd01cee2d16cf81aaa2fb226b14f422a37b3",
        "licence": "MIT",
    },
}
AUTO_ORDER = ("hhem", "deberta", "minicheck")
_TOKEN = re.compile(r"[a-z]+")


class Scorer(Protocol):
    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]: ...


def lexical_score(claim: str, premise: str) -> float | None:
    """The floor: share of the claim's content tokens found in the casefolded premise."""

    tokens = {token for token in _TOKEN.findall(claim.casefold()) if len(token) > 3}
    if not tokens:
        return None
    haystack = premise.casefold()
    return sum(1 for token in tokens if token in haystack) / len(tokens)


def is_prose(kind: str | None) -> bool:
    return bool(kind) and str(kind).upper().startswith("NARRATIVE")


def premise_mix(kinds: Sequence[str | None]) -> str:
    flags = {is_prose(kind) for kind in kinds}
    if flags == {True}:
        return "prose"
    if flags == {False}:
        return "tabular"
    return "mixed"


# --------------------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------------------


class HhemScorer:
    name = "hhem"

    def __init__(self) -> None:
        from transformers import AutoModelForSequenceClassification

        spec = INSTRUMENTS["hhem"]
        self._model = AutoModelForSequenceClassification.from_pretrained(
            spec["model_id"], revision=spec["revision"], trust_remote_code=True
        )

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        scores = self._model.predict(list(pairs))
        return [float(value) for value in scores]


class DebertaNliScorer:
    name = "deberta"

    def __init__(self, *, max_length: int = 512) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        spec = INSTRUMENTS["deberta"]
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(spec["model_id"], revision=spec["revision"])
        self._model = AutoModelForSequenceClassification.from_pretrained(
            spec["model_id"], revision=spec["revision"]
        )
        self._model.eval()
        labels = {str(v).lower(): int(k) for k, v in self._model.config.id2label.items()}
        self._entailment = labels["entailment"]
        self._max_length = max_length
        self.truncated = 0

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        scores: list[float] = []
        for premise, hypothesis in pairs:
            encoded = self._tokenizer(
                premise,
                hypothesis,
                truncation="only_first",
                max_length=self._max_length,
                return_tensors="pt",
                return_overflowing_tokens=False,
            )
            if encoded["input_ids"].shape[1] >= self._max_length:
                self.truncated += 1
            with self._torch.no_grad():
                logits = self._model(**encoded).logits[0]
            probabilities = self._torch.softmax(logits, dim=-1)
            scores.append(float(probabilities[self._entailment]))
        return scores


class MiniCheckScorer:
    name = "minicheck"

    def __init__(self) -> None:
        from minicheck.minicheck import MiniCheck  # type: ignore[import-not-found]

        self._checker = MiniCheck(model_name="flan-t5-large", cache_dir=None)

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        docs = [premise for premise, _ in pairs]
        claims = [hypothesis for _, hypothesis in pairs]
        _, probabilities, _, _ = self._checker.score(docs=docs, claims=claims)
        return [float(value) for value in probabilities]


def build_instrument(name: str) -> tuple[Scorer | None, list[dict[str, Any]]]:
    """Try the instruments in order and record every failure; `lexical` loads nothing."""

    attempts: list[dict[str, Any]] = []
    if name == "lexical":
        return None, attempts
    order = AUTO_ORDER if name == "auto" else (name,)
    builders: dict[str, Callable[[], Scorer]] = {
        "hhem": HhemScorer,
        "deberta": DebertaNliScorer,
        "minicheck": MiniCheckScorer,
    }
    for candidate in order:
        started = time.perf_counter()
        try:
            scorer = builders[candidate]()
        except Exception as error:  # noqa: BLE001 - every failure is recorded, none hidden
            attempts.append(
                {
                    "instrument": candidate,
                    "loaded": False,
                    "error": f"{type(error).__name__}: {str(error)[:300]}",
                    "seconds": round(time.perf_counter() - started, 1),
                }
            )
            continue
        attempts.append(
            {
                "instrument": candidate,
                "loaded": True,
                "seconds": round(time.perf_counter() - started, 1),
            }
        )
        return scorer, attempts
    return None, attempts


# --------------------------------------------------------------------------------------
# Rendering and scoring
# --------------------------------------------------------------------------------------


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


def collect_items(
    run: dict[str, Any], render_full: Callable[[str], str | None]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Every (claim, cited passage) pair and every claim with its concatenated premise."""

    pairs: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    missing = 0
    for record in run["results"]:
        if outcome_of(record) != "ANSWERED":
            continue
        kinds = {
            passage["evidence_id"]: passage.get("kind")
            for passage in (record.get("retrieval") or {}).get("passages") or []
        }
        for index, claim in enumerate(record["generation"].get("claims") or [], start=1):
            cited = list(dict.fromkeys(claim.get("evidence_ids") or []))
            texts: list[str] = []
            cited_kinds: list[str | None] = []
            for evidence_id in cited:
                text = render_full(evidence_id)
                if text is None:
                    missing += 1
                    continue
                texts.append(text)
                cited_kinds.append(kinds.get(evidence_id))
                pairs.append(
                    {
                        "question_id": record["question_id"],
                        "claim_index": index,
                        "evidence_id": evidence_id,
                        "premise_kind": kinds.get(evidence_id),
                        "premise": text,
                        "hypothesis": claim.get("text") or "",
                    }
                )
            if texts:
                claims.append(
                    {
                        "question_id": record["question_id"],
                        "claim_index": index,
                        "premise_mix": premise_mix(cited_kinds),
                        "cited": cited,
                        "premise": "\n\n".join(texts),
                        "hypothesis": claim.get("text") or "",
                    }
                )
    return pairs, claims, missing


def score_items(
    scorer: Scorer | None, items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float]:
    """Attach `score` (checker) and `lexical_score` (floor); drop the text; time the checker."""

    started = time.perf_counter()
    checker_scores: list[float | None]
    if scorer is None or not items:
        checker_scores = [None] * len(items)
    else:
        checker_scores = list(
            scorer.score([(item["premise"], item["hypothesis"]) for item in items])
        )
    elapsed = time.perf_counter() - started
    scored = []
    for item, score in zip(items, checker_scores, strict=True):
        entry = {k: v for k, v in item.items() if k not in {"premise", "hypothesis"}}
        entry["score"] = round(score, 6) if score is not None else None
        lexical = lexical_score(item["hypothesis"], item["premise"])
        entry["lexical_score"] = round(lexical, 4) if lexical is not None else None
        entry["premise_characters"] = len(item["premise"])
        scored.append(entry)
    return scored, elapsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--labels", type=Path, default=None, help="carried for rubric_sha256")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instrument", choices=("auto", *AUTO_ORDER, "lexical"), default="auto")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N claims")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    labels = json.loads(arguments.labels.read_text(encoding="utf-8")) if arguments.labels else None
    render_full = bundle_renderer(arguments.bundle)
    pairs, claims, missing = collect_items(run, render_full)
    if arguments.limit is not None:
        claims = claims[: arguments.limit]
        keep = {(c["question_id"], c["claim_index"]) for c in claims}
        pairs = [p for p in pairs if (p["question_id"], p["claim_index"]) in keep]
    print(f"claims {len(claims)}  pairs {len(pairs)}  passages without bundle text {missing}")

    scorer, attempts = build_instrument(arguments.instrument)
    loaded = next((a["instrument"] for a in attempts if a["loaded"]), None)
    print(f"instrument: {loaded or 'lexical floor only'}")
    for attempt in attempts:
        if not attempt["loaded"]:
            print(f"  {attempt['instrument']} failed: {attempt['error']}")

    scored_pairs, pair_seconds = score_items(scorer, pairs)
    scored_claims, claim_seconds = score_items(scorer, claims)
    import transformers

    output = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": str(arguments.run),
        "run_label": run.get("run_label"),
        "rubric_sha256": (labels or {}).get("rubric_sha256"),
        "bundle_sha256": hashlib.sha256(arguments.bundle.read_bytes()).hexdigest(),
        "instrument": {
            "name": loaded or "lexical",
            **(INSTRUMENTS.get(loaded) or {}),
            "transformers_version": transformers.__version__,
            "attempts": attempts,
            "truncated_premises": getattr(scorer, "truncated", None),
            "operating_points": {
                "flagged_rate": 0.5,
                "q3": "90% specificity, leave-one-record-out, selected in cm_statistics.py",
            },
        },
        "lexical_floor": (
            "fraction of the claim's content tokens (alphabetic, > 3 characters, casefolded) "
            "occurring as substrings of the concatenated cited text"
        ),
        "counts": {
            "pairs": len(scored_pairs),
            "claims": len(scored_claims),
            "passages_without_text": missing,
        },
        "timing": {
            "pair_seconds_total": round(pair_seconds, 2),
            "pair_seconds_mean": round(pair_seconds / len(scored_pairs), 4)
            if scored_pairs
            else None,
            "claim_seconds_total": round(claim_seconds, 2),
            "claim_seconds_mean": round(claim_seconds / len(scored_claims), 4)
            if scored_claims
            else None,
        },
        "pairs": scored_pairs,
        "claims": scored_claims,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"pairs {pair_seconds:.1f}s  claims {claim_seconds:.1f}s")
    print(f"wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
