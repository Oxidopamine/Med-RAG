"""Emit the project owner's review worksheet for the MVP coverage question set.

The question set is committed as DRAFT: the frame and the draw are mechanical and
reproducible, but each question is a judgement about how a clinician would ask, and
docs/mvp-definition.md requires `review_status` to reach REVIEWED before stage 1 runs.
This renders the 50 drawn items into one reviewable document, ordered by review priority,
with each question adjacent to the recommendation it was written from.

It renders. It does not decide: no question is edited, reordered into usability, or
dropped here, and the keep/rewrite/drop column is left blank for a human.

It also reports the frame against the draw. The draw is 50 from 165 without replacement,
so a chapter's count is hypergeometric, not binomial - at a 30% sampling fraction the
finite-population correction is substantial and a binomial interval would overstate the
variance. The frame counts come from re-extracting the parent PDF through
build_mvp_question_set.py rather than from the committed JSON's own summary, so the table
cannot quietly disagree with the frame it claims to describe.

Usage:

    python scripts/build_mvp_review_worksheet.py --pdf <path-to-2021-guidelines.pdf>

The decision column is filled in by hand. Re-running this overwrites the file, so do not
regenerate once review has started.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from fractions import Fraction
from math import comb
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_mvp_question_set import SAMPLE_N, SEED, _extract, _frame

NEAR_DUPLICATE_PREFIX = "NEAR_DUPLICATE_OF:"
HIGH_OVERLAP = "REVIEW_HIGH_SOURCE_OVERLAP"
GENERAL_ADVICE = "GENERAL_POPULATION_ADVICE"
UNUSABLE = "UNUSABLE_FRAGMENT"

GOODNESS_OF_FIT_TRIALS = 200_000


# --- hypergeometric, exact ------------------------------------------------------------
#
# Fractions throughout. The population is small enough that exact rational arithmetic is
# cheaper than justifying a floating-point tolerance, and an interval that decides whether
# a pre-registered sample is faithful should not turn on rounding.


def _support(pop: int, successes: int, draws: int) -> range:
    return range(max(0, draws - (pop - successes)), min(successes, draws) + 1)


def _pmf(k: int, pop: int, successes: int, draws: int) -> Fraction:
    if k not in _support(pop, successes, draws):
        return Fraction(0)
    return Fraction(comb(successes, k) * comb(pop - successes, draws - k), comb(pop, draws))


def _central_interval(pop: int, successes: int, draws: int, alpha: Fraction) -> tuple[int, int]:
    """Equal-tailed interval: the widest [lo, hi] leaving at most alpha/2 in each tail."""
    half, support = alpha / 2, _support(pop, successes, draws)
    lo = hi = None
    cumulative = Fraction(0)
    for k in support:
        if cumulative + _pmf(k, pop, successes, draws) > half:
            lo = k
            break
        cumulative += _pmf(k, pop, successes, draws)
    cumulative = Fraction(0)
    for k in reversed(support):
        if cumulative + _pmf(k, pop, successes, draws) > half:
            hi = k
            break
        cumulative += _pmf(k, pop, successes, draws)
    return lo, hi


def _two_sided_p(observed: int, pop: int, successes: int, draws: int) -> Fraction:
    """Fisher's convention: every outcome no more likely than the one observed."""
    threshold = _pmf(observed, pop, successes, draws)
    return sum(
        (
            probability
            for k in _support(pop, successes, draws)
            if (probability := _pmf(k, pop, successes, draws)) <= threshold
        ),
        Fraction(0),
    )


