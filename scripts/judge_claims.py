"""The judge panel: two Gemini checkpoints label every claim, and the human census judges them.

Section 6 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
Two checkpoints of one vendor's model, `gemini-2.5-flash` primary and `gemini-3.7-flash` as
the sensitivity analysis, form a two-judge same-vendor panel, not a jury. The panel is a
second signal; the human census is the arbiter of every disagreement and is used in every
reported quantity without exception. `cm_statistics.py` reports the panel only if its
agreement with the census clears the pass criteria of Section 6.2.

**Two calls per record, never one.** The attribution call carries the question, each claim
and the full rendered text of each cited passage, and asks for pair attribution, joint
attribution and one sentence of rationale each; the gold statement is not in the prompt.
The agreement call carries the question, each claim and the gold statement, and asks for
guideline agreement and eligibility drop; the passages are not in the prompt. This is the
information the human rubric's steps have, which is what makes the agreement statistic
interpretable.

Checks (`--check`), each written to its own file:

* `primary`: both calls on every answered record of the arm.
* `stability`: seed 20260907 and the claims within each record in a different seeded
  order, on a seeded random 30 records.
* `determinism`: a same-seed identical-input repeat on the same 30 records.
* `no_gold`: the agreement call without the gold statement, on the 30 records; measures how
  much "agreement" is the judge echoing the question.
* `miscitation`: 30 records in which one claim's cited passage text is replaced, from a
  hand-built plan (`--miscitation-plan`), administered inside the per-record attribution
  call; 15 swaps by a passage of the same kind retrieved for a different question, 15 by an
  on-topic passage from this question's own retrieved set that the claim did not cite.

Temperature 0, the seed, the model id and the UTC date are recorded. The output stores the
prompt templates and the sha256 of each filled prompt, never the filled prompt, because a
filled attribution prompt carries WHO passage text.

    python scripts/judge_claims.py --run data/local/cm/production-a.json --arm production \\
        --bundle data/local/validated-who-smart-hiv-release.json \\
        --questions benchmarks/questions/mvp-coverage-who-hiv-v2.json \\
        --judge gemini-2.5-flash --check primary \\
        --output data/local/cm/judge-gemini-2.5-flash-production-primary.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import importlib.util
import json
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]

ARMS = ("production", "closed_book", "naive")
CHECKS = ("primary", "stability", "determinism", "no_gold", "miscitation")
ATTRIBUTION = ("ATTRIBUTABLE", "EXTRAPOLATORY", "CONTRADICTORY", "NO_SUPPORT")
AGREEMENT = ("AGREES", "PARTIAL", "DISAGREES", "NOT_ADJUDICABLE")
DEFAULT_SEED = 20260906
STABILITY_SEED = 20260907
CHECK_RECORDS = 30

ATTRIBUTION_PROMPT = """You audit whether cited guideline passages support the claims that cite \
them. You see a clinical question, numbered claims, and for each claim the full text of every \
passage it cites.

For each claim, label every cited passage on its own (pair attribution) and then the cited \
passages taken together (joint attribution), with one of:
- ATTRIBUTABLE: everything the claim asserts is stated by or follows directly from the passage(s).
- EXTRAPOLATORY: the passage(s) support part of the claim and the rest is not in them.
- CONTRADICTORY: the passage(s) say the opposite of some part of the claim.
- NO_SUPPORT: the passage(s) do not bear on the claim.

Judge whether the passage states or entails each quantity the claim asserts, not whether the \
numeral appears in it: "twice daily" against "every 12 hours" is ATTRIBUTABLE; "1000 copies/mL" \
against a passage with no threshold is at most EXTRAPOLATORY. Give one sentence of rationale per \
label. Use only the passages shown; do not use other knowledge."""

AGREEMENT_PROMPT = """You compare claims from an answer with the recommendation the question was \
drawn from. You see a clinical question, the recommendation statement (the gold statement), \
and numbered claims. You do not see any passages.

For each claim, label its agreement with the gold statement:
- AGREES: the claim agrees with the recommendation.
- PARTIAL: consistent, but omits a condition, population, strength or alternative the \
recommendation states.
- DISAGREES: the claim conflicts with the recommendation.
- NOT_ADJUDICABLE: deciding needs clinical knowledge beyond the two texts.

Also report eligibility_drop: true when the gold statement conditions the recommendation on \
a population or clinical state that neither the claim nor the question supplies, with the \
condition named from: PREGNANCY_OR_BREASTFEEDING, TB_OR_CRYPTOCOCCAL_COINFECTION, \
WEIGHT_OR_AGE_BAND, RENAL_OR_HEPATIC_FUNCTION, PRIOR_ART_EXPOSURE_OR_TREATMENT_LINE, \
CD4_OR_VIRAL_LOAD_THRESHOLD, SETTING_LEVEL_EPIDEMIOLOGY, or OTHER:<condition>. A condition \
the question already states is not a drop. One sentence of rationale per claim."""

NO_GOLD_PROMPT = """You assess claims from an answer to a clinical question. You see the \
question and numbered claims, and nothing else.

