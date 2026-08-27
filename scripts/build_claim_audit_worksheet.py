"""Render a coverage run into a worksheet a human can classify, and nothing more.

`run_mvp_coverage_stage1.py` records what the system did and leaves every `bucket` null.
This turns that file into the document the classification is actually done in: each
answered question beside the claims it rendered, each claim beside the exact passages it
cites, and each abstention beside what retrieval offered it.

It renders. It does not decide - no bucket is filled here, and the deterministic screens
below flag rows for attention without ever asserting that a claim is unsupported. That
separation is the same one `build_mvp_review_worksheet.py` keeps, and it exists because
`ANSWERED_WRONG` is the stop-the-MVP bucket: a screen that guessed it would either
manufacture a stop or, worse, quietly clear one.

## What the screens do and do not catch

Two mechanical checks, chosen because both are decidable without judgement:

* `NUMERAL_NOT_IN_SOURCE` - a number in the claim that appears in none of the passages it
  cites. This is the highest-value screen for a clinical corpus, because a fabricated
  dose, threshold, interval, or age is the failure that makes a wrong answer dangerous
  rather than merely unhelpful. Matching is on standalone numeric tokens, so `6` does not
  match `36`; a number spelled as a word is not caught at all.
* `WIDE_CITATION` - a claim citing four or more passages. Not a defect in itself, but a
  claim assembled from many sources is more likely to assert something no single passage
  supports, which is exactly what the grounding check cannot see: it verifies that the
  cited IDs were retrieved, never that they say what the claim says.

Neither screen is evidence of anything on its own. A flagged claim is a claim to read
first, and an unflagged claim is not thereby correct - the screens cannot read, and
`ANSWERED_WRONG` is overwhelmingly a semantic failure rather than a numeric one.

`SOURCE_TRUNCATED` is a third flag and means something different: the run stored a
shortened copy of that passage, so the numeral screen could not see all of it and its
result there is unreliable in the direction of over-flagging. Re-run with a larger
`--passage-text` to remove it.

Usage:

    python scripts/build_claim_audit_worksheet.py \\
        --run data/local/mvp-coverage-stage1-<model>.json \\
        --output data/local/mvp-coverage-stage1-<model>-worksheet.md \\
        --buckets-template data/local/mvp-coverage-stage1-<model>-buckets.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ANSWERED_BUCKETS = ("ANSWERED_CORRECT", "ANSWERED_DEFECTIVE", "ANSWERED_WRONG")
ABSTAINED_BUCKETS = ("ABSTAINED_CORRECT", "ABSTAINED_AVOIDABLE")

# Four is the threshold rather than three because conflict-bearing answers legitimately
# cite a small handful, and flagging those would bury the genuinely wide ones.
WIDE_CITATION_THRESHOLD = 4

_NUMERAL = re.compile(r"\d+(?:\.\d+)?")


def _standalone_numbers(text: str) -> list[str]:
    """Numbers in `text`, as written. Digits only: a spelled-out number is not caught."""

    return _NUMERAL.findall(text)


def _contains_number(haystack: str, number: str) -> bool:
    """True when `number` occurs in `haystack` as a whole numeric token.

    Guards the boundary in both directions so `6` does not match inside `36` or `6.5`.
    A claim's `1000` still matches a passage's `>1000 copies/mL`, which is the case that
    matters - the surrounding punctuation is not part of the token.
    """

    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", haystack) is not None


def screen_claim(
    claim: dict[str, Any], passages: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Return (flags, missing_numbers) for one claim against the passages it cites."""

    cited_ids = list(dict.fromkeys(claim.get("evidence_ids") or []))
    cited_text = "\n".join(
        (passages.get(evidence_id) or {}).get("rendered_text") or "" for evidence_id in cited_ids
    )
    missing = [
        number
        for number in dict.fromkeys(_standalone_numbers(claim.get("text") or ""))
        if not _contains_number(cited_text, number)
    ]

    flags: list[str] = []
    if missing:
        flags.append("NUMERAL_NOT_IN_SOURCE")
    if len(cited_ids) >= WIDE_CITATION_THRESHOLD:
        flags.append("WIDE_CITATION")
    if any(
        (passages.get(evidence_id) or {}).get("rendered_text_truncated") for evidence_id in cited_ids
    ):
        flags.append("SOURCE_TRUNCATED")
    return flags, missing