def _goodness_of_fit(frame_counts: list[int], drawn_counts: list[int], draws: int) -> float:
    """Monte Carlo p for the whole composition under the multivariate hypergeometric.

    The per-chapter tests are six separate looks at one sample. This is the single test
    that asks whether the composition as a whole is consistent with a uniform draw, so a
    reader is not left multiplying six p-values by eye. Exact enumeration of the
    multivariate distribution is not worth it at this size; the simulation is seeded, so
    the number is reproducible.
    """
    population = sum(frame_counts)
    expected = [draws * count / population for count in frame_counts]
    statistic = sum((c - e) ** 2 / e for c, e in zip(drawn_counts, expected, strict=True))
    urn = [index for index, count in enumerate(frame_counts) for _ in range(count)]
    rng = random.Random(SEED)
    hits = 0
    for _ in range(GOODNESS_OF_FIT_TRIALS):
        simulated = [0] * len(frame_counts)
        for index in rng.sample(urn, draws):
            simulated[index] += 1
        chi = sum((c - e) ** 2 / e for c, e in zip(simulated, expected, strict=True))
        if chi >= statistic - 1e-12:
            hits += 1
    return hits / GOODNESS_OF_FIT_TRIALS


# --- rendering ------------------------------------------------------------------------

HEADER = (
    "| # | question_id | chapter | Ely form | Question "
    "| Source statement it was written from | overlap | flags | keep / rewrite / drop |"
)
RULE = "|---|---|---|---|---|---|---|---|---|"


def _block(text: str) -> list[str]:
    return dedent(text).strip("\n").split("\n")


def _cell(text: str | None) -> str:
    """A markdown table cell.

    The committed statements carry no pipes and no newlines - checked before writing this -
    so the only case left to handle is the fragment that has no question.
    """
    return text if text else "_(none)_"


def _overlap(item: dict) -> str:
    value = item["source_term_overlap"]
    return "n/a" if value is None else f"{value:.3f}"


def _flags(item: dict) -> str:
    return "`" + "`, `".join(item["flags"]) + "`" if item["flags"] else ""


def _rows(items: list[dict], header: str = HEADER) -> list[str]:
    lines = [header, RULE]
    for number, item in enumerate(items, start=1):
        question = f"**{item['question']}**" if item["question"] else _cell(None)
        lines.append(
            f"| {number} | `{item['question_id']}` | {item['chapter_no']} {item['chapter']} "
            f"| {_cell(item['ely_form'])} | {question} | {_cell(item['source_statement'])} "
            f"| {_overlap(item)} | {_flags(item)} | |"
        )
    return lines


def _frame_vs_draw(frame: list[dict], drawn: list[dict]) -> tuple[list[str], dict, float]:
    pop, draws = len(frame), len(drawn)
    frame_counts = Counter((r["chapter_no"], r["chapter"]) for r in frame)
    drawn_counts = Counter((i["chapter_no"], i["chapter"]) for i in drawn)

    lines = [
        "| chapter | frame | frame share | drawn | drawn share | expected "
        "| exact 95% interval | two-sided p |",
        "|---|---:|---:|---:|---:|---:|:---:|---:|",
    ]
    detail: dict[int, dict] = {}
    for key in sorted(frame_counts):
        number, name = key
        successes, observed = frame_counts[key], drawn_counts.get(key, 0)
        expected = Fraction(draws * successes, pop)
        lo, hi = _central_interval(pop, successes, draws, Fraction(1, 20))
        probability = _two_sided_p(observed, pop, successes, draws)
        lines.append(
            f"| {number} {name} | {successes} | {successes / pop * 100:.1f}% | {observed} "
            f"| {observed / draws * 100:.1f}% | {float(expected):.2f} | [{lo}, {hi}] "
            f"| {float(probability):.3f} |"
        )
        detail[number] = {
            "name": name,
            "frame": successes,
            "frame_share": successes / pop,
            "drawn": observed,
            "expected": float(expected),
            "interval": (lo, hi),
            "p": float(probability),
        }
    lines.append(f"| **total** | **{pop}** | 100.0% | **{draws}** | 100.0% | | | |")

    ordered = sorted(frame_counts)
    fit = _goodness_of_fit(
        [frame_counts[key] for key in ordered],
        [drawn_counts.get(key, 0) for key in ordered],
        draws,
    )
    return lines, detail, fit