For each claim, label how well it answers the question as a guideline would:
- AGREES: the claim is what a current WHO HIV guideline would recommend for this question.
- PARTIAL: broadly consistent, but omits a condition, population, strength or alternative.
- DISAGREES: the claim conflicts with what the guideline would recommend.
- NOT_ADJUDICABLE: deciding needs knowledge you do not have.

Also report eligibility_drop and the condition as in a guideline comparison, or false and \
null. One sentence of rationale per claim."""


class PairVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    label: str
    rationale: str


class ClaimAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    pair_attribution: list[PairVerdict]
    joint_attribution: str
    rationale: str


class AttributionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimAttribution] = Field(max_length=40)


class ClaimAgreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    agreement: str
    eligibility_drop: bool
    eligibility_condition: str | None = None
    rationale: str


class AgreementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimAgreement] = Field(max_length=40)


def _load_by_path(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"medrag_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# Pure pieces: selection, prompts, parsing, the mis-citation swap
# --------------------------------------------------------------------------------------


def outcome_of(record: dict[str, Any]) -> str:
    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


def label_key(question_id: str, arm: str) -> str:
    return question_id if arm == "production" else f"{question_id}:{arm}"


def select_records(
    records: list[dict[str, Any]], *, size: int | None, seed: int
) -> list[dict[str, Any]]:
    """A seeded simple random sample of answered records, in question order."""

    answered = [r for r in records if outcome_of(r) == "ANSWERED"]
    if size is None or size >= len(answered):
        return answered
    ids = sorted(r["question_id"] for r in answered)
    chosen = set(random.Random(seed).sample(ids, size))
    return [r for r in answered if r["question_id"] in chosen]


def shuffled_claims(claims: list[dict[str, Any]], *, seed: int) -> list[tuple[int, dict[str, Any]]]:
    """(original index, claim) in a seeded order; the judge sees new numbering, we keep the map."""

    indexed = list(enumerate(claims, start=1))
    random.Random(seed).shuffle(indexed)
    return indexed


def attribution_user_content(
    question: str,
    claims: list[tuple[int, dict[str, Any]]],
    passage_text: Callable[[str], str | None],
) -> tuple[str, dict[int, int]]:
    """The filled attribution prompt and the map from displayed number to original index."""

    lines = [f"Question:\n{question.strip()}", ""]
    numbering: dict[int, int] = {}
    for shown, (original, claim) in enumerate(claims, start=1):
        numbering[shown] = original
        lines.append(f"Claim {shown}: {claim.get('text', '').strip()}")
        for evidence_id in dict.fromkeys(claim.get("evidence_ids") or []):
            text = passage_text(evidence_id) or "(passage text unavailable)"
            lines.append(f"  [evidence_id: {evidence_id}]")
            lines.append("  " + text.strip().replace("\n", "\n  "))
        lines.append("")
    return "\n".join(lines).strip(), numbering


def agreement_user_content(
    question: str,
    gold: str | None,
    claims: list[tuple[int, dict[str, Any]]],
) -> tuple[str, dict[int, int]]:
    lines = [f"Question:\n{question.strip()}", ""]
    if gold is not None:
        lines += [f"Gold statement:\n{gold.strip()}", ""]
    numbering: dict[int, int] = {}
    for shown, (original, claim) in enumerate(claims, start=1):
        numbering[shown] = original
        lines.append(f"Claim {shown}: {claim.get('text', '').strip()}")
    return "\n".join(lines).strip(), numbering


def parse_attribution(
    payload: dict[str, Any], numbering: dict[int, int], cited: dict[int, list[str]]
) -> dict[int, dict[str, Any]]:
    """Judge output keyed by original claim index; unknown labels are UNLABELABLE."""

    response = AttributionResponse.model_validate(payload)
    out: dict[int, dict[str, Any]] = {}
    for item in response.claims:
        original = numbering.get(item.index)
        if original is None:
            continue
        pairs = {}
        for verdict in item.pair_attribution:
            if verdict.evidence_id in cited.get(original, []):
                pairs[verdict.evidence_id] = (
                    verdict.label if verdict.label in ATTRIBUTION else "UNLABELABLE"
                )
        for evidence_id in cited.get(original, []):
            pairs.setdefault(evidence_id, "UNLABELABLE")
        out[original] = {
            "index": original,
            "pair_attribution": pairs,
            "joint_attribution": (
                item.joint_attribution if item.joint_attribution in ATTRIBUTION else "UNLABELABLE"
            ),
            "rationale": item.rationale,
            "pair_rationales": {v.evidence_id: v.rationale for v in item.pair_attribution},
        }
    return out


def parse_agreement(
    payload: dict[str, Any], numbering: dict[int, int]
) -> dict[int, dict[str, Any]]:
    response = AgreementResponse.model_validate(payload)
    out: dict[int, dict[str, Any]] = {}
    for item in response.claims:
        original = numbering.get(item.index)
        if original is None:
            continue
        out[original] = {
            "index": original,
            "agreement": item.agreement if item.agreement in AGREEMENT else "UNLABELABLE",
            "eligibility_drop": item.eligibility_drop,
            "eligibility_condition": item.eligibility_condition,
            "rationale": item.rationale,
        }
    return out


def swapped_passage_text(
    passage_text: Callable[[str], str | None],
    swap: dict[str, Any] | None,
) -> Callable[[str], str | None]:
    """Replace one cited passage's text with another passage's text, keeping the evidence ID.

    `swap` is one entry of the hand-built mis-citation plan: `evidence_id` (the cited ID
    whose text is replaced) and `replacement_evidence_id` (whose text is shown instead).
    """

    if swap is None:
        return passage_text

    def render(evidence_id: str) -> str | None:
        if evidence_id == swap["evidence_id"]:
            return passage_text(swap["replacement_evidence_id"])
        return passage_text(evidence_id)

    return render


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def bundle_renderer(bundle_path: Path) -> Callable[[str], str | None]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.reasoning.presentation import PassagePresenter
    from app.schemas.corpus import CorpusReleaseBundle

    bundle = CorpusReleaseBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    evidence = {record.evidence_id: record for record in bundle.evidence}
    presenter = PassagePresenter.for_release(evidence.values())
    cache: dict[str, str | None] = {}

    def render(evidence_id: str) -> str | None:
        if evidence_id not in cache:
            record = evidence.get(evidence_id)
            cache[evidence_id] = presenter.render(record).text if record is not None else None
        return cache[evidence_id]

    return render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS, default="production")
    parser.add_argument(
        "--bundle", type=Path, default=None, help="release bundle; needed for attribution"
    )
    parser.add_argument(
        "--questions", type=Path, required=True, help="question set with gold statements"
    )
    parser.add_argument("--judge", required=True, help="gemini-2.5-flash or gemini-3.7-flash")
    parser.add_argument("--check", choices=CHECKS, default="primary")
    parser.add_argument(
        "--seed", type=int, default=None, help="defaults to 20260906, or 20260907 for stability"
    )
    parser.add_argument(
        "--sample-seed", type=int, default=DEFAULT_SEED, help="seed of the 30-record sample"
    )
    parser.add_argument(
        "--records", type=int, default=None, help="record sample size; 30 for the checks"
    )
    parser.add_argument("--miscitation-plan", type=Path, default=None)
    parser.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="label file; for the naive arm, restricts to the abstention-review selection",
    )
    parser.add_argument("--rubric", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    return parser


async def main() -> int:
    arguments = build_parser().parse_args()
    gemini_json = _load_by_path("gemini_json")
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    questions = json.loads(arguments.questions.read_text(encoding="utf-8"))
    gold = {item["question_id"]: item.get("source_statement") for item in questions["items"]}

    seed = arguments.seed
    if seed is None:
        seed = STABILITY_SEED if arguments.check == "stability" else DEFAULT_SEED
    sample_size = arguments.records
    if sample_size is None and arguments.check != "primary":
        sample_size = CHECK_RECORDS
    records = select_records(run["results"], size=sample_size, seed=arguments.sample_seed)
    if arguments.arm == "naive" and arguments.labels is not None:
        labels = json.loads(arguments.labels.read_text(encoding="utf-8"))
        selection = set(labels.get("gate_blocked") or {}) | {
            key for key in (labels.get("abstained_sample") or {}) if key.startswith("MVPQ")
        }
        records = [r for r in records if r["question_id"] in selection]
    if arguments.limit is not None:
        records = records[: arguments.limit]

    plan: dict[str, dict[str, Any]] = {}
    if arguments.check == "miscitation":
        if arguments.miscitation_plan is None:
            raise SystemExit("--check miscitation needs --miscitation-plan")
        entries = json.loads(arguments.miscitation_plan.read_text(encoding="utf-8"))
        plan = {entry["question_id"]: entry for entry in entries}
        records = [r for r in run["results"] if r["question_id"] in plan]

    needs_attribution = arguments.arm != "closed_book" and arguments.check != "no_gold"
    if needs_attribution and arguments.bundle is None:
        raise SystemExit("the attribution call renders passages from the release: pass --bundle")
    passage_text = bundle_renderer(arguments.bundle) if arguments.bundle else (lambda _e: None)

    parameters = gemini_json.parameters_from_environment().model_copy(
        update={"model_id": arguments.judge, "seed": seed, "temperature": 0.0}
    )
    client = gemini_json.GeminiJsonClient(parameters)
    attribution_schema = gemini_json.closed_schema(AttributionResponse)
    agreement_schema = gemini_json.closed_schema(AgreementResponse)
    agreement_system = NO_GOLD_PROMPT if arguments.check == "no_gold" else AGREEMENT_PROMPT

    output: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "judge": arguments.judge,
        "seed": seed,
        "sample_seed": arguments.sample_seed,
        "check": arguments.check,
        "arm": arguments.arm,
        "run": str(arguments.run),
        "run_label": run.get("run_label"),
        "rubric_sha256": (
            hashlib.sha256(arguments.rubric.read_bytes()).hexdigest() if arguments.rubric else None
        ),
        "binding": gemini_json.binding_of(parameters),
        "prompt_templates": {
            "attribution": ATTRIBUTION_PROMPT,
            "agreement": agreement_system,
        },
        "config_sha256": {
            "attribution": client.config_sha256(ATTRIBUTION_PROMPT, attribution_schema),
            "agreement": client.config_sha256(agreement_system, agreement_schema),
        },
        "records": {},
        "prompt_sha256": {},
        "miscitation": {},
        "errors": [],
    }
    print(
        f"judge {arguments.judge}  check {arguments.check}  arm {arguments.arm}  "
        f"records {len(records)}"
    )
    started = time.perf_counter()
    for number, record in enumerate(records, start=1):
        question_id = record["question_id"]
        key = label_key(question_id, arguments.arm)
        claims = list(record["generation"].get("claims") or [])
        ordered = (
            shuffled_claims(claims, seed=seed)
            if arguments.check == "stability"
            else list(enumerate(claims, start=1))
        )
        cited = {
            index: list(dict.fromkeys(claim.get("evidence_ids") or [])) for index, claim in ordered
        }
        entry: dict[str, Any] = {"arm": arguments.arm, "claims": {}}
        digests: dict[str, str] = {}

        if needs_attribution:
            swap = plan.get(question_id)
            content, numbering = attribution_user_content(
                record["question"], ordered, swapped_passage_text(passage_text, swap)
            )
            result = await client.generate_json(
                system_prompt=ATTRIBUTION_PROMPT, user_content=content, schema=attribution_schema
            )
            digests["attribution"] = result["prompt_sha256"]
            if result["error"] is not None:
                output["errors"].append(
                    {
                        "key": key,
                        "call": "attribution",
                        **{k: result[k] for k in ("error", "error_class", "attempts")},
                    }
                )
            else:
                try:
                    for index, verdict in parse_attribution(
                        result["json"], numbering, cited
                    ).items():
                        entry["claims"].setdefault(index, {"index": index}).update(verdict)
                except ValidationError as error:
                    output["errors"].append(
                        {
                            "key": key,
                            "call": "attribution",
                            "error": str(error)[:300],
                            "error_class": "CONTRACT_VALIDATION",
                        }
                    )
            if swap is not None:
                verdict = (
                    entry["claims"]
                    .get(swap["claim_index"], {})
                    .get("pair_attribution", {})
                    .get(swap["evidence_id"])
                )
                output["miscitation"][key] = {
                    "claim_index": swap["claim_index"],
                    "evidence_id": swap["evidence_id"],
                    "replacement_evidence_id": swap["replacement_evidence_id"],
                    "swap_kind": swap["swap_kind"],
                    "verdict": verdict,
                }

        if arguments.check != "miscitation":
            content, numbering = agreement_user_content(
                record["question"],
                None if arguments.check == "no_gold" else gold.get(question_id),
                ordered,
            )
            result = await client.generate_json(
                system_prompt=agreement_system, user_content=content, schema=agreement_schema
            )
            digests["agreement"] = result["prompt_sha256"]
            if result["error"] is not None:
                output["errors"].append(
                    {
                        "key": key,
                        "call": "agreement",
                        **{k: result[k] for k in ("error", "error_class", "attempts")},
                    }
                )
            else:
                try:
                    for index, verdict in parse_agreement(result["json"], numbering).items():
                        entry["claims"].setdefault(index, {"index": index}).update(verdict)
                except ValidationError as error:
                    output["errors"].append(
                        {
                            "key": key,
                            "call": "agreement",
                            "error": str(error)[:300],
                            "error_class": "CONTRACT_VALIDATION",
                        }
                    )

        entry["claims"] = [entry["claims"][index] for index in sorted(entry["claims"])]
        output["records"][key] = entry
        output["prompt_sha256"][key] = digests
        print(f"[{number:>3}/{len(records)}] {key:<28} claims {len(entry['claims'])}")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    output["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    arguments.output.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"errors {len(output['errors'])}  wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