def _passage_index(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["evidence_id"]: p for p in record["retrieval"]["passages"]}


def _render_passage(passage: dict[str, Any], *, limit: int) -> list[str]:
    text = (passage.get("rendered_text") or "").strip().replace("\n", "\n      ")
    if len(text) > limit:
        text = text[:limit] + " ..."
    marker = " [TRUNCATED IN RUN]" if passage.get("rendered_text_truncated") else ""
    roles = ",".join(passage.get("qualified_roles") or passage.get("evidence_roles") or [])
    return [
        f"    - `{passage['evidence_id']}` *{passage.get('kind')}* roles=`{roles}`{marker}",
        f"      {text}",
    ]


def render(run: dict[str, Any], *, passage_limit: int) -> tuple[str, dict[str, Any]]:
    answered: list[tuple[int, dict[str, Any]]] = []
    generation_abstained: list[dict[str, Any]] = []
    gate_abstained: list[dict[str, Any]] = []

    for record in run["results"]:
        generation = record.get("generation")
        if generation is None:
            gate_abstained.append(record)
        elif generation.get("abstained"):
            generation_abstained.append(record)
        else:
            passages = _passage_index(record)
            flag_count = sum(
                len(screen_claim(claim, passages)[0]) for claim in generation.get("claims") or []
            )
            answered.append((flag_count, record))

    # Flagged first: the reading order is the whole point of applying the screens here
    # rather than leaving them to the reader.
    answered.sort(key=lambda pair: (-pair[0], pair[1]["question_id"]))

    binding = run.get("generation_binding") or {}
    lines: list[str] = [
        "# Claim audit worksheet",
        "",
        f"- run provider: `{run.get('generation_provider')}`",
        f"- generation binding: `{json.dumps(binding) if binding else 'NOT RECORDED IN THIS RUN'}`",
        f"- registered: `{run.get('registered')}`",
        (
            f"- questions: {len(run['results'])}  "
            f"answered: {len(answered)}  "
            f"abstained at generation: {len(generation_abstained)}  "
            f"abstained at gate: {len(gate_abstained)}"
        ),
        "",
        "## The judgement being made",
        "",
        "`ANSWERED_WRONG` asks whether the **cited passage supports the claim**, not whether",
        "the claim is good medicine. Clinical correctness is explicitly out of scope for this",
        "measurement, so a claim that is clinically right but unsupported by what it cites is",
        "still `ANSWERED_WRONG`, and that is the intended reading.",
        "",
        "Per question, apply in order:",
        "",
        "1. **any** claim unsupported by its citations -> `ANSWERED_WRONG`",
        "2. otherwise, supported but poorly presented, partial, or badly cited",
        "   -> `ANSWERED_DEFECTIVE`",
        "3. otherwise -> `ANSWERED_CORRECT`",
        "",
        "For an abstention, ask only whether the corpus in fact held the answer:",
        "`ABSTAINED_AVOIDABLE` if it did (a system defect), `ABSTAINED_CORRECT` if it did not.",
        "",
        "Flags order the reading. They decide nothing: an unflagged claim is not thereby",
        "correct, and `ANSWERED_WRONG` is usually semantic rather than numeric.",
        "",
        f"## Part 1 - answered ({len(answered)}), most-flagged first",
        "",
    ]

    for _, record in answered:
        passages = _passage_index(record)
        generation = record["generation"]
        verification = generation.get("verification") or {}
        lines += [
            f"### {record['question_id']} - ch{record['chapter_no']} {record['chapter']}",
            "",
            f"> {record['question']}",
            "",
            (
                f"- rendered {verification.get('rendered_claims')} claims, "
                f"{verification.get('supported_claims')} survived grounding, "
                f"{verification.get('withheld_claims')} discarded as ungrounded"
            ),
            f"- **bucket:** `______` one of {' | '.join(ANSWERED_BUCKETS)}",
            "- **note:** ",
            "",
        ]
        for index, claim in enumerate(generation.get("claims") or [], start=1):
            flags, missing = screen_claim(claim, passages)
            suffix = ""
            if flags:
                detail = f" (absent: {', '.join(missing)})" if missing else ""
                suffix = f"  -- **{', '.join(flags)}**{detail}"
            lines.append(f"  {index}. {claim.get('text')}{suffix}")
            for evidence_id in dict.fromkeys(claim.get("evidence_ids") or []):
                passage = passages.get(evidence_id)
                if passage is None:
                    # Cannot happen through the composer, which discards a claim citing
                    # anything unretrieved. Rendered rather than skipped so that a future
                    # change breaking that invariant is visible in the worksheet.
                    lines.append(f"    - `{evidence_id}` **NOT IN RETRIEVED SET**")
                    continue
                lines += _render_passage(passage, limit=passage_limit)
            lines.append("")
        lines.append("")

    lines += [
        f"## Part 2 - abstained at generation ({len(generation_abstained)})",
        "",
        "The model saw these passages and declined. The question is whether the corpus held",
        "the answer, not whether the model was polite about missing it.",
        "",
    ]
    for record in generation_abstained:
        generation = record["generation"]
        lines += [
            f"### {record['question_id']} - ch{record['chapter_no']} {record['chapter']}",
            "",
            f"> {record['question']}",
            "",
            f"- reason: `{generation.get('reason_code')}`",
            f"- model said: {(generation.get('message') or '').strip()}",
            f"- **bucket:** `______` one of {' | '.join(ABSTAINED_BUCKETS)}",
            "- **note:** ",
            "",
            "  Retrieved:",
        ]
        for passage in record["retrieval"]["passages"][:5]:
            lines += _render_passage(passage, limit=passage_limit)
        lines.append("")

    lines += [
        f"## Part 3 - abstained at the role gate ({len(gate_abstained)})",
        "",
        "No model call was made. These are `ABSTAINED_*` too, and the same question applies.",
        "",
    ]
    for record in gate_abstained:
        lines += [
            f"### {record['question_id']} - ch{record['chapter_no']} {record['chapter']}",
            "",
            f"> {record['question']}",
            "",
            f"- gate reason: `{record.get('gate_reason')}`",
            f"- missing roles: `{record['retrieval'].get('missing_required_roles')}`",
            f"- **bucket:** `______` one of {' | '.join(ABSTAINED_BUCKETS)}",
            "- **note:** ",
            "",
            "  Retrieved:",
        ]
        for passage in record["retrieval"]["passages"][:5]:
            lines += _render_passage(passage, limit=passage_limit)
        lines.append("")

    template = {
        "run": None,
        "buckets": {record["question_id"]: None for record in run["results"]},
        "notes": {},
    }
    return "\n".join(lines) + "\n", template


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--buckets-template",
        type=Path,
        default=None,
        help="also write a question_id -> bucket skeleton for the scorer to read",
    )
    parser.add_argument("--passage-text", type=int, default=700)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    worksheet, template = render(run, passage_limit=arguments.passage_text)

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(worksheet, encoding="utf-8")
    print(f"wrote {arguments.output}")

    if arguments.buckets_template:
        template["run"] = str(arguments.run)
        arguments.buckets_template.parent.mkdir(parents=True, exist_ok=True)
        arguments.buckets_template.write_text(
            json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"wrote {arguments.buckets_template}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