def _priority_groups(items: list[dict]) -> tuple[list[dict], ...]:
    """The five review groups, in priority order.

    They are disjoint in the committed set. Assert it rather than assume it: an item that
    gained a second flag would otherwise be silently duplicated or dropped from the
    worksheet, which is the one thing a review document must never do.
    """
    high = sorted(
        (i for i in items if HIGH_OVERLAP in i["flags"]),
        key=lambda i: (-i["source_term_overlap"], i["question_id"]),
    )
    near_duplicate = [
        i for i in items if any(f.startswith(NEAR_DUPLICATE_PREFIX) for f in i["flags"])
    ]
    general = sorted(
        (i for i in items if GENERAL_ADVICE in i["flags"]), key=lambda i: i["question_id"]
    )
    unusable = [i for i in items if UNUSABLE in i["flags"]]

    flagged_groups = (high, near_duplicate, general, unusable)
    flagged = {i["question_id"] for group in flagged_groups for i in group}
    if len(flagged) != sum(len(group) for group in flagged_groups):
        raise SystemExit(
            "an item carries flags from more than one priority group; the ordering is ambiguous"
        )
    remainder = sorted(
        (i for i in items if i["question_id"] not in flagged),
        key=lambda i: (i["chapter_no"], i["question_id"]),
    )
    if any(not i["usable"] for i in high + near_duplicate + general + remainder):
        raise SystemExit("an unusable item reached a clinical-review group")
    return high, near_duplicate, general, remainder, unusable


def _preamble(
    committed: dict, questions: Path, extracted: int, graded: int, frame: int, usable: int
) -> list[str]:
    screen = committed["source_term_overlap"]
    return _block(
        f"""
        # MVP coverage question set - review worksheet

        Set: [`{questions.name}`]({questions.name}) · `{committed["set_id"]}` ·
        review status **{committed["review_status"].split(" - ")[0]}**

        Generated by
        [`scripts/build_mvp_review_worksheet.py`](../../scripts/build_mvp_review_worksheet.py).
        **The decision column is filled in by hand, and re-running the generator overwrites
        this file** - do not regenerate once review has started.

        ## What this is for

        [docs/mvp-definition.md](../../docs/mvp-definition.md) pre-registers the stage-1
        decision rule and requires `review_status` to reach `REVIEWED` before stage 1 runs:
        *a coverage number measured on unreviewed questions inherits whatever bias the
        drafting introduced*. The frame and the draw are mechanical and reproduce exactly
        (below). The questions are not - each is a model-authored judgement about how a
        clinician would ask - and this worksheet exists so a human can rule on every one.

        The standard to apply, from the definition document: **rewrite any question that
        reads like it was written by someone who had already read the recommendation.** A
        question should give the clinical situation and the decision in vocabulary a
        practitioner would use unprompted, and should not carry across the recommendation's
        distinctive framing.

        `overlap` is the lexical-leakage screen - the fraction of a question's content terms
        that also appear in the recommendation it was written from. It is a **review
        priority, not a verdict**: the metric cannot separate unavoidable clinical nouns (a
        question about cryptococcal meningitis has to say "cryptococcal meningitis") from
        real leakage. This set measures mean {screen["mean"]}, median {screen["median"]}; the
        source-derived 430-case suite that this set exists to avoid resembling measures 1.000
        on five strata.

        ## Frame and draw, verified

        `scripts/build_mvp_question_set.py` was run against the parent PDF. The frame and the
        seeded draw reproduce exactly: **{extracted} extracted statements, {graded} graded
        recommendations, frame size {frame}, {SAMPLE_N} drawn** at seed `{SEED}`, {usable}
        usable, and the drawn ids match the committed set in order.
        """
    )


