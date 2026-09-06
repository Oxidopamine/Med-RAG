"""Render a coverage run into worksheets a human can label, and nothing more.

`run_mvp_coverage_stage1.py` records what the system did and leaves every `bucket` null.
This turns that file into the documents the labelling is actually done in. It renders. It
does not decide: no bucket is filled here, and the deterministic screens below flag rows for
attention without ever asserting that a claim is unsupported. That separation exists because
`ANSWERED_WRONG` is the stop-the-MVP bucket: a screen that guessed it would either
manufacture a stop or, worse, quietly clear one.

## The census instrument (plan Section 4.1)

The stage-1 worksheet (`--pass legacy`) sorted answered records most-flagged-first, printed
lexical flags beside each claim and asked for a hand-entered bucket. A census labelled from
it would be primed by a lexical detector of the same family as the floor Section 5 evaluates
against the labels, in an order chosen by that detector. The census passes therefore differ:

* `--run PATH[:ARM]`, repeatable, ARM one of `production`, `closed_book`, `naive`, so the
  agreement pass can interleave claims from three run files. The arm is written to the
  claims template and the key file, never to the rendered worksheet.
* `--questions PATH` shows each record's gold `source_statement`; the run carries none.
* `--bundle PATH`, mandatory for the census, renders each cited passage in full through
  `PassagePresenter`, which is the string the composer sent. The run file truncated
  `rendered_text`; the screens are re-run against the full text and the template records
  per pair which text source was used. The census is invalid if any pair used the run-file
  fallback, which needs `--allow-run-text`.
* `--shuffle-seed N` emits records, or claims under the agreement pass, in a seeded random
  order, replacing the flag-count sort.
* `--no-screen` suppresses every `screen_claim` flag and the bucket field, so the annotator
  sees no automated verdict. The census worksheets are built with it.
* `--pass agreement` shows question, gold statement and claim text, with no citations and
  no passages, arms interleaved at claim level. `--pass attribution` shows question, each
  claim and its cited passages, no gold statement, plus a separately shuffled abstention
  section. `--pass abstention` renders that section alone: every gate-blocked record and a
  seeded random subsample of the model-declared ones, all ten retrieved passages and the
  gold statement together, no answered-record claim. `--pass mislead` renders, from a label
  file, every flagged claim and a seeded random set of unflagged controls with the trigger
  status hidden.
* `--claims-template PATH` writes the label skeleton of Section 4.3. `--buckets-template`
  stays because `score_mvp_coverage.py --buckets` consumes it.

Worksheet items carry an item id (`A-017`) rather than the arm; the key file written beside
the worksheet maps item ids to records and is not opened until the pass is finished.

## What the screens do and do not catch

* `NUMERAL_NOT_IN_SOURCE`: a number in the claim that appears in none of the passages it
  cites. Matching is on standalone numeric tokens, so `6` does not match `36`.
* `WIDE_CITATION`: a claim citing four or more passages.
* `SOURCE_TRUNCATED`: the run stored a shortened copy of that passage, so the numeral
  screen is unreliable there; `--bundle` removes it.

Neither screen is evidence of anything on its own, and the rubric of Section 4.2 keeps the
numeral-presence screen out of the human labels so the lexical floor is not scored against a
reference built from itself.

Worksheets stay in `data/local/` and are never committed: they carry WHO passage text that
the release marks `render_allowed: false`.

    python scripts/build_claim_audit_worksheet.py \\
        --run data/local/cm/production-a.json:production \\
        --run data/local/cm/closed-book-164.json:closed_book \\
        --run data/local/cm/naive-164.json:naive \\
        --questions benchmarks/questions/mvp-coverage-who-hiv-v2.json \\
        --bundle data/local/validated-who-smart-hiv-release.json \\
        --pass agreement --shuffle-seed 20260906 --no-screen \\
        --output data/local/cm/pass-a-agreement.md \\
        --claims-template data/local/cm/labels-template.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

ANSWERED_BUCKETS = ("ANSWERED_CORRECT", "ANSWERED_DEFECTIVE", "ANSWERED_WRONG")
ABSTAINED_BUCKETS = ("ABSTAINED_CORRECT", "ABSTAINED_AVOIDABLE")
ARMS = ("production", "closed_book", "naive")
PASSES = ("legacy", "agreement", "attribution", "abstention", "mislead")
ATTRIBUTION_LABELS = "ATTRIBUTABLE | EXTRAPOLATORY | CONTRADICTORY | NO_SUPPORT | UNLABELABLE"
AGREEMENT_LABELS = "AGREES | PARTIAL | DISAGREES | NOT_ADJUDICABLE | UNLABELABLE"
ELIGIBILITY_CONDITIONS = (
    "PREGNANCY_OR_BREASTFEEDING | TB_OR_CRYPTOCOCCAL_COINFECTION | WEIGHT_OR_AGE_BAND | "
    "RENAL_OR_HEPATIC_FUNCTION | PRIOR_ART_EXPOSURE_OR_TREATMENT_LINE | "
    "CD4_OR_VIRAL_LOAD_THRESHOLD | SETTING_LEVEL_EPIDEMIOLOGY | OTHER:<condition verbatim>"
)
DEFAULT_ABSTENTION_SAMPLE = 20
DEFAULT_CONTROLS = 30

# Four is the threshold rather than three because conflict-bearing answers legitimately
# cite a small handful, and flagging those would bury the genuinely wide ones.
WIDE_CITATION_THRESHOLD = 4

_NUMERAL = re.compile(r"\d+(?:\.\d+)?")


def _standalone_numbers(text: str) -> list[str]:
    """Numbers in `text`, as written. Digits only: a spelled-out number is not caught."""

    return _NUMERAL.findall(text)


def _contains_number(haystack: str, number: str) -> bool:
    """True when `number` occurs in `haystack` as a whole numeric token."""

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
        (passages.get(evidence_id) or {}).get("rendered_text_truncated")
        for evidence_id in cited_ids
    ):
        flags.append("SOURCE_TRUNCATED")
    return flags, missing


def record_outcome(record: dict[str, Any]) -> str:
    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


def _passage_index(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["evidence_id"]: p for p in (record.get("retrieval") or {}).get("passages") or []}


def _render_passage(passage: dict[str, Any], *, limit: int | None) -> list[str]:
    text = (passage.get("rendered_text") or "").strip().replace("\n", "\n      ")
    if limit is not None and len(text) > limit:
        text = text[:limit] + " ..."
    marker = " [TRUNCATED IN RUN]" if passage.get("rendered_text_truncated") else ""
    source = passage.get("text_source")
    if source:
        marker += f" [text: {source}]"
    roles = ",".join(passage.get("qualified_roles") or passage.get("evidence_roles") or [])
    return [
        f"    - `{passage['evidence_id']}` *{passage.get('kind')}* roles=`{roles}`{marker}",
        f"      {text}",
    ]


# --------------------------------------------------------------------------------------
# Full passage text from the release bundle
# --------------------------------------------------------------------------------------


def bundle_renderer(bundle_path: Path) -> Callable[[str], str | None]:
    """Render evidence IDs to the full text the composer saw, through `PassagePresenter`.

    Imports `apps/api` lazily so the legacy worksheet stays stdlib-only. The presenter is
    built over the full evidence list because table-schema discovery scans every record.
    """

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


def resolve_passages(
    record: dict[str, Any], render_full: Callable[[str], str | None] | None
) -> dict[str, dict[str, Any]]:
    """The record's retrieved passages with full text where the bundle supplies it.

    Each passage gains `text_source`: `bundle` when the release rendered it in full, `run`
    when only the run file's (possibly truncated) copy was available.
    """

    passages: dict[str, dict[str, Any]] = {}
    for passage in (record.get("retrieval") or {}).get("passages") or []:
        copy = dict(passage)
        full = render_full(passage["evidence_id"]) if render_full is not None else None
        if full is not None:
            copy["rendered_text"] = full
            copy["rendered_text_truncated"] = False
            copy["text_source"] = "bundle"
        else:
            copy["text_source"] = "run"
        passages[passage["evidence_id"]] = copy
    return passages


# --------------------------------------------------------------------------------------
# Selections: seeded, recorded, never chosen by a screen
# --------------------------------------------------------------------------------------


def parse_run_argument(value: str) -> tuple[Path, str]:
    """`PATH[:ARM]`; a Windows drive colon is not an arm separator."""

    head, _, tail = value.rpartition(":")
    if head and tail in ARMS:
        return Path(head), tail
    return Path(value), "production"


def seeded_order(items: Iterable[Any], seed: int | None, key: Callable[[Any], str]) -> list[Any]:
    ordered = sorted(items, key=key)
    if seed is None:
        return ordered
    random.Random(seed).shuffle(ordered)
    return ordered


def abstention_review_selection(
    records: list[dict[str, Any]], *, sample_size: int, seed: int | None
) -> tuple[list[str], list[str], int]:
    """Every gate-blocked question, and a seeded simple random sample of the model-declared.

    Returns (gate_blocked_ids, sampled_model_declared_ids, model_declared_total).
    """

    gate_blocked = sorted(
        r["question_id"]
        for r in records
        if record_outcome(r) == "ABSTAINED" and r.get("generation") is None
    )
    model_declared = sorted(
        r["question_id"]
        for r in records
        if record_outcome(r) == "ABSTAINED" and r.get("generation") is not None
    )
    size = min(sample_size, len(model_declared))
    sampled = sorted(random.Random(seed).sample(model_declared, size)) if size else []
    return gate_blocked, sampled, len(model_declared)


def answered_record_sample(
    records: list[dict[str, Any]], *, sample_size: int | None, seed: int | None
) -> list[dict[str, Any]]:
    answered = [r for r in records if record_outcome(r) == "ANSWERED"]
    if sample_size is None or sample_size >= len(answered):
        return answered
    ids = sorted(r["question_id"] for r in answered)
    chosen = set(random.Random(seed).sample(ids, sample_size))
    return [r for r in answered if r["question_id"] in chosen]


def label_key(question_id: str, arm: str) -> str:
    return question_id if arm == "production" else f"{question_id}:{arm}"


# --------------------------------------------------------------------------------------
# The legacy stage-1 worksheet
# --------------------------------------------------------------------------------------


def render(
    run: dict[str, Any],
    *,
    passage_limit: int,
    screen: bool = True,
    shuffle_seed: int | None = None,
) -> tuple[str, dict[str, Any]]:
    answered: list[tuple[int, dict[str, Any]]] = []
    generation_abstained: list[dict[str, Any]] = []
    gate_abstained: list[dict[str, Any]] = []
    errored: list[dict[str, Any]] = []

    for record in run["results"]:
        outcome = record_outcome(record)
        if outcome == "ERROR":
            # A generation failure is a missing measurement: it is listed so the reader
            # can see it, and it carries no bucket because there is nothing to classify.
            errored.append(record)
        elif record.get("generation") is None:
            gate_abstained.append(record)
        elif outcome == "ABSTAINED":
            generation_abstained.append(record)
        else:
            passages = _passage_index(record)
            claims = record["generation"].get("claims") or []
            flag_count = (
                sum(len(screen_claim(claim, passages)[0]) for claim in claims) if screen else 0
            )
            answered.append((flag_count, record))

    if shuffle_seed is not None:
        answered = seeded_order(answered, shuffle_seed, key=lambda pair: pair[1]["question_id"])
    else:
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
            f"abstained at gate: {len(gate_abstained)}  "
            f"generation errors: {len(errored)}"
        ),
        f"- screens: {'on' if screen else 'off (--no-screen)'}",
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
        f"## Part 1 - answered ({len(answered)}), "
        + ("seeded order" if shuffle_seed is not None else "most-flagged first"),
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
        ]
        if screen:
            lines.append(f"- **bucket:** `______` one of {' | '.join(ANSWERED_BUCKETS)}")
        lines += ["- **note:** ", ""]
        for index, claim in enumerate(generation.get("claims") or [], start=1):
            suffix = ""
            if screen:
                flags, missing = screen_claim(claim, passages)
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
        ]
        if screen:
            lines.append(f"- **bucket:** `______` one of {' | '.join(ABSTAINED_BUCKETS)}")
        lines += ["- **note:** ", "", "  Retrieved:"]
        for passage in (record.get("retrieval") or {}).get("passages") or []:
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
            f"- missing roles: `{(record.get('retrieval') or {}).get('missing_required_roles')}`",
        ]
        if screen:
            lines.append(f"- **bucket:** `______` one of {' | '.join(ABSTAINED_BUCKETS)}")
        lines += ["- **note:** ", "", "  Retrieved:"]
        for passage in (record.get("retrieval") or {}).get("passages") or []:
            lines += _render_passage(passage, limit=passage_limit)
        lines.append("")

    lines += [
        f"## Part 4 - generation errors ({len(errored)}), not classifiable",
        "",
        "A quota failure, a declined finish or a model failure recorded under its own",
        "error class. These are missing measurements: they enter no denominator, carry no",
        "bucket, and are re-run with `--only-question-ids` before any bound is quoted.",
        "",
    ]
    for record in errored:
        generation = record["generation"]
        detail = generation.get("error") or generation.get("message") or ""
        lines += [
            f"### {record['question_id']} - ch{record['chapter_no']} {record['chapter']}",
            "",
            f"- error class: `{generation.get('error_class') or 'UNCLASSIFIED'}`",
            f"- detail: {detail.strip()[:300]}",
            "",
        ]

    template = {
        "run": None,
        "buckets": {record["question_id"]: None for record in run["results"]},
        "notes": {},
    }
    return "\n".join(lines) + "\n", template


# --------------------------------------------------------------------------------------
# The census passes
# --------------------------------------------------------------------------------------


class Census:
    """The runs, the gold statements, the passage renderer and the seeded selections."""

    def __init__(
        self,
        runs: dict[str, dict[str, Any]],
        *,
        questions: dict[str, Any] | None,
        render_full: Callable[[str], str | None] | None,
        shuffle_seed: int | None,
        abstention_sample: int,
        record_sample: int | None,
        screen: bool,
    ) -> None:
        if "production" not in runs:
            raise SystemExit("the census passes need a production run (--run PATH:production)")
        self.runs = runs
        self.gold = {
            item["question_id"]: item.get("source_statement") or ""
            for item in (questions or {}).get("items", [])
        }
        self.render_full = render_full
        self.seed = shuffle_seed
        self.screen = screen
        production = runs["production"]["results"]
        self.production = {r["question_id"]: r for r in production}
        self.gate_blocked, self.sampled, self.model_declared_total = abstention_review_selection(
            production, sample_size=abstention_sample, seed=shuffle_seed
        )
        self.review_ids = set(self.gate_blocked) | set(self.sampled)
        self.answered = answered_record_sample(
            production, sample_size=record_sample, seed=shuffle_seed
        )
        self.answered_ids = {r["question_id"] for r in self.answered}
        self.text_sources = {"bundle": 0, "run": 0}
        self.abstention_sample_size = abstention_sample

    def gold_statement(self, question_id: str) -> str:
        return self.gold.get(question_id) or "(no gold statement: pass --questions)"

    def arm_records(self, arm: str) -> list[dict[str, Any]]:
        """Answered records of an arm that enter the census.

        Production: the (sampled) answered records. Closed book: every answered record.
        Naive: only the questions selected for abstention review, which is the only place
        the chain's benefit is observable (plan Section 3.3).
        """

        run = self.runs.get(arm)
        if run is None:
            return []
        records = [r for r in run["results"] if record_outcome(r) == "ANSWERED"]
        if arm == "production":
            return [r for r in records if r["question_id"] in self.answered_ids]
        if arm == "naive":
            return [r for r in records if r["question_id"] in self.review_ids]
        return records

    def passages(self, record: dict[str, Any]) -> dict[str, dict[str, Any]]:
        passages = resolve_passages(record, self.render_full)
        for passage in passages.values():
            self.text_sources[passage["text_source"]] += 1
        return passages

    def claim_items(self) -> list[dict[str, Any]]:
        items = []
        for arm in ARMS:
            for record in self.arm_records(arm):
                for index, claim in enumerate(record["generation"].get("claims") or [], start=1):
                    items.append(
                        {
                            "arm": arm,
                            "question_id": record["question_id"],
                            "key": label_key(record["question_id"], arm),
                            "index": index,
                            "record": record,
                            "claim": claim,
                        }
                    )
        return items


def _header(title: str, census: Census, *, blinded: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"- shuffle seed: `{census.seed}`",
        f"- screens: {'on' if census.screen else 'off (--no-screen)'}",
        f"- arm blinding: {blinded}",
        "- started_at: `______`  finished_at: `______`  (UTC, recorded in the label file)",
        "",
    ]


def render_agreement(census: Census) -> tuple[list[str], list[dict[str, Any]]]:
    """Pass A: claim text, question and gold statement only; arms interleaved and concealed."""

    items = seeded_order(
        census.claim_items(), census.seed, key=lambda i: f"{i['key']}#{i['index']:03d}"
    )
    lines = _header(
        "Pass A - guideline agreement",
        census,
        blinded="arm concealed; item ids map to arms in the key file",
    ) + [
        "Label each claim against the gold statement alone. `NOT_ADJUDICABLE` when deciding",
        "needs clinical knowledge beyond the two texts; label it and move on. An eligibility",
        "drop is a condition the gold statement states that neither the claim nor the",
        "question supplies; a condition the question already states is not a drop.",
        "",
    ]
    key: list[dict[str, Any]] = []
    for number, item in enumerate(items, start=1):
        item_id = f"A-{number:03d}"
        key.append(
            {"item": item_id, "key": item["key"], "arm": item["arm"], "index": item["index"]}
        )
        record = item["record"]
        lines += [
            f"### {item_id}  ({record['question_id']})",
            "",
            f"> Question: {record['question']}",
            f"> Gold: {census.gold_statement(record['question_id'])}",
            "",
            f"Claim: {item['claim'].get('text')}",
            "",
            f"- agreement: `______`  {AGREEMENT_LABELS}",
            "- eligibility_drop: `______`  true | false",
            f"- eligibility_condition: `______`  {ELIGIBILITY_CONDITIONS} | null",
            "- note: ",
            "",
        ]
    question_ids = sorted({item["question_id"] for item in items})
    lines += [
        f"## Records - question validity ({len(question_ids)} questions)",
        "",
        "Would a clinician plausibly ask this, and does the gold statement answer it? One",
        "judgement per question, whatever arms answered it.",
        "",
    ]
    for question_id in question_ids:
        record = census.production.get(question_id) or next(
            r for arm in ARMS for r in census.arm_records(arm) if r["question_id"] == question_id
        )
        lines += [
            f"### {question_id}",
            "",
            f"> Question: {record['question']}",
            f"> Gold: {census.gold_statement(question_id)}",
            "",
            "- question_valid: `______`  true | false",
            "- note: ",
            "",
        ]
    return lines, key


def _claim_block(
    census: Census, item: dict[str, Any], passages: dict[str, dict[str, Any]]
) -> list[str]:
    claim = item["claim"]
    suffix = ""
    if census.screen:
        flags, missing = screen_claim(claim, passages)
        if flags:
            detail = f" (absent: {', '.join(missing)})" if missing else ""
            suffix = f"  -- **{', '.join(flags)}**{detail}"
    lines = [f"Claim {item['index']}: {claim.get('text')}{suffix}", ""]
    cited = list(dict.fromkeys(claim.get("evidence_ids") or []))
    for evidence_id in cited:
        passage = passages.get(evidence_id)
        if passage is None:
            lines.append(f"    - `{evidence_id}` **NOT IN RETRIEVED SET**")
            continue
        lines += _render_passage(passage, limit=None)
        lines.append(f"    - pair_attribution `{evidence_id}`: `______`  {ATTRIBUTION_LABELS}")
        lines.append("")
    if not cited:
        lines.append("    (no citations)")
    lines += [f"- joint_attribution: `______`  {ATTRIBUTION_LABELS}", "- note: ", ""]
    return lines


def render_attribution(census: Census, *, include_abstentions: bool = True) -> list[str]:
    """Pass B: production (and naive) claims with their full cited passages, no gold statement."""

    lines = _header(
        "Pass B - attribution",
        census,
        blinded="unblinded to arm by construction (citations reveal it)",
    ) + [
        "Judge whether the passage states or entails each quantity the claim asserts, not",
        "whether the numeral appears in it: 'twice daily' against 'every 12 hours' is",
        "ATTRIBUTABLE; '1000 copies/mL' against a passage with no threshold is at most",
        "EXTRAPOLATORY. Joint attribution is the same judgement over every cited passage",
        "taken together, in retrieval order.",
        "",
    ]
    for arm in ("production", "naive"):
        records = seeded_order(census.arm_records(arm), census.seed, key=lambda r: r["question_id"])
        if not records:
            continue
        title = "Production A" if arm == "production" else "Naive arm on the withheld questions"
        lines += [f"## {title} ({len(records)} records)", ""]
        for record in records:
            passages = census.passages(record)
            heading = label_key(record["question_id"], arm)
            lines += [
                f"### {heading} - ch{record['chapter_no']} {record['chapter']}",
                "",
                f"> Question: {record['question']}",
                "",
            ]
            for index, claim in enumerate(record["generation"].get("claims") or [], start=1):
                item = {"index": index, "claim": claim}
                lines += _claim_block(census, item, passages)
            lines += ["- presentation_defect: `______`  true | false", "- note: ", ""]
    if include_abstentions:
        lines += render_abstention(census, standalone=False)
    return lines


def render_abstention(census: Census, *, standalone: bool = True) -> list[str]:
    """The abstention adjudications: all retrieved passages and the gold statement together."""

    lines = (
        _header("Abstention adjudications", census, blinded="not applicable: no claim is shown")
        if standalone
        else ["## Abstention adjudications (separately shuffled)", ""]
    )
    lines += [
        f"Every gate-blocked record ({len(census.gate_blocked)}) and a seeded random",
        f"{len(census.sampled)} of the {census.model_declared_total} model-declared ones",
        f"(m = {census.abstention_sample_size}, seed {census.seed}, recorded before any label).",
        "All retrieved passages are shown, in full. No answered-record claim appears here.",
        "",
    ]
    ordered = seeded_order(
        list(census.gate_blocked) + list(census.sampled),
        census.seed + 1 if census.seed is not None else None,
        key=lambda q: q,
    )
    for question_id in ordered:
        record = census.production[question_id]
        gate = question_id in census.gate_blocked
        lines += [
            f"### {question_id} - ch{record['chapter_no']} {record['chapter']}"
            + ("  [gate-blocked]" if gate else "  [model-declared, sampled]"),
            "",
            f"> Question: {record['question']}",
            f"> Gold: {census.gold_statement(question_id)}",
            "",
        ]
        if gate:
            lines.append(f"- gate reason: `{record.get('gate_reason')}`")
        else:
            lines.append(
                f"- model said: {((record.get('generation') or {}).get('message') or '').strip()}"
            )
        lines += ["", "  Retrieved:"]
        for passage in census.passages(record).values():
            lines += _render_passage(passage, limit=None)
        lines.append("")
        if gate:
            lines += [
                "- gate_right: `______`  true | false  "
                "(nothing retrieved could carry the recommendation)",
                "- dak_has_answer: `______`  true | false  "
                "(a retrieved passage carries it and the gate still blocked)",
            ]
        else:
            lines += [
                "- any_retrieved_passage_answers: `______`  true | false",
                "- question_valid: `______`  true | false",
            ]
        lines += ["- note: ", ""]
    return lines


def _load_cm_statistics():
    path = REPO_ROOT / "scripts" / "cm_statistics.py"
    spec = importlib.util.spec_from_file_location("medrag_cm_statistics", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def render_mislead(
    census: Census, labels: dict[str, Any], *, controls: int
) -> tuple[list[str], list[dict[str, Any]]]:
    """Pass C: every flagged claim and seeded controls, trigger status and earlier labels hidden."""

    statistics = _load_cm_statistics()
    flagged: list[tuple[str, int]] = []
    clean: list[tuple[str, int]] = []
    for key, record_label in (labels.get("records") or {}).items():
        for claim_label in record_label.get("claims") or []:
            target = flagged if statistics.claim_is_flagged(claim_label, record_label) else clean
            target.append((key, int(claim_label["index"])))
    chosen_controls = sorted(
        random.Random(census.seed).sample(sorted(clean), min(controls, len(clean)))
    )
    selected = seeded_order(
        [(k, i, "flagged") for k, i in flagged] + [(k, i, "control") for k, i in chosen_controls],
        census.seed,
        key=lambda t: f"{t[0]}#{t[1]:03d}",
    )
    by_key: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        for record in (census.runs.get(arm) or {}).get("results", []):
            by_key[label_key(record["question_id"], arm)] = record
    lines = _header(
        "Pass C - potential to mislead",
        census,
        blinded="trigger status and all earlier labels hidden",
    ) + [
        "Rate every item on two axes from the claim, the question and the gold statement",
        "alone. Extent: NONE | MILD | MODERATE | SEVERE (Appendix A anchors). Likelihood that a",
        "clinician acting on the claim as rendered reaches that extent: LOW | MEDIUM | HIGH.",
        "One sentence of reasoning. Provisional, rated by a non-clinician.",
        "",
    ]
    key_rows: list[dict[str, Any]] = []
    for number, (key, index, status) in enumerate(selected, start=1):
        item_id = f"C-{number:03d}"
        key_rows.append({"item": item_id, "key": key, "index": index, "status": status})
        record = by_key.get(key)
        if record is None:
            lines += [f"### {item_id}", "", f"(record {key} not in the given runs)", ""]
            continue
        claims = (record.get("generation") or {}).get("claims") or []
        text = claims[index - 1].get("text") if 0 < index <= len(claims) else "(claim not found)"
        lines += [
            f"### {item_id}  ({record['question_id']})",
            "",
            f"> Question: {record['question']}",
            f"> Gold: {census.gold_statement(record['question_id'])}",
            "",
            f"Claim: {text}",
            "",
            "- extent: `______`  NONE | MILD | MODERATE | SEVERE",
            "- likelihood: `______`  LOW | MEDIUM | HIGH",
            "- reason: ",
            "",
        ]
    return lines, key_rows


def claims_template(
    census: Census,
    *,
    run_paths: dict[str, Path],
    rubric_sha256: str | None,
) -> dict[str, Any]:
    """The label skeleton of plan Section 4.3, every label null, every text source recorded."""

    records: dict[str, Any] = {}
    for arm in ARMS:
        for record in census.arm_records(arm):
            passages = resolve_passages(record, census.render_full)
            claims = []
            for index, claim in enumerate(record["generation"].get("claims") or [], start=1):
                cited = list(dict.fromkeys(claim.get("evidence_ids") or []))
                claims.append(
                    {
                        "index": index,
                        "pair_attribution": {evidence_id: None for evidence_id in cited},
                        "pair_text_source": {
                            evidence_id: (passages.get(evidence_id) or {}).get("text_source", "run")
                            for evidence_id in cited
                        },
                        "joint_attribution": None,
                        "agreement": None,
                        "eligibility_drop": None,
                        "eligibility_condition": None,
                        "mislead": None,
                        "note": "",
                    }
                )
            records[label_key(record["question_id"], arm)] = {
                "arm": arm,
                "claims": claims,
                "presentation_defect": None,
                "question_valid": None,
                "note": "",
            }
    production = census.runs["production"]
    sources = {"bundle": 0, "run": 0}
    for record_label in records.values():
        for claim in record_label["claims"]:
            for source in claim["pair_text_source"].values():
                sources[source] += 1
    return {
        "schema_version": 1,
        "run": {
            "local": str(run_paths["production"]),
            "published": None,
            "question_set_sha256": (production.get("question_set") or {}).get("sha256"),
            "run_label": production.get("run_label"),
        },
        "arms": {arm: str(path) for arm, path in run_paths.items() if arm != "production"},
        "annotator": None,
        "rubric_sha256": rubric_sha256,
        "shuffle_seed": census.seed,
        "passes": {
            name: {"started_at": None, "finished_at": None}
            for name in ("agreement", "attribution", "mislead")
        },
        "records": records,
        "gate_blocked": {
            question_id: {"gate_right": None, "dak_has_answer": None, "note": ""}
            for question_id in census.gate_blocked
        },
        "abstained_sample": {
            "size": len(census.sampled),
            "seed": census.seed,
            "model_declared_total": census.model_declared_total,
            **{
                question_id: {"any_retrieved_passage_answers": None, "question_valid": None}
                for question_id in census.sampled
            },
        },
        "pair_text_sources": sources,
        "census_valid": sources["run"] == 0,
    }


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="PATH[:ARM]",
        help="a run file, with its arm; repeatable; the arm defaults to production",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pass", dest="worksheet_pass", choices=PASSES, default="legacy")
    parser.add_argument(
        "--questions", type=Path, default=None, help="question set with gold statements"
    )
    parser.add_argument(
        "--bundle", type=Path, default=None, help="release bundle for full passage text"
    )
    parser.add_argument(
        "--allow-run-text",
        action="store_true",
        help="permit the run-file text fallback; the census is invalid if any pair uses it",
    )
    parser.add_argument("--shuffle-seed", type=int, default=None)
    parser.add_argument("--no-screen", action="store_true")
    parser.add_argument("--claims-template", type=Path, default=None)
    parser.add_argument(
        "--buckets-template",
        type=Path,
        default=None,
        help="also write a question_id -> bucket skeleton for the scorer to read",
    )
    parser.add_argument(
        "--key", type=Path, default=None, help="item-id key file; default beside --output"
    )
    parser.add_argument("--passage-text", type=int, default=700, help="legacy pass truncation only")
    parser.add_argument(
        "--record-sample", type=int, default=None, help="seeded subset of answered records"
    )
    parser.add_argument("--abstention-sample", type=int, default=DEFAULT_ABSTENTION_SAMPLE)
    parser.add_argument("--labels", type=Path, default=None, help="label file, for --pass mislead")
    parser.add_argument("--controls", type=int, default=DEFAULT_CONTROLS)
    parser.add_argument(
        "--rubric", type=Path, default=None, help="the deposited plan, for rubric_sha256"
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    run_paths: dict[str, Path] = {}
    runs: dict[str, dict[str, Any]] = {}
    for value in arguments.run:
        path, arm = parse_run_argument(value)
        if arm in runs:
            raise SystemExit(f"two runs given for arm {arm}")
        run_paths[arm] = path
        runs[arm] = json.loads(path.read_text(encoding="utf-8"))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)

    if arguments.worksheet_pass == "legacy":
        run = runs.get("production") or next(iter(runs.values()))
        worksheet, template = render(
            run,
            passage_limit=arguments.passage_text,
            screen=not arguments.no_screen,
            shuffle_seed=arguments.shuffle_seed,
        )
        arguments.output.write_text(worksheet, encoding="utf-8")
        print(f"wrote {arguments.output}")
        if arguments.buckets_template:
            template["run"] = str(next(iter(run_paths.values())))
            arguments.buckets_template.parent.mkdir(parents=True, exist_ok=True)
            arguments.buckets_template.write_text(
                json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"wrote {arguments.buckets_template}")
        return 0

    needs_text = arguments.worksheet_pass in {"attribution", "abstention"}
    if needs_text and arguments.bundle is None and not arguments.allow_run_text:
        raise SystemExit(
            "the census passes render passages from the release: pass --bundle, or "
            "--allow-run-text for a shakedown whose labels the census cannot use"
        )
    render_full = bundle_renderer(arguments.bundle) if arguments.bundle else None
    questions = (
        json.loads(arguments.questions.read_text(encoding="utf-8")) if arguments.questions else None
    )
    census = Census(
        runs,
        questions=questions,
        render_full=render_full,
        shuffle_seed=arguments.shuffle_seed,
        abstention_sample=arguments.abstention_sample,
        record_sample=arguments.record_sample,
        screen=not arguments.no_screen,
    )
    key_rows: list[dict[str, Any]] = []
    if arguments.worksheet_pass == "agreement":
        lines, key_rows = render_agreement(census)
    elif arguments.worksheet_pass == "attribution":
        lines = render_attribution(census)
    elif arguments.worksheet_pass == "abstention":
        lines = render_abstention(census)
    else:
        if arguments.labels is None:
            raise SystemExit("--pass mislead needs --labels")
        labels = json.loads(arguments.labels.read_text(encoding="utf-8"))
        lines, key_rows = render_mislead(census, labels, controls=arguments.controls)
    arguments.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {arguments.output}")
    if census.text_sources["run"]:
        print(
            f"!! {census.text_sources['run']} passages rendered from the run file; the census is "
            "invalid until every pair renders from the bundle"
        )

    if key_rows:
        key_path = arguments.key or arguments.output.with_suffix(".key.json")
        key_path.write_text(json.dumps(key_rows, indent=2), encoding="utf-8")
        print(f"wrote {key_path}  (do not open until the pass is finished)")

    if arguments.claims_template:
        rubric = (
            hashlib.sha256(arguments.rubric.read_bytes()).hexdigest() if arguments.rubric else None
        )
        template = claims_template(census, run_paths=run_paths, rubric_sha256=rubric)
        arguments.claims_template.parent.mkdir(parents=True, exist_ok=True)
        arguments.claims_template.write_text(
            json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"wrote {arguments.claims_template}")
    if arguments.buckets_template:
        production = census.runs["production"]
        skeleton = {
            "run": str(run_paths["production"]),
            "buckets": {record["question_id"]: None for record in production["results"]},
            "notes": {},
        }
        arguments.buckets_template.write_text(
            json.dumps(skeleton, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"wrote {arguments.buckets_template}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