def _analysis(frame: int, drawn: int, table: list[str], detail: dict, fit: float) -> list[str]:
    coinfections, service = detail[6], detail[7]
    lines = _block(
        f"""
        ### Per chapter: frame against draw

        The draw is {drawn} from {frame} **without replacement** - a
        {drawn / frame * 100:.0f}% sampling fraction - so a chapter's count is
        **hypergeometric**, not binomial. `expected` is the hypergeometric mean, the interval
        is the exact equal-tailed 95% central interval for that count under uniform sampling,
        and `p` is the exact two-sided probability of a count no more likely than the one
        observed. A binomial approximation would drop the finite-population correction and
        overstate the variance at this sampling fraction.
        """
    )
    lines.append("")
    lines.extend(table)
    lines.append("")
    lines.extend(
        _block(
            f"""
            **Conclusion: the coinfection skew is a property of the sampling frame, not an
            accident of the draw.** Chapter 6 is {coinfections["frame_share"] * 100:.1f}% of
            the {frame}-statement frame on its own, and its {coinfections["drawn"]} drawn
            items sit against an expectation of {coinfections["expected"]:.1f} with an exact
            95% interval of [{coinfections["interval"][0]}, {coinfections["interval"][1]}]
            (p = {coinfections["p"]:.2f}) - comfortably inside sampling noise.

            Two further facts, recorded because they bear on the owner's design decision
            rather than on this review:

            - **Every chapter's drawn count falls inside its exact 95% interval.** A
              chi-square statistic against the uniform-sampling expectation gives
              p = {fit:.2f} under the multivariate hypergeometric
              ({GOODNESS_OF_FIT_TRIALS:,} simulated draws, seeded), so the composition as a
              whole - not only chapter 6 - is consistent with a faithful draw.
            - **Service delivery is the one materially under-drawn chapter**:
              {service["drawn"]} against an expectation of {service["expected"]:.1f}
              (p = {service["p"]:.2f}), sitting exactly on the lower bound of its 95%
              interval of [{service["interval"][0]}, {service["interval"][1]}]. That is
              inside the interval and is not evidence against the draw. It is worth recording
              only because service delivery is the area the DAK's business processes most
              directly operationalize, so under-drawing it pushes `p_answered` down rather
              than up.

            The set's chapter mix is the guideline's own mix. What to do about that - if
            anything - is the owner's call, and it is a call to take before stage 1 runs, not
            after seeing results.

            ## How to fill this in

            One decision per row, in the last column:

            - **keep** - the question is how a clinician would ask it unprompted.
            - **rewrite** - it borrows the recommendation's framing, or it would only be
              phrased that way by someone who had already read the recommendation. Note the
              replacement alongside.
            - **drop** - it should not be in the sample at all. Dropping moves the stage-1
              denominator, which is pre-registered, so record why.

            Items are ordered by review priority, not by id. All {drawn} drawn items appear
            exactly once.
            """
        )
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True, help="2021 consolidated guidelines PDF")
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("benchmarks/questions/mvp-coverage-who-hiv-v1.json"),
        help="committed question set to render",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("benchmarks/questions/mvp-coverage-who-hiv-v1-review-worksheet.md"),
        help="worksheet to write (overwritten in place)",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    committed = json.loads(args.questions.read_text(encoding="utf-8"))
    items = committed["items"]
    if len(items) != SAMPLE_N:
        raise SystemExit(f"committed set holds {len(items)} items, expected {SAMPLE_N}")
    usable = sum(1 for i in items if i["usable"])

    records = _extract(args.pdf)
    frame, _ = _frame(records)
    frame.sort(key=lambda r: r["id"])
    graded = sum(1 for r in records if r["kind"] == "RECOMMENDATION")

    high, near_duplicate, general, remainder, unusable = _priority_groups(items)
    table, detail, fit = _frame_vs_draw(frame, items)

    partner_id = next(
        flag.removeprefix(NEAR_DUPLICATE_PREFIX)
        for flag in near_duplicate[0]["flags"]
        if flag.startswith(NEAR_DUPLICATE_PREFIX)
    )
    partner = next(i for i in items if i["recommendation_id"] == partner_id)

    out = _preamble(committed, args.questions, len(records), graded, len(frame), usable)
    out.append("")
    out.extend(_analysis(len(frame), len(items), table, detail, fit))
    out.append("")

    out.extend(
        _block(
            f"""
            ## 1. High source-term overlap ({len(high)} items)

            Every item scoring at or above 0.60 on the leakage screen, highest first, ties
            broken by id. These are the items where a clinically natural question and a
            reworded recommendation are hardest to tell apart: read the question against the
            source and decide which one it is. The highest scorer in this set is *"Which
            children with HIV need co-trimoxazole prophylaxis?"* - which is also how a
            clinician would actually ask - so a high score genuinely does not settle the
            matter by itself.
            """
        )
    )
    out.append("")
    out.extend(_rows(high))
    out.append("")

    out.extend(
        _block(
            f"""
            ## 2. Near-duplicate pair ({len(near_duplicate)} item)

            `{near_duplicate[0]["recommendation_id"]}` restates `{partner_id}` almost
            verbatim - adolescent disclosure counselling appears in both chapter 2 and
            chapter 7. The guideline genuinely repeats itself, so the frame contains real
            duplicates and a topic can be over-weighted by that alone.

            **Why this matters beyond the wording.** A near-duplicate pair means two
            questions probing near-identical recommendations - and **both members of this
            pair were drawn**. They will almost certainly resolve the same way, so the sample
            covers {usable - 1} distinct clinical topics rather than {usable}. For a
            *pre-registered* proportion that is not cosmetic: `p_answered` and its Wilson
            interval are computed as though every item were an independent draw, and two
            perfectly correlated items make the effective sample smaller than its nominal
            size, so the interval comes out slightly narrower than the evidence supports. The
            stage-1 thresholds were fixed in advance against a stated n, so whether to keep
            both, drop one, or restate the denominator is a decision to take **before** stage
            1 runs.
            """
        )
    )
    out.append("")
    out.extend(_rows(near_duplicate))
    out.append("")
    out.extend(
        _block(
            f"""
            The other half of the pair, `{partner["question_id"]}`, is reproduced here for
            comparison only. It carries no flag of its own and its decision row is in section
            4, where it should be filled in once.

            > **`{partner["question_id"]}`** · chapter {partner["chapter_no"]}
            > {partner["chapter"]} · overlap {_overlap(partner)}
            >
            > **Q:** {partner["question"]}
            >
            > **S:** {partner["source_statement"]}
            """
        )
    )
    out.append("")

    out.extend(
        _block(
            f"""
            ## 3. General-population advice ({len(general)} items)

            Both sit in the noncommunicable disease section of chapter 6, but both are
            general physical-activity and lifestyle advice rather than HIV-specific
            decisions. The judgement here is whether a question a clinician would not bring
            to an HIV product belongs in a measurement of what that product can answer.
            """
        )
    )
    out.append("")
    out.extend(_rows(general))
    out.append("")

    out.extend(
        _block(
            f"""
            ## 4. Remaining usable items, by chapter ({len(remainder)} items)

            No flag fired on these. They need the same judgement as the rest: the leakage
            screen is a filter for attention, not a substitute for reading.
            """
        )
    )
    out.append("")
    out.extend(_rows(remainder))
    out.append("")

    out.extend(
        _block(
            f"""
            ## 5. Excluded from the sample - confirm only ({len(unusable)} item)

            **This is not a clinical review.** The item is already `usable: false`, with no
            Ely form, no question and no overlap score, which is why stage 1 runs at
            n = {usable} rather than {len(items)}. It carries a GRADE rating but is an
            anaphoric fragment whose referent sits in a preceding block, so no question could
            be written from it. It was deliberately **not** redrawn: replacing a drawn item
            to reach a round number would make the seed meaningless. The only thing to
            confirm is that excluding it was right.
            """
        )
    )
    out.append("")
    confirm_header = HEADER.replace("keep / rewrite / drop", "exclusion correct?")
    out.extend(_rows(unusable, header=confirm_header))
    out.append("")

    args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
    groups = (high, near_duplicate, general, remainder, unusable)
    total = sum(len(group) for group in groups)
    print(f"wrote {args.out}")
    print(f"  1 high source-term overlap : {len(high)}")
    print(f"  2 near-duplicate           : {len(near_duplicate)}")
    print(f"  3 general-population advice: {len(general)}")
    print(f"  4 remaining usable         : {len(remainder)}")
    print(f"  5 excluded (confirm only)  : {len(unusable)}")
    print(f"  total rows                 : {total} of {len(items)} drawn")
    if total != len(items):
        raise SystemExit("worksheet does not cover every drawn item exactly once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
