"""Every number the correctness measurement plan reports, computed from files on disk.

This is the frozen analysis code named in Section 1.4 of
[docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md). It is
deposited with that document before any label exists, so the estimators are fixed before
the data they run on. It computes the quantities of Sections 1.2, 3.5, 4.5, 4.6, 5.2,
6.2 and 7.3 from the run, label, judge, oracle and checker files, and it fails loudly on a
missing input rather than computing a section on a partial one.

## Sections and the inputs each needs

* `placeholders`: recompute every stage-2 count the plan quotes from production A and
  flag moves above 10%. Needs production A and the question set.
* `3.5`: noise floor A against B, the negative control, Q5 with the Tango interval and
  exact McNemar, the MDE tables. Needs production A, production B and the naive run;
  stage 2 is optional.
* `1.2`: Q1 to Q5. Needs production A, the question set and the labels; the naive run
  for Q5, the closed-book run for Q4 and the checker output for Q3.
* `4.6`: bucket distribution, agreement with its identification region, strata and
  subgroups. Needs production A, the question set and the labels; the oracle is optional.
* `4.5`: intra-rater re-test and inter-rater reliability. Needs the labels plus the
  re-test and/or second-reader label files.
* `5.2`: the entailment audit against the human labels. Needs production A, labels and
  the checker output.
* `6.2`: judge validity checks. Needs production A, labels and the judge outputs.
* `7.3`: the abstention estimators. Needs production A, labels and the oracle output.

Run `--sections` with any subset; the default is every section, which is the Horizon 2
invocation. Horizon 1 has runs and no labels, so it calls `--sections placeholders,3.5`.
`--write-mde PATH` writes the design-stage `mde.json` of Sections 3.5 and 5.2, which
depends on nothing unseen and is written before the naive file is opened.

## Input shapes the later scripts must write

The label file is the shape of Section 4.3 and Appendix B. Because Pass A labels three
arms for one question, non-production records are keyed `<question_id>:<arm>`; the
production census keeps the bare `question_id` key. The judge, oracle and checker files
are written by scripts that do not exist at deposit time, so their shapes are fixed here:

* **judge** (`judge_claims.py`): `{"judge", "seed", "check", "records": {key: {"arm",
  "claims": [{"index", "pair_attribution", "joint_attribution", "agreement",
  "eligibility_drop"}]}}, "miscitation": {key: {"claim_index", "swap_kind", "verdict"}}}`
  where `check` is one of `primary`, `stability`, `determinism`, `no_gold`,
  `miscitation`, and `key` follows the label-file convention.
* **oracle** (`abstention_oracle.py`): `{"judge", "shortlist_size", "records":
  {question_id: {"kind": "abstained" | "answered_control", "reason": ...,
  "cited_in_pre_union_rankings": bool | null, "cited_in_shortlist": bool | null,
  "verdict": "PRESENT" | "ABSENT", "evidence_id": ...}}}`.
* **checker** (`entailment_audit.py`): `{"instrument", "pairs": [{"question_id",
  "claim_index", "evidence_id", "score", "premise_kind"}], "claims": [{"question_id",
  "claim_index", "score", "lexical_score", "premise_mix"}]}` with `premise_mix` one of
  `prose`, `tabular`, `mixed`.

## Conventions, from Section 1.4

Two-sided 95% unless named one-sided. Wilson is the score interval without continuity
correction. A proportion observed at 0 or at its complement takes the exact one-sided
binomial bound. Bootstrap is 10,000 percentile resamples over records with seed 20260906.
Goodman's simultaneous intervals use the Bonferroni chi-square over k = 5 cells, with
Sison-Glaz as the sensitivity. Paired differences carry Tango's score interval with
Newcombe's as the cross-check. Proportions are reported to three decimals with n/N.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import functools
import hashlib
import json
import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[1]

SEED = 20260906
BOOTSTRAP_RESAMPLES = 10_000
Z_95 = 1.959963984540054
ALPHA = 0.05

ATTRIBUTION_LABELS = ("ATTRIBUTABLE", "EXTRAPOLATORY", "CONTRADICTORY", "NO_SUPPORT")
# The ordinal support gradient used for Gwet's AC2 weights: full support, partial support,
# no bearing, the opposite. Stated here because a weight scheme chosen after the labels
# are seen is a degree of freedom.
ATTRIBUTION_ORDER = ("ATTRIBUTABLE", "EXTRAPOLATORY", "NO_SUPPORT", "CONTRADICTORY")
AGREEMENT_LABELS = ("AGREES", "PARTIAL", "DISAGREES", "NOT_ADJUDICABLE")
UNSUPPORTED = frozenset({"CONTRADICTORY", "NO_SUPPORT"})
NON_ATTRIBUTABLE = frozenset({"EXTRAPOLATORY", "CONTRADICTORY", "NO_SUPPORT"})
BUCKETS = (
    "ANSWERED_CORRECT",
    "ANSWERED_DEFECTIVE",
    "ANSWERED_WRONG",
    "ABSTAINED_CORRECT",
    "ABSTAINED_AVOIDABLE",
)
PREMISE_KINDS = (
    "NARRATIVE",
    "DECISION_RULE",
    "SCHEDULE_ENTRY",
    "DATA_DICTIONARY_ENTRY",
    "INDICATOR_DEFINITION",
)
PROSE_KINDS = frozenset({"NARRATIVE"})


def is_prose(kind: str | None) -> bool:
    """Every narrative kind is prose; every table-derived kind is tabular."""

    return bool(kind) and str(kind).upper().startswith("NARRATIVE")


ELIGIBILITY_CONDITIONS = (
    "PREGNANCY_OR_BREASTFEEDING",
    "TB_OR_CRYPTOCOCCAL_COINFECTION",
    "WEIGHT_OR_AGE_BAND",
    "RENAL_OR_HEPATIC_FUNCTION",
    "PRIOR_ART_EXPOSURE_OR_TREATMENT_LINE",
    "CD4_OR_VIRAL_LOAD_THRESHOLD",
    "SETTING_LEVEL_EPIDEMIOLOGY",
)
TRIGGER_CLASSES = (
    "joint_attribution_non_attributable",
    "pair_attribution_non_attributable",
    "agreement_disagrees_or_partial",
    "eligibility_drop",
    "presentation_defect",
)

# Section 3.5: the stage-1 production-versus-naive anchor at fixed gemini-3.7-flash, and
# the study size. Error-corrected (2 of 48) and uncorrected (3 of 49).
MDE_ANCHORS = {"corrected": (2, 48), "uncorrected": (3, 49)}
MDE_N = 164
MDE_DELTAS = (0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10)
MDE_POWER_TARGET = 0.80

# The stage-2 descriptives the plan quotes as placeholders. Every one is recomputed from
# production A and a move above 10% is reported for entry in Section 11.
STAGE2_PLACEHOLDERS: dict[str, float] = {
    "questions": 164,
    "answered": 73,
    "abstained_model_declared": 77,
    "abstained_gate_blocked": 14,
    "claims": 190,
    "pairs": 316,
    "distinct_cited_ids": 125,
    "multi_citation_claims": 83,
    "draw_questions": 149,
    "draw_answered": 69,
    "draw_claims": 178,
    "draw_pairs": 299,
    "premise_NARRATIVE": 128,
    "premise_DECISION_RULE": 64,
    "premise_SCHEDULE_ENTRY": 63,
    "premise_DATA_DICTIONARY_ENTRY": 38,
    "premise_INDICATOR_DEFINITION": 23,
    "chapter_2_answered": 19,
    "chapter_3_answered": 8,
    "chapter_4_answered": 9,
    "chapter_5_answered": 4,
    "chapter_6_answered": 21,
    "chapter_7_answered": 12,
    "high_overlap_answered": 7,
}
PLACEHOLDER_MOVE_TOLERANCE = 0.10


class MissingInput(RuntimeError):
    """A section was asked for and an input it needs is absent."""


# --------------------------------------------------------------------------------------
# Interval and test primitives
# --------------------------------------------------------------------------------------


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Score interval without continuity correction, as `run_mvp_coverage_stage1.py` has it."""

    if total == 0:
        return (0.0, 1.0)
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z
        / denominator
        * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total))
    )
    return (max(0.0, center - spread), min(1.0, center + spread))


def zero_occurrence_upper_bound(total: int, alpha: float = ALPHA) -> float:
    """Exact one-sided upper bound on a rate observed zero times in `total` trials."""

    if total <= 0:
        return 1.0
    return 1.0 - alpha ** (1.0 / total)


def exact_one_sided_bounds(successes: int, total: int, alpha: float = ALPHA) -> dict[str, float]:
    """Clopper-Pearson one-sided limits: the exact bound the plan uses at 0 or at n."""

    if total <= 0:
        return {"lower": 0.0, "upper": 1.0}
    lower = (
        0.0 if successes == 0 else float(stats.beta.ppf(alpha, successes, total - successes + 1))
    )
    upper = (
        1.0
        if successes == total
        else float(stats.beta.ppf(1 - alpha, successes + 1, total - successes))
    )
    return {"lower": lower, "upper": upper}


def proportion_report(successes: int, total: int) -> dict[str, Any]:
    """A proportion with n/N, the Wilson interval, and the exact bound at the boundary.

    Where the count is 0 or its complement, the reported limit on the empty side is the
    exact one-sided bound rather than the Wilson limit (Section 1.4).
    """

    if total == 0:
        return {"count": 0, "total": 0, "rate": None, "interval_95": None, "method": None}
    lower, upper = wilson_interval(successes, total)
    method = "wilson"
    if successes == 0:
        upper = zero_occurrence_upper_bound(total)
        method = "wilson lower, exact one-sided upper"
    elif successes == total:
        lower = 1.0 - zero_occurrence_upper_bound(total)
        method = "exact one-sided lower, wilson upper"
    return {
        "count": successes,
        "total": total,
        "rate": round(successes / total, 3),
        "interval_95": [round(lower, 3), round(upper, 3)],
        "method": method,
    }


def goodman_intervals(counts: Sequence[int], alpha: float = ALPHA) -> list[tuple[float, float]]:
    """Goodman (1965) simultaneous intervals with the Bonferroni chi-square over k cells."""

    k = len(counts)
    total = sum(counts)
    if total == 0:
        return [(0.0, 1.0)] * k
    critical = float(stats.chi2.ppf(1 - alpha / k, 1))
    intervals = []
    for count in counts:
        root = math.sqrt(critical * (critical + 4 * count * (total - count) / total))
        lower = (critical + 2 * count - root) / (2 * (total + critical))
        upper = (critical + 2 * count + root) / (2 * (total + critical))
        intervals.append((max(0.0, lower), min(1.0, upper)))
    return intervals


def _sison_glaz_moments(c: int, lam: float) -> tuple[float, float, float, float, float]:
    a = lam + c
    b = max(lam - c, 0.0)
    if b > 0:
        den = stats.poisson.cdf(a, lam) - stats.poisson.cdf(b - 1, lam)
    else:
        den = stats.poisson.cdf(a, lam)
    mu = [0.0] * 5
    for r in range(1, 5):
        pois_a = (
            stats.poisson.cdf(a, lam) - stats.poisson.cdf(a - r, lam)
            if a - r >= 0
            else stats.poisson.cdf(a, lam)
        )
        if b - r - 1 >= 0:
            pois_b = stats.poisson.cdf(b - 1, lam) - stats.poisson.cdf(b - r - 1, lam)
        elif b - 1 >= 0:
            pois_b = stats.poisson.cdf(b - 1, lam)
        else:
            pois_b = 0.0
        mu[r] = (lam**r) * (1 - (pois_a - pois_b) / den)
    m1 = mu[1]
    m2 = mu[2] + mu[1] - mu[1] ** 2
    m3 = mu[3] + mu[2] * (3 - 3 * mu[1]) + (mu[1] - 3 * mu[1] ** 2 + 2 * mu[1] ** 3)
    m4 = (
        mu[4]
        + mu[3] * (6 - 4 * mu[1])
        + mu[2] * (7 - 12 * mu[1] + 6 * mu[1] ** 2)
        + mu[1]
        - 4 * mu[1] ** 2
        + 6 * mu[1] ** 3
        - 3 * mu[1] ** 4
    )
    return m1, m2, m3, m4, float(den)


def _sison_glaz_probability(c: int, counts: Sequence[int]) -> float:
    n = sum(counts)
    rows = [_sison_glaz_moments(c, float(x)) for x in counts]
    s1 = sum(row[0] for row in rows)
    s2 = sum(row[1] for row in rows)
    s3 = sum(row[2] for row in rows)
    s4 = sum(row[3] - 3 * row[1] ** 2 for row in rows)
    probn = 1.0 / (stats.poisson.cdf(n, n) - stats.poisson.cdf(n - 1, n))
    z = (n - s1) / math.sqrt(s2)
    g1 = s3 / (s2**1.5)
    g2 = s4 / (s2**2)
    poly = (
        1
        + g1 * (z**3 - 3 * z) / 6
        + g2 * (z**4 - 6 * z**2 + 3) / 24
        + g1**2 * (z**6 - 15 * z**4 + 45 * z**2 - 15) / 72
    )
    f = poly * math.exp(-(z**2) / 2) / math.sqrt(2 * math.pi)
    probx = 1.0
    for row in rows:
        probx *= row[4]
    return probn * probx * f / math.sqrt(s2)


def sison_glaz_intervals(counts: Sequence[int], alpha: float = ALPHA) -> list[tuple[float, float]]:
    """Sison and Glaz (1995) simultaneous intervals, the port of DescTools' MultinomCI."""

    n = sum(counts)
    k = len(counts)
    if n == 0:
        return [(0.0, 1.0)] * k
    c_found = 0
    p_old = 0.0
    p_new = 0.0
    for cc in range(1, n + 1):
        p_new = _sison_glaz_probability(cc, counts)
        if p_new > 1 - alpha and p_old < 1 - alpha:
            c_found = cc
            break
        p_old = p_new
    delta = (1 - alpha - p_old) / (p_new - p_old) if p_new != p_old else 0.0
    intervals = []
    for count in counts:
        lower = max(0.0, count / n - c_found / n)
        upper = min(1.0, count / n + c_found / n + 2 * float(delta) / n)
        intervals.append((float(lower), float(upper)))
    return intervals


def _tango_restricted_p21(b: int, c: int, n: int, delta: float) -> float:
    linear = (b + c) - delta * (2 * n - b + c)
    return (linear + math.sqrt(linear**2 + 8 * n * c * delta * (1 - delta))) / (4 * n)


def _tango_score(b: int, c: int, n: int, delta: float) -> float:
    numerator = b - c - n * delta
    variance = n * (2 * _tango_restricted_p21(b, c, n, delta) + delta - delta**2)
    if variance <= 0:
        # Only at the point estimate with no discordance: the score is 0/0 there and
        # the statistic is zero by continuity, not undefined.
        if numerator == 0:
            return 0.0
        return math.inf if numerator > 0 else -math.inf
    return numerator / math.sqrt(variance)


def tango_interval(b: int, c: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Tango (1998) score interval for the paired difference p_b - p_c.

    `b` is the count of pairs where the first outcome is a success and the second is not,
    `c` the reverse, `n` the number of pairs. The limits are the roots of the score
    statistic at plus and minus `z`, found by bisection because the statistic is monotone
    in the difference.
    """

    if n == 0:
        return (-1.0, 1.0)

    def solve(target: float) -> float:
        low, high = -1.0 + 1e-12, 1.0 - 1e-12
        for _ in range(200):
            mid = (low + high) / 2
            if _tango_score(b, c, n, mid) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    return (solve(z), solve(-z))


def newcombe_paired_interval(
    a: int, b: int, c: int, d: int, z: float = Z_95
) -> tuple[float, float]:
    """Newcombe (1998) method 10 for a paired difference: the cross-check on Tango."""

    n = a + b + c + d
    if n == 0:
        return (-1.0, 1.0)
    p1 = (a + b) / n
    p2 = (a + c) / n
    l1, u1 = wilson_interval(a + b, n, z)
    l2, u2 = wilson_interval(a + c, n, z)
    margins = (a + b) * (c + d) * (a + c) * (b + d)
    if margins == 0:
        phi = 0.0
    else:
        numerator = a * d - b * c
        if numerator > 0:
            numerator = max(numerator - n / 2, 0)
        phi = numerator / math.sqrt(margins)
    theta = p1 - p2
    lower = theta - math.sqrt(
        max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2)
    )
    upper = theta + math.sqrt(
        max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2)
    )
    return (max(-1.0, lower), min(1.0, upper))


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p, as `statsmodels...mcnemar(exact=True)` computes it."""

    from statsmodels.stats.contingency_tables import mcnemar

    if b + c == 0:
        return 1.0
    return float(mcnemar([[0, b], [c, 0]], exact=True).pvalue)


def paired_comparison(
    outcomes_first: Sequence[bool], outcomes_second: Sequence[bool]
) -> dict[str, Any]:
    """The 2x2, the paired difference with Tango and Newcombe intervals, and exact McNemar."""

    if len(outcomes_first) != len(outcomes_second):
        raise ValueError("paired outcomes have different lengths")
    pairs = list(zip(outcomes_first, outcomes_second, strict=True))
    a = sum(1 for x, y in pairs if x and y)
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if not x and y)
    d = sum(1 for x, y in pairs if not x and not y)
    n = a + b + c + d
    tango = tango_interval(b, c, n)
    newcombe = newcombe_paired_interval(a, b, c, d)
    return {
        "n": n,
        "table": {"both": a, "first_only": b, "second_only": c, "neither": d},
        "rate_first": round((a + b) / n, 3) if n else None,
        "rate_second": round((a + c) / n, 3) if n else None,
        "difference": round((b - c) / n, 3) if n else None,
        "tango_95": [round(tango[0], 3), round(tango[1], 3)],
        "newcombe_95": [round(newcombe[0], 3), round(newcombe[1], 3)],
        "mcnemar_exact_p": round(mcnemar_exact_p(b, c), 4),
        "discordant": b + c,
    }


def clustered_bootstrap(
    clusters: Sequence[Sequence[Any]],
    statistic: Callable[[list[Any]], float | None],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Percentile interval over resampled records; items inside a record travel together."""

    rng = np.random.default_rng(seed)
    count = len(clusters)
    if count == 0:
        return {"interval_95": None, "resamples": resamples, "undefined_resamples": resamples}
    values: list[float] = []
    failed = 0
    for _ in range(resamples):
        draw = rng.integers(0, count, size=count)
        pooled: list[Any] = []
        for index in draw:
            pooled.extend(clusters[index])
        value = statistic(pooled)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            failed += 1
            continue
        values.append(value)
    if not values:
        return {"interval_95": None, "resamples": resamples, "undefined_resamples": failed}
    lower, upper = np.percentile(values, [2.5, 97.5])
    return {
        "interval_95": [round(float(lower), 3), round(float(upper), 3)],
        "resamples": resamples,
        "undefined_resamples": failed,
        "seed": seed,
    }


def rate_or_none(items: Sequence[Any], predicate: Callable[[Any], bool]) -> float | None:
    if not items:
        return None
    return sum(1 for item in items if predicate(item)) / len(items)


def auc_mann_whitney(positive: Sequence[float], negative: Sequence[float]) -> float | None:
    """AUC as U/(n_pos n_neg): the Mann-Whitney identity, ties counted half."""

    if not positive or not negative:
        return None
    result = stats.mannwhitneyu(positive, negative, alternative="two-sided")
    return float(result.statistic) / (len(positive) * len(negative))


# --------------------------------------------------------------------------------------
# Agreement coefficients (Sections 4.5, 6.2, 7.3)
# --------------------------------------------------------------------------------------


def _ordinal_weights(categories: Sequence[str]) -> dict[tuple[str, str], float]:
    q = len(categories)
    position = {label: index for index, label in enumerate(categories)}

    def pairs_between(k: int, m: int) -> float:
        distance = abs(k - m)
        return distance * (distance + 1) / 2

    maximum = pairs_between(0, q - 1)
    return {
        (left, right): 1.0 - pairs_between(position[left], position[right]) / maximum
        for left in categories
        for right in categories
    }


def agreement_coefficients(
    pairs: Sequence[tuple[str, str]],
    categories: Sequence[str],
    *,
    ordinal: bool = False,
) -> dict[str, Any]:
    """Percent agreement, Cohen's kappa, Gwet's AC1 (or ordinal AC2) and PABAK.

    Gwet's coefficients are primary because they do not collapse under the skewed
    marginals expected here; PABAK is a rescaling of observed agreement, reported as such.
    """

    n = len(pairs)
    q = len(categories)
    if n == 0:
        return {"n": 0}
    weights = (
        _ordinal_weights(categories)
        if ordinal
        else {(x, y): 1.0 if x == y else 0.0 for x in categories for y in categories}
    )
    joint = collections.Counter(pairs)
    marginal_a = collections.Counter(left for left, _ in pairs)
    marginal_b = collections.Counter(right for _, right in pairs)
    observed_exact = sum(1 for left, right in pairs if left == right) / n
    observed_weighted = (
        sum(weights[(left, right)] * count for (left, right), count in joint.items()) / n
    )
    pi = {label: (marginal_a[label] + marginal_b[label]) / (2 * n) for label in categories}
    total_weight = sum(weights.values())
    gwet_expected = total_weight / (q * (q - 1)) * sum(p * (1 - p) for p in pi.values())
    gwet = (observed_weighted - gwet_expected) / (1 - gwet_expected) if gwet_expected < 1 else None
    kappa_expected = sum(
        weights[(x, y)] * (marginal_a[x] / n) * (marginal_b[y] / n)
        for x in categories
        for y in categories
    )
    kappa = (
        (observed_weighted - kappa_expected) / (1 - kappa_expected) if kappa_expected < 1 else None
    )
    pabak = (q * observed_exact - 1) / (q - 1)
    confusion = {
        left: {right: joint.get((left, right), 0) for right in categories} for left in categories
    }
    gwet_key = "gwet_ac2_ordinal" if ordinal else "gwet_ac1"
    return {
        "n": n,
        "percent_agreement": round(observed_exact, 3),
        "cohen_kappa": round(kappa, 3) if kappa is not None else None,
        gwet_key: round(gwet, 3) if gwet is not None else None,
        "pabak": round(pabak, 3),
        "confusion": confusion,
    }


def bootstrapped_agreement(
    clusters: Sequence[Sequence[tuple[str, str]]],
    categories: Sequence[str],
    *,
    ordinal: bool = False,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    pooled = [pair for cluster in clusters for pair in cluster]
    point = agreement_coefficients(pooled, categories, ordinal=ordinal)
    if not pooled:
        return point
    gwet_key = "gwet_ac2_ordinal" if ordinal else "gwet_ac1"
    intervals = {}
    for key in ("percent_agreement", "cohen_kappa", gwet_key, "pabak"):
        intervals[key] = clustered_bootstrap(
            clusters,
            lambda items, key=key: agreement_coefficients(items, categories, ordinal=ordinal).get(
                key
            ),
            resamples=resamples,
            seed=seed,
        )["interval_95"]
    point["intervals_95"] = intervals
    point["disagreements"] = sum(1 for left, right in pooled if left != right)
    return point


# --------------------------------------------------------------------------------------
# Minimum detectable effect (Section 3.5) and design half-widths (Section 5.2)
# --------------------------------------------------------------------------------------


@functools.cache
def _mcnemar_rejects(b: int, c: int, alpha: float = ALPHA) -> bool:
    m = b + c
    if m == 0:
        return False
    p = min(1.0, 2 * float(stats.binom.cdf(min(b, c), m, 0.5)))
    return p <= alpha


def mcnemar_power(p_b: float, p_c: float, n: int, alpha: float = ALPHA) -> float:
    """Power of the exact two-sided McNemar test, enumerating the trinomial over (b, c)."""

    from scipy.special import gammaln

    if p_b < 0 or p_c < 0 or p_b + p_c > 1:
        raise ValueError("discordant probabilities must be non-negative and sum to at most 1")
    p_a = 1 - p_b - p_c
    logs = {
        "b": math.log(p_b) if p_b > 0 else -math.inf,
        "c": math.log(p_c) if p_c > 0 else -math.inf,
        "a": math.log(p_a) if p_a > 0 else -math.inf,
    }
    power = 0.0
    for b in range(n + 1):
        for c in range(n - b + 1):
            if not _mcnemar_rejects(b, c, alpha):
                continue
            a = n - b - c
            log_prob = gammaln(n + 1) - gammaln(b + 1) - gammaln(c + 1) - gammaln(a + 1)
            for count, log_p in ((b, logs["b"]), (c, logs["c"]), (a, logs["a"])):
                if count > 0:
                    if log_p == -math.inf:
                        log_prob = -math.inf
                        break
                    log_prob += count * log_p
            if log_prob > -math.inf:
                power += math.exp(log_prob)
    return power


def mde_tables(n: int = MDE_N, deltas: Sequence[float] = MDE_DELTAS) -> dict[str, Any]:
    """The primary and secondary parameterisations of Section 3.5, at both anchors."""

    tables: dict[str, Any] = {"n": n, "alpha": ALPHA, "power_target": MDE_POWER_TARGET}
    for name, (discordant, trials) in MDE_ANCHORS.items():
        pi0 = discordant / trials
        primary = {}
        for delta in deltas:
            primary[f"{delta:.2f}"] = round(mcnemar_power(pi0 / 2 + delta, pi0 / 2, n), 3)
        mde = next((delta for delta in deltas if primary[f"{delta:.2f}"] >= MDE_POWER_TARGET), None)
        tables[name] = {
            "anchor": {"discordant": discordant, "trials": trials, "pi0": round(pi0, 4)},
            "primary": {
                "parameterisation": (
                    "p_b = pi0/2 + delta, p_c = pi0/2, trinomial enumerated unconditionally"
                ),
                "power_by_delta": primary,
                "mde_at_80_percent": mde,
            },
        }
        if name == "uncorrected":
            secondary = {}
            for delta in deltas:
                if delta > pi0:
                    secondary[f"{delta:.2f}"] = None
                    continue
                secondary[f"{delta:.2f}"] = round(
                    mcnemar_power((pi0 + delta) / 2, (pi0 - delta) / 2, n), 3
                )
            tables[name]["secondary"] = {
                "parameterisation": (
                    "p_b + p_c fixed at pi0, only the direction varies; caps delta at pi0 "
                    "and drives p_c toward zero, which is the source of its optimism"
                ),
                "power_by_delta": secondary,
                "mde_at_80_percent": next(
                    (
                        delta
                        for delta in deltas
                        if secondary.get(f"{delta:.2f}") is not None
                        and secondary[f"{delta:.2f}"] >= MDE_POWER_TARGET
                    ),
                    None,
                ),
            }
    return tables


def expected_half_widths(
    *,
    claims: int,
    answered_records: int,
    mean_claims_per_record: float,
    unsupported_rates: Sequence[float] = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35),
    design_effect: float = 1.5,
) -> dict[str, Any]:
    """Design-stage half-widths for Q2 and the Q3 sensitivity across a prior grid.

    Q2's half-width is the Wilson half-width at the claim count deflated by a stated
    design effect for within-record clustering. Q3's is the Wilson half-width at the
    number of non-attributable claims the rate implies, for a sensitivity of 0.75. Both
    are reference figures so an uninformative Q3 is a pre-stated outcome, not a surprise.
    """

    effective_claims = claims / design_effect
    grid = {}
    for rate in unsupported_rates:
        q2_successes = int(round(rate * effective_claims))
        lower, upper = wilson_interval(q2_successes, int(round(effective_claims)))
        positives = max(1, int(round(rate * claims)))
        q3_lower, q3_upper = wilson_interval(int(round(0.75 * positives)), positives)
        grid[f"{rate:.2f}"] = {
            "q2_half_width": round((upper - lower) / 2, 3),
            "expected_non_attributable_claims": positives,
            "q3_sensitivity_half_width_at_0.75": round((q3_upper - q3_lower) / 2, 3),
        }
    return {
        "claims": claims,
        "answered_records": answered_records,
        "mean_claims_per_record": round(mean_claims_per_record, 2),
        "design_effect_assumed": design_effect,
        "grid": grid,
    }


# --------------------------------------------------------------------------------------
# Stratified and prediction-assisted estimators (Section 7.3)
# --------------------------------------------------------------------------------------


def stratified_presence(
    *,
    gate_blocked_present: int,
    gate_blocked_total: int,
    sample_present: int,
    sample_size: int,
    model_declared_total: int,
) -> dict[str, Any]:
    """p = (G pG + M pm)/(G + M) with the finite-population correction on the sampled term."""

    g_total, m_total, m = gate_blocked_total, model_declared_total, sample_size
    if g_total + m_total == 0:
        return {"estimate": None}
    p_gate = gate_blocked_present / g_total if g_total else 0.0
    p_sample = sample_present / m if m else 0.0
    fpc = (1 - m / m_total) if m_total else 0.0
    variance = (
        (m_total / (g_total + m_total)) ** 2
        * (p_sample * (1 - p_sample) / (m - 1) if m > 1 else 0.0)
        * fpc
    )
    estimate = (g_total * p_gate + m_total * p_sample) / (g_total + m_total)

    def rescale(limits: tuple[float, float]) -> list[float]:
        return [
            round((g_total * p_gate + m_total * limits[0]) / (g_total + m_total), 3),
            round((g_total * p_gate + m_total * limits[1]) / (g_total + m_total), 3),
        ]

    wilson_plain = wilson_interval(sample_present, m) if m else (0.0, 1.0)
    effective = m / fpc if fpc > 0 else m
    wilson_fpc = (
        wilson_interval(int(round(p_sample * effective)), int(round(effective)))
        if m
        else (0.0, 1.0)
    )
    return {
        "gate_blocked": {
            "present": gate_blocked_present,
            "total": g_total,
            "rate": round(p_gate, 3),
        },
        "model_declared": {
            "total": m_total,
            "sampled": m,
            "sampled_present": sample_present,
            "sample_rate": round(p_sample, 3),
        },
        "estimate": round(estimate, 3),
        "variance": variance,
        "standard_error": round(math.sqrt(variance), 4),
        "interval_95_rescaled_wilson": rescale(wilson_plain),
        "interval_95_rescaled_wilson_fpc": rescale(wilson_fpc),
        "fpc": round(fpc, 3),
    }


def difference_estimator(
    *,
    judge_rate_over_stratum: float,
    stratum_size: int,
    sample_human: Sequence[bool],
    sample_judge: Sequence[bool],
) -> dict[str, Any]:
    """Design-based correction of the judge's rate by the sampled human-judge discrepancy."""

    if len(sample_human) != len(sample_judge) or not sample_human:
        return {"estimate": None}
    m = len(sample_human)
    differences = [int(h) - int(j) for h, j in zip(sample_human, sample_judge, strict=True)]
    mean_difference = sum(differences) / m
    variance_d = sum((d - mean_difference) ** 2 for d in differences) / (m - 1) if m > 1 else 0.0
    fpc = 1 - m / stratum_size if stratum_size else 0.0
    variance = variance_d / m * fpc
    estimate = judge_rate_over_stratum + mean_difference
    half = Z_95 * math.sqrt(variance)
    return {
        "judge_rate": round(judge_rate_over_stratum, 3),
        "mean_discrepancy": round(mean_difference, 3),
        "estimate": round(estimate, 3),
        "interval_95": [
            round(max(0.0, estimate - half), 3),
            round(min(1.0, estimate + half), 3),
        ],
        "sample_size": m,
        "stratum_size": stratum_size,
    }


def verbosity_reference(per_claim_rate: float, claim_counts: Sequence[int]) -> float | None:
    """1 - (1 - p)^k averaged over observed k: the independence reference, not a prediction."""

    if not claim_counts:
        return None
    return sum(1 - (1 - per_claim_rate) ** k for k in claim_counts) / len(claim_counts)


# --------------------------------------------------------------------------------------
# Run-file readers and label derivations (Sections 1.1, 4.4)
# --------------------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def outcome_of(record: dict[str, Any]) -> str:
    """ANSWERED, ABSTAINED or ERROR, with a legacy quota failure read as an error."""

    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    if generation.get("abstained"):
        return "ABSTAINED"
    return "ANSWERED"


def abstention_reason(record: dict[str, Any]) -> str | None:
    generation = record.get("generation")
    if generation is None:
        return record.get("gate_reason") or "GATE"
    if generation.get("abstained"):
        return generation.get("reason_code")
    return None


def claims_of(record: dict[str, Any]) -> list[dict[str, Any]]:
    if outcome_of(record) != "ANSWERED":
        return []
    return list((record.get("generation") or {}).get("claims") or [])


def records_by_id(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["question_id"]: record for record in run["results"]}


def passage_kinds(record: dict[str, Any]) -> dict[str, str]:
    return {
        passage["evidence_id"]: str(passage.get("kind") or "").upper()
        for passage in (record.get("retrieval") or {}).get("passages") or []
    }


def draw_ids(questions: dict[str, Any]) -> set[str]:
    return {
        item["question_id"]
        for item in questions["items"]
        if item.get("in_preregistered_150") and item.get("usable", True)
    }


def question_index(questions: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["question_id"]: item for item in questions["items"]}


def label_key(question_id: str, arm: str) -> str:
    return question_id if arm == "production" else f"{question_id}:{arm}"


def label_records(labels: dict[str, Any], arm: str) -> dict[str, dict[str, Any]]:
    """Records of one arm keyed by bare question_id, whatever key convention was used."""

    out: dict[str, dict[str, Any]] = {}
    for key, record in (labels.get("records") or {}).items():
        record_arm = record.get("arm") or "production"
        question_id = key.split(":", 1)[0]
        if record_arm == arm:
            out[question_id] = record
    return out


def claim_support(claim_label: dict[str, Any]) -> str:
    """Section 4.4: UNSUPPORTED, PARTIAL, SUPPORTED or UNLABELABLE from the labels."""

    joint = claim_label.get("joint_attribution")
    if joint == "UNLABELABLE" or joint is None:
        return "UNLABELABLE"
    if joint in UNSUPPORTED:
        return "UNSUPPORTED"
    pairs = (claim_label.get("pair_attribution") or {}).values()
    if joint == "EXTRAPOLATORY" or any(label in UNSUPPORTED for label in pairs):
        return "PARTIAL"
    return "SUPPORTED"


def answered_bucket(record_label: dict[str, Any]) -> str | None:
    supports = [claim_support(claim) for claim in record_label.get("claims") or []]
    if not supports or "UNLABELABLE" in supports:
        return None
    if "UNSUPPORTED" in supports:
        return "ANSWERED_WRONG"
    defective = (
        "PARTIAL" in supports
        or bool(record_label.get("presentation_defect"))
        or any(bool(claim.get("eligibility_drop")) for claim in record_label["claims"])
    )
    return "ANSWERED_DEFECTIVE" if defective else "ANSWERED_CORRECT"


def claim_is_flagged(claim_label: dict[str, Any], record_label: dict[str, Any]) -> list[str]:
    """The five trigger classes of Section 1.1 that hold for a claim, in a fixed order."""

    triggers = []
    if claim_label.get("joint_attribution") in NON_ATTRIBUTABLE:
        triggers.append(TRIGGER_CLASSES[0])
    if any(
        label in NON_ATTRIBUTABLE for label in (claim_label.get("pair_attribution") or {}).values()
    ):
        triggers.append(TRIGGER_CLASSES[1])
    if claim_label.get("agreement") in {"DISAGREES", "PARTIAL"}:
        triggers.append(TRIGGER_CLASSES[2])
    if claim_label.get("eligibility_drop"):
        triggers.append(TRIGGER_CLASSES[3])
    if record_label.get("presentation_defect"):
        triggers.append(TRIGGER_CLASSES[4])
    return triggers


def human_presence(labels: dict[str, Any], question_id: str) -> bool | None:
    """Retrievable presence from the human abstention adjudications, or None if unread."""

    gate = (labels.get("gate_blocked") or {}).get(question_id)
    if isinstance(gate, dict) and "dak_has_answer" in gate:
        return bool(gate["dak_has_answer"])
    sample = (labels.get("abstained_sample") or {}).get(question_id)
    if isinstance(sample, dict) and "any_retrieved_passage_answers" in sample:
        return bool(sample["any_retrieved_passage_answers"])
    return None


def derive_buckets(
    run: dict[str, Any],
    labels: dict[str, Any],
    oracle: dict[str, Any] | None,
    *,
    oracle_cleared_floor: bool,
) -> dict[str, Any]:
    """Section 4.4 applied to every record of the run; None where the plan withholds."""

    production = label_records(labels, "production")
    buckets: dict[str, str | None] = {}
    sources: dict[str, str] = {}
    for record in run["results"]:
        question_id = record["question_id"]
        outcome = outcome_of(record)
        if outcome == "ERROR":
            buckets[question_id] = None
            sources[question_id] = "error record: missing measurement"
            continue
        if outcome == "ANSWERED":
            label = production.get(question_id)
            buckets[question_id] = answered_bucket(label) if label else None
            sources[question_id] = "human" if label else "unlabelled"
            continue
        presence = human_presence(labels, question_id)
        if presence is not None:
            buckets[question_id] = "ABSTAINED_AVOIDABLE" if presence else "ABSTAINED_CORRECT"
            sources[question_id] = "human"
            continue
        verdict = ((oracle or {}).get("records") or {}).get(question_id, {}).get("verdict")
        if verdict in {"PRESENT", "ABSENT"} and oracle_cleared_floor:
            buckets[question_id] = (
                "ABSTAINED_AVOIDABLE" if verdict == "PRESENT" else "ABSTAINED_CORRECT"
            )
            sources[question_id] = "judge"
        else:
            buckets[question_id] = None
            sources[question_id] = "withheld: no human read and the oracle floor not cleared"
    return {"buckets": buckets, "sources": sources}


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------


class Inputs:
    """Loaded inputs, with a loud failure the first time a missing one is asked for."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        self._arguments = arguments
        self._cache: dict[str, Any] = {}
        self.provenance: dict[str, Any] = {}

    def path(self, name: str) -> Path | None:
        return getattr(self._arguments, name, None)

    def has(self, name: str) -> bool:
        path = self.path(name)
        return path is not None and Path(path).exists()

    def get(self, name: str, *, section: str) -> Any:
        if name in self._cache:
            return self._cache[name]
        path = self.path(name)
        if path is None or not Path(path).exists():
            raise MissingInput(
                f"section {section} needs --{name.replace('_', '-')}"
                + (f" ({path} does not exist)" if path else " (not given)")
            )
        path = Path(path)
        if name == "judge":
            documents = [load_json(Path(item)) for item in path]  # type: ignore[union-attr]
            self.provenance[name] = [
                {"path": str(item), "sha256": sha256_of(Path(item))}
                for item in path  # type: ignore[union-attr]
            ]
            self._cache[name] = documents
            return documents
        document = load_json(path)
        self.provenance[name] = {"path": str(path), "sha256": sha256_of(path)}
        self._cache[name] = document
        return document

    def optional(self, name: str) -> Any | None:
        return self.get(name, section="optional") if self.has(name) else None


def section_placeholders(inputs: Inputs) -> dict[str, Any]:
    run = inputs.get("production_a", section="placeholders")
    questions = inputs.get("questions", section="placeholders")
    index = question_index(questions)
    draw = draw_ids(questions)
    counts: dict[str, float] = collections.Counter()
    premise = collections.Counter()
    chapter_answered: collections.Counter = collections.Counter()
    chapter_total: collections.Counter = collections.Counter()
    ely_answered: collections.Counter = collections.Counter()
    ely_total: collections.Counter = collections.Counter()
    cited_ids: set[str] = set()
    citations_per_claim: collections.Counter = collections.Counter()
    errors = collections.Counter()
    for record in run["results"]:
        question_id = record["question_id"]
        item = index.get(question_id, {})
        outcome = outcome_of(record)
        counts["questions"] += 1
        chapter_total[record.get("chapter_no")] += 1
        ely_total[record.get("ely_form_no")] += 1
        if question_id in draw:
            counts["draw_questions"] += 1
        if outcome == "ERROR":
            errors[str((record.get("generation") or {}).get("error_class") or "UNCLASSIFIED")] += 1
            continue
        if outcome == "ABSTAINED":
            if record.get("generation") is None:
                counts["abstained_gate_blocked"] += 1
            else:
                counts["abstained_model_declared"] += 1
            continue
        counts["answered"] += 1
        chapter_answered[record.get("chapter_no")] += 1
        ely_answered[record.get("ely_form_no")] += 1
        if "REVIEW_HIGH_SOURCE_OVERLAP" in (item.get("flags") or []):
            counts["high_overlap_answered"] += 1
        kinds = passage_kinds(record)
        claims = claims_of(record)
        counts["claims"] += len(claims)
        if question_id in draw:
            counts["draw_answered"] += 1
            counts["draw_claims"] += len(claims)
        for claim in claims:
            cited = list(dict.fromkeys(claim.get("evidence_ids") or []))
            citations_per_claim[len(cited)] += 1
            counts["pairs"] += len(cited)
            if question_id in draw:
                counts["draw_pairs"] += len(cited)
            if len(cited) > 1:
                counts["multi_citation_claims"] += 1
            for evidence_id in cited:
                cited_ids.add(evidence_id)
                premise[kinds.get(evidence_id, "UNKNOWN")] += 1
    counts["distinct_cited_ids"] = len(cited_ids)
    for kind in PREMISE_KINDS:
        counts[f"premise_{kind}"] = premise.get(kind, 0)
    for chapter in range(2, 8):
        counts[f"chapter_{chapter}_answered"] = chapter_answered.get(chapter, 0)

    moves = {}
    for key, placeholder in STAGE2_PLACEHOLDERS.items():
        recomputed = counts.get(key, 0)
        relative = abs(recomputed - placeholder) / placeholder if placeholder else None
        moves[key] = {
            "stage2_placeholder": placeholder,
            "production_a": recomputed,
            "relative_move": round(relative, 3) if relative is not None else None,
            "enter_in_section_11": bool(
                relative is not None and relative > PLACEHOLDER_MOVE_TOLERANCE
            ),
        }
    model_declared = int(counts["abstained_model_declared"])
    answered = int(counts["answered"])
    return {
        "recomputed": {key: int(value) for key, value in sorted(counts.items())},
        "premise_split_other": {k: v for k, v in premise.items() if k not in PREMISE_KINDS},
        "citations_per_claim": {str(k): v for k, v in sorted(citations_per_claim.items())},
        "error_records_by_class": dict(errors),
        "strata": {
            "chapter": {
                str(chapter): {"answered": chapter_answered.get(chapter, 0), "total": total}
                for chapter, total in sorted(chapter_total.items(), key=lambda kv: str(kv[0]))
            },
            "ely_form": {
                str(form): {"answered": ely_answered.get(form, 0), "total": total}
                for form, total in sorted(ely_total.items(), key=lambda kv: str(kv[0]))
            },
        },
        "derived_sizes": {
            "model_declared_subsample_m": 20 if model_declared >= 20 else model_declared,
            "model_declared_subsample_m_if_wall_clock_allows": min(30, model_declared),
            "mislead_controls": 30,
            "retest_records": min(40, answered),
            "inter_rater_pairs": 50,
            "inter_rater_claims": 50,
            "clinician_cap_claims": 90,
        },
        "moves": moves,
        "moved_beyond_tolerance": [
            key for key, move in moves.items() if move["enter_in_section_11"]
        ],
    }


def _answered_map(run: dict[str, Any]) -> dict[str, bool]:
    return {r["question_id"]: outcome_of(r) == "ANSWERED" for r in run["results"]}


def _paired_over_common(
    first: dict[str, Any], second: dict[str, Any], *, exclude_errors: bool = True
) -> tuple[list[str], list[bool], list[bool], int]:
    first_records = records_by_id(first)
    second_records = records_by_id(second)
    ids = [q for q in first_records if q in second_records]
    kept, x, y, errors = [], [], [], 0
    for question_id in ids:
        a, b = first_records[question_id], second_records[question_id]
        if exclude_errors and (outcome_of(a) == "ERROR" or outcome_of(b) == "ERROR"):
            errors += 1
            continue
        kept.append(question_id)
        x.append(outcome_of(a) == "ANSWERED")
        y.append(outcome_of(b) == "ANSWERED")
    return kept, x, y, errors


def _error_rate_guard(run: dict[str, Any], name: str) -> dict[str, Any]:
    total = len(run["results"])
    errors = sum(1 for r in run["results"] if outcome_of(r) == "ERROR")
    return {
        "run": name,
        "questions": total,
        "error_records": errors,
        "error_share": round(errors / total, 3) if total else None,
        "valid_under_5_percent_rule": bool(total and errors / total <= 0.05),
    }


def section_3_5(inputs: Inputs) -> dict[str, Any]:
    a = inputs.get("production_a", section="3.5")
    b = inputs.get("production_b", section="3.5")
    naive = inputs.get("naive", section="3.5")
    stage2 = inputs.optional("stage2")

    ids, x, y, errors = _paired_over_common(a, b)
    disagreements = sum(1 for p, q in zip(x, y, strict=True) if p != q)
    a_records, b_records = records_by_id(a), records_by_id(b)
    both_answered = [q for q, p, r in zip(ids, x, y, strict=True) if p and r]
    claim_count_changed = sum(
        1 for q in both_answered if len(claims_of(a_records[q])) != len(claims_of(b_records[q]))
    )
    noise = {
        "pairs": len(ids),
        "error_pairs_excluded": errors,
        "answered_versus_abstained_disagreement": proportion_report(disagreements, len(ids)),
        "answered_in_both": len(both_answered),
        "claim_count_changed_among_answered_in_both": proportion_report(
            claim_count_changed, len(both_answered)
        ),
        "reading": (
            "One replicate pair is one realisation of run-to-run variability. Run noise "
            "does not bias the exact tests, whose null is symmetric discordance; its role "
            "is to explain the limited power of Q5 and to caveat single-question claims."
        ),
    }
    negative_control = paired_comparison(x, y)

    q5_ids, xa, xn, q5_errors = _paired_over_common(a, naive)
    q5 = paired_comparison(xa, xn)
    q5["error_pairs_excluded"] = q5_errors
    q5["reading"] = (
        "Reported as the Tango interval for the coverage cost of the chain, never as the "
        "MDE. The naive arm still carries the model's own refusal, so this is the "
        "pipeline's marginal cost on top of a model that declines on its own."
    )

    cross_day = None
    if stage2 is not None:
        s_ids, xs, xa2, s_errors = _paired_over_common(stage2, a)
        cross_day = {
            "pairs": len(s_ids),
            "error_pairs_excluded": s_errors,
            "disagreement": proportion_report(
                sum(1 for p, q in zip(xs, xa2, strict=True) if p != q), len(s_ids)
            ),
            "reading": (
                "An upper bound that also contains day-to-day drift, a seed difference and "
                "two hand-handled retries; not an input to anything."
            ),
        }
    return {
        "error_guards": [
            _error_rate_guard(a, "production_a"),
            _error_rate_guard(b, "production_b"),
            _error_rate_guard(naive, "naive"),
        ],
        "noise_floor": noise,
        "negative_control_a_versus_b": negative_control,
        "q5_production_a_versus_naive": q5,
        "stage2_versus_a_upper_bound": cross_day,
        "mde": mde_tables(),
        "multiplicity": (
            "Findings paper: Q5 alone, unadjusted. Journal paper: Q4 on clinician-adjudicated "
            "labels and Q5 under Holm."
        ),
    }


def _q4_outcome(claims: list[dict[str, Any]]) -> tuple[bool | None, bool]:
    """(success or None when excluded, excluded) for one arm on one question."""

    if not claims:
        return False, False
    agreements = [claim.get("agreement") for claim in claims]
    adjudicated = [a for a in agreements if a not in {"NOT_ADJUDICABLE", "UNLABELABLE", None}]
    if not adjudicated:
        return None, True
    success = "AGREES" in adjudicated and "DISAGREES" not in adjudicated
    return success, False


def _agreement_arm(
    run: dict[str, Any], labels: dict[str, Any], arm: str
) -> dict[str, dict[str, Any]]:
    """Per question: the arm's claim labels, claim count and the three Q4 definitions."""

    arm_labels = label_records(labels, arm)
    out = {}
    for record in run["results"]:
        question_id = record["question_id"]
        if outcome_of(record) == "ERROR":
            continue
        rendered = claims_of(record)
        label = arm_labels.get(question_id, {})
        claims = list(label.get("claims") or []) if rendered else []
        primary, excluded = _q4_outcome(claims)
        agreements = [c.get("agreement") for c in claims]
        out[question_id] = {
            "claims": len(rendered),
            "labelled_claims": len(claims),
            "primary": primary,
            "excluded": excluded,
            "any_agrees": "AGREES" in agreements,
            "no_disagrees": bool(rendered) and "DISAGREES" not in agreements,
            "not_adjudicable": sum(1 for a in agreements if a == "NOT_ADJUDICABLE"),
        }
    return out


def section_1_2(inputs: Inputs) -> dict[str, Any]:
    run = inputs.get("production_a", section="1.2")
    questions = inputs.get("questions", section="1.2")
    labels = inputs.get("labels", section="1.2")
    draw = draw_ids(questions)
    production = label_records(labels, "production")
    oracle = inputs.optional("oracle")

    # Q1 from the ANSWERED_* labels directly, never through the scorer (Section 4.4).
    wrong_in_draw = 0
    answered_in_draw = 0
    unlabelled = []
    unlabelable_records = 0
    for record in run["results"]:
        question_id = record["question_id"]
        if outcome_of(record) != "ANSWERED":
            continue
        label = production.get(question_id)
        if label is None:
            unlabelled.append(question_id)
            continue
        bucket = answered_bucket(label)
        if bucket is None:
            unlabelable_records += 1
        if question_id in draw:
            answered_in_draw += 1
            if bucket == "ANSWERED_WRONG":
                wrong_in_draw += 1
    if unlabelled:
        raise MissingInput(
            f"section 1.2: {len(unlabelled)} answered records carry no label (first: "
            f"{unlabelled[:3]}); no confirmatory quantity is computed on a partial census"
        )
    draw_n = len(draw)
    q1 = {
        "answered_wrong_in_draw": wrong_in_draw,
        "draw_questions": draw_n,
        "answered_in_draw": answered_in_draw,
        "records_with_unlabelable_claims": unlabelable_records,
        "p_wrong_over_questions": (
            {
                "rate": f"0/{draw_n}",
                "exact_one_sided_upper_95": round(zero_occurrence_upper_bound(draw_n), 4),
            }
            if wrong_in_draw == 0
            else {
                "rate": f"{wrong_in_draw}/{draw_n}",
                "exact_one_sided_lower_95": round(
                    exact_one_sided_bounds(wrong_in_draw, draw_n)["lower"], 4
                ),
                "wilson_95": [round(v, 4) for v in wilson_interval(wrong_in_draw, draw_n)],
            }
        ),
        "p_wrong_over_answered_records_diagnostic": (
            {
                "rate": f"0/{answered_in_draw}",
                "exact_one_sided_upper_95": round(zero_occurrence_upper_bound(answered_in_draw), 4),
            }
            if wrong_in_draw == 0
            else {
                "rate": f"{wrong_in_draw}/{answered_in_draw}",
                "wilson_95": [
                    round(v, 4) for v in wilson_interval(wrong_in_draw, answered_in_draw)
                ],
            }
        ),
        "stop_the_mvp": wrong_in_draw > 0,
        "reading": (
            "The pre-registered rule stands: one occurrence withdraws the MVP readiness "
            "claim and the paper reports a defect rate."
        ),
    }

    # Q2: claim-level unsupported rate over the census claims, record-clustered.
    clusters_claims: list[list[dict[str, Any]]] = []
    clusters_pairs: list[list[str]] = []
    clusters_draw: list[list[dict[str, Any]]] = []
    for record in run["results"]:
        if outcome_of(record) != "ANSWERED":
            continue
        label = production[record["question_id"]]
        claims = list(label.get("claims") or [])
        clusters_claims.append(claims)
        clusters_pairs.append(
            [v for claim in claims for v in (claim.get("pair_attribution") or {}).values()]
        )
        if record["question_id"] in draw:
            clusters_draw.append(claims)

    def unsupported_rate(items: list[dict[str, Any]]) -> float | None:
        labelled = [c for c in items if c.get("joint_attribution") not in {None, "UNLABELABLE"}]
        return rate_or_none(labelled, lambda c: c["joint_attribution"] in UNSUPPORTED)

    all_claims = [c for cluster in clusters_claims for c in cluster]
    all_pairs = [p for cluster in clusters_pairs for p in cluster]
    q2 = {
        "claims": len(all_claims),
        "unlabelable_claims": sum(
            1 for c in all_claims if c.get("joint_attribution") == "UNLABELABLE"
        ),
        "unsupported_claims": sum(
            1 for c in all_claims if c.get("joint_attribution") in UNSUPPORTED
        ),
        "rate": round(unsupported_rate(all_claims) or 0.0, 3),
        "bootstrap": clustered_bootstrap(clusters_claims, unsupported_rate),
        "draw_sensitivity": {
            "claims": sum(len(c) for c in clusters_draw),
            "rate": round(unsupported_rate([c for cl in clusters_draw for c in cl]) or 0.0, 3),
            "bootstrap": clustered_bootstrap(clusters_draw, unsupported_rate),
        },
        "pair_level_citation_precision_exploratory": {
            "pairs": len(all_pairs),
            "unsupported_pairs": sum(1 for p in all_pairs if p in UNSUPPORTED),
            "rate": round(
                rate_or_none(
                    [p for p in all_pairs if p != "UNLABELABLE"], lambda p: p in UNSUPPORTED
                )
                or 0.0,
                3,
            ),
            "bootstrap": clustered_bootstrap(
                clusters_pairs,
                lambda items: rate_or_none(
                    [p for p in items if p != "UNLABELABLE"], lambda p: p in UNSUPPORTED
                ),
            ),
        },
    }

    # Q3 needs the checker; Q4 the closed-book run; Q5 the naive run. Each is computed
    # when its input is present and named as missing otherwise, so the Findings paper
    # invocation does not fail on Horizon 2 inputs.
    q3 = (
        checker_sensitivity(inputs, labels, run)
        if inputs.has("checker")
        else {"missing": "--checker"}
    )
    q4 = (
        q4_agreement(run, inputs.get("closed_book", section="1.2 Q4"), labels)
        if inputs.has("closed_book")
        else {"missing": "--closed-book"}
    )
    if inputs.has("naive"):
        _, xa, xn, q5_errors = _paired_over_common(run, inputs.get("naive", section="1.2 Q5"))
        q5 = paired_comparison(xa, xn)
        q5["error_pairs_excluded"] = q5_errors
    else:
        q5 = {"missing": "--naive"}
    buckets = derive_buckets(
        run, labels, oracle, oracle_cleared_floor=_oracle_floor_cleared(oracle)
    )
    return {"q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5, "buckets": buckets}


def q4_agreement(
    run: dict[str, Any], closed_book: dict[str, Any], labels: dict[str, Any]
) -> dict[str, Any]:
    production = _agreement_arm(run, labels, "production")
    closed = _agreement_arm(closed_book, labels, "closed_book")
    ids = [q for q in production if q in closed]
    result: dict[str, Any] = {"questions": len(ids)}
    for name, key in (
        ("primary_intention_to_serve", "primary"),
        ("sensitivity_any_agrees", "any_agrees"),
        ("sensitivity_no_disagrees", "no_disagrees"),
    ):
        kept = [
            q
            for q in ids
            if not (key == "primary" and (production[q]["excluded"] or closed[q]["excluded"]))
        ]
        x = [bool(production[q][key]) for q in kept]
        y = [bool(closed[q][key]) for q in kept]
        comparison = paired_comparison(x, y)
        comparison["excluded_all_not_adjudicable"] = len(ids) - len(kept)
        result[name] = comparison
    both = [q for q in ids if production[q]["claims"] and closed[q]["claims"]]
    result["descriptive_2x2_both_answered"] = (
        paired_comparison(
            [bool(production[q]["primary"]) for q in both],
            [bool(closed[q]["primary"]) for q in both],
        )
        if both
        else None
    )
    for arm_name, arm in (("production", production), ("closed_book", closed)):
        answered = [v for v in arm.values() if v["claims"]]
        labelled = sum(v["labelled_claims"] for v in answered)
        not_adjudicable = sum(v["not_adjudicable"] for v in answered)
        result[f"{arm_name}_mean_claims_per_answered_question"] = (
            round(sum(v["claims"] for v in answered) / len(answered), 2) if answered else None
        )
        result[f"{arm_name}_not_adjudicable_share"] = proportion_report(not_adjudicable, labelled)
        result[f"{arm_name}_not_adjudicable_exceeds_20_percent"] = bool(
            labelled and not_adjudicable / labelled > 0.20
        )
    result["headline_allowed"] = not (
        result["production_not_adjudicable_exceeds_20_percent"]
        or result["closed_book_not_adjudicable_exceeds_20_percent"]
    )
    result["reading"] = (
        "Exploratory in the Findings paper (interval, no p-value); confirmatory in the "
        "journal paper on clinician-adjudicated labels. 'No claim DISAGREES' is monotone "
        "decreasing in claim count, so mean claims per arm is reported beside the 2x2."
    )
    return result


def _checker_index(checker: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(c["question_id"], int(c["claim_index"])): c for c in checker.get("claims") or []}


def _threshold_for_specificity(
    attributable_scores: Sequence[float], specificity: float = 0.90
) -> float | None:
    """The largest threshold keeping `specificity` of ATTRIBUTABLE claims unflagged.

    A claim is flagged when its score is below the threshold, so the threshold is the
    (1 - specificity) quantile of the attributable scores.
    """

    if not attributable_scores:
        return None
    ordered = sorted(attributable_scores)
    keep = math.ceil(specificity * len(ordered))
    index = len(ordered) - keep
    return ordered[index]


def checker_sensitivity(
    inputs: Inputs, labels: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    checker = inputs.get("checker", section="1.2 Q3")
    scores = _checker_index(checker)
    production = label_records(labels, "production")
    clusters: list[list[tuple[float, bool]]] = []
    missing = 0
    for record in run["results"]:
        if outcome_of(record) != "ANSWERED":
            continue
        question_id = record["question_id"]
        cluster = []
        for claim in production.get(question_id, {}).get("claims") or []:
            joint = claim.get("joint_attribution")
            if joint in {None, "UNLABELABLE"}:
                continue
            scored = scores.get((question_id, int(claim["index"])))
            if scored is None or scored.get("score") is None:
                missing += 1
                continue
            cluster.append((float(scored["score"]), joint in NON_ATTRIBUTABLE))
        clusters.append(cluster)

    # Leave-one-record-out point estimate: the threshold for record r is selected on
    # every other record's attributable claims.
    flagged = 0
    positives = 0
    for held_out, cluster in enumerate(clusters):
        others = [
            s for i, c in enumerate(clusters) if i != held_out for s, positive in c if not positive
        ]
        threshold = _threshold_for_specificity(others)
        if threshold is None:
            continue
        for score, positive in cluster:
            if positive:
                positives += 1
                flagged += score < threshold

    def sensitivity(items: list[tuple[float, bool]]) -> float | None:
        threshold = _threshold_for_specificity([s for s, positive in items if not positive])
        pos = [s for s, positive in items if positive]
        if threshold is None or not pos:
            return None
        return sum(1 for s in pos if s < threshold) / len(pos)

    bootstrap = clustered_bootstrap(clusters, sensitivity)
    lower = bootstrap["interval_95"][0] if bootstrap["interval_95"] else None
    pooled = [item for cluster in clusters for item in cluster]
    auc_non_attributable = auc_mann_whitney(
        [-s for s, positive in pooled if positive], [-s for s, positive in pooled if not positive]
    )
    return {
        "claims_scored": len(pooled),
        "claims_without_score": missing,
        "non_attributable_claims": positives,
        "sensitivity_at_specificity_0.90_loro": round(flagged / positives, 3)
        if positives
        else None,
        "bootstrap_operating_point_reselected": bootstrap,
        "usable_second_gate": bool(lower is not None and lower > 0.60),
        "auc_non_attributable_versus_attributable": (
            round(auc_non_attributable, 3) if auc_non_attributable is not None else None
        ),
        "instrument": checker.get("instrument"),
    }


def _oracle_floor_cleared(oracle: dict[str, Any] | None) -> bool:
    if not oracle:
        return False
    controls = [
        r for r in (oracle.get("records") or {}).values() if r.get("kind") == "answered_control"
    ]
    if not controls:
        return False
    recall = rate_or_none(controls, lambda r: bool(r.get("cited_in_pre_union_rankings")))
    widened = oracle.get("shortlist_size", 25) >= 50
    return bool(recall is not None and (recall >= 0.90 or (widened and recall >= 0.80)))


def section_4_6(inputs: Inputs) -> dict[str, Any]:
    run = inputs.get("production_a", section="4.6")
    questions = inputs.get("questions", section="4.6")
    labels = inputs.get("labels", section="4.6")
    oracle = inputs.optional("oracle")
    index = question_index(questions)
    draw = draw_ids(questions)
    production = label_records(labels, "production")
    derived = derive_buckets(
        run, labels, oracle, oracle_cleared_floor=_oracle_floor_cleared(oracle)
    )
    buckets = derived["buckets"]
    records = records_by_id(run)

    def distribution(ids: Sequence[str]) -> dict[str, Any]:
        counts = collections.Counter(buckets[q] for q in ids)
        classified = [counts.get(b, 0) for b in BUCKETS]
        total = sum(classified)
        goodman = goodman_intervals(classified)
        sison = sison_glaz_intervals(classified) if total else [(0.0, 1.0)] * 5
        return {
            "n": len(ids),
            "classified": total,
            "unclassified": counts.get(None, 0),
            "cells": {
                bucket: {
                    "count": count,
                    "share": round(count / total, 3) if total else None,
                    "goodman_95": [round(v, 3) for v in g],
                    "sison_glaz_95": [round(v, 3) for v in s],
                }
                for bucket, count, g, s in zip(BUCKETS, classified, goodman, sison, strict=True)
            },
            "judge_derived_cells": sorted(
                {buckets[q] for q in ids if derived["sources"][q] == "judge" and buckets[q]}
            ),
        }

    draw_ids_ordered = [q for q in records if q in draw]
    result: dict[str, Any] = {
        "bucket_distribution_draw": distribution(draw_ids_ordered),
        "bucket_distribution_census_counts": {
            bucket: sum(1 for q in records if buckets[q] == bucket) for bucket in BUCKETS
        },
        "bucket_sources": collections.Counter(derived["sources"].values()),
    }

    # Guideline agreement with the identification region.
    clusters: list[list[dict[str, Any]]] = []
    for question_id, record in records.items():
        if outcome_of(record) == "ANSWERED" and question_id in production:
            clusters.append(list(production[question_id].get("claims") or []))
    claims = [c for cluster in clusters for c in cluster]

    def agreement_block(
        items: list[dict[str, Any]], cl: list[list[dict[str, Any]]]
    ) -> dict[str, Any]:
        labelled = [c for c in items if c.get("agreement") not in {None, "UNLABELABLE"}]
        not_adj = [c for c in labelled if c["agreement"] == "NOT_ADJUDICABLE"]
        adjudicated = [c for c in labelled if c["agreement"] != "NOT_ADJUDICABLE"]
        agrees = sum(1 for c in adjudicated if c["agreement"] == "AGREES")
        complete_case = proportion_report(agrees, len(adjudicated))
        complete_case["bootstrap"] = clustered_bootstrap(
            cl,
            lambda xs: rate_or_none(
                [
                    c
                    for c in xs
                    if c.get("agreement") not in {None, "UNLABELABLE", "NOT_ADJUDICABLE"}
                ],
                lambda c: c["agreement"] == "AGREES",
            ),
        )
        region = (
            [round(agrees / len(labelled), 3), round((agrees + len(not_adj)) / len(labelled), 3)]
            if labelled
            else None
        )
        headline = None
        if region and complete_case["interval_95"]:
            width_region = region[1] - region[0]
            width_cc = complete_case["interval_95"][1] - complete_case["interval_95"][0]
            headline = "identification_region" if width_region > width_cc else "complete_case"
        return {
            "labelled_claims": len(labelled),
            "not_adjudicable_share": proportion_report(len(not_adj), len(labelled)),
            "agrees_complete_case": complete_case,
            "identification_region": region,
            "headline": headline,
            "distribution": dict(collections.Counter(c["agreement"] for c in labelled)),
        }

    result["guideline_agreement"] = agreement_block(claims, clusters)
    result["eligibility_drop"] = {
        "count": sum(1 for c in claims if c.get("eligibility_drop")),
        "conditions": dict(
            collections.Counter(
                str(c.get("eligibility_condition")) for c in claims if c.get("eligibility_drop")
            )
        ),
    }

    # Potential to mislead, by trigger class, under the provisional name.
    mislead: dict[str, Any] = {
        "name": "potential to mislead, rated by a non-clinician, provisional"
    }
    by_trigger: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    control = collections.Counter()
    for question_id in records:
        label = production.get(question_id)
        if not label:
            continue
        for claim in label.get("claims") or []:
            rating = claim.get("mislead")
            if not rating:
                continue
            cell = f"{rating.get('extent')}/{rating.get('likelihood')}"
            triggers = claim_is_flagged(claim, label)
            if triggers:
                for trigger in triggers:
                    by_trigger[trigger][cell] += 1
            else:
                control[cell] += 1
    mislead["by_trigger_class"] = {t: dict(by_trigger.get(t, {})) for t in TRIGGER_CLASSES}
    mislead["controls"] = dict(control)
    result["potential_to_mislead"] = mislead

    # Bucket by agreement cross-tabulation over answered records.
    cross: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for question_id, record in records.items():
        if outcome_of(record) != "ANSWERED" or question_id not in production:
            continue
        worst = "AGREES"
        for claim in production[question_id].get("claims") or []:
            a = claim.get("agreement")
            if a == "DISAGREES":
                worst = "DISAGREES"
            elif a == "PARTIAL" and worst != "DISAGREES":
                worst = "PARTIAL"
            elif a == "NOT_ADJUDICABLE" and worst == "AGREES":
                worst = "NOT_ADJUDICABLE"
        cross[str(buckets[question_id])][worst] += 1
    result["bucket_by_worst_agreement"] = {k: dict(v) for k, v in cross.items()}

    # Strata with Wilson intervals and the verbosity confound beside each.
    unsupported_claims = sum(1 for c in claims if c.get("joint_attribution") in UNSUPPORTED)
    labelled_claims = sum(
        1 for c in claims if c.get("joint_attribution") not in {None, "UNLABELABLE"}
    )
    pooled_rate = unsupported_claims / labelled_claims if labelled_claims else 0.0

    def stratum(ids: Sequence[str]) -> dict[str, Any]:
        answered = [q for q in ids if outcome_of(records[q]) == "ANSWERED"]
        wrong = sum(1 for q in answered if buckets[q] == "ANSWERED_WRONG")
        k = [len(claims_of(records[q])) for q in answered]
        return {
            "n": len(ids),
            "answered": proportion_report(len(answered), len(ids)),
            "answered_wrong": (
                {"count": wrong, "of_answered": len(answered)}
                if len(answered) < 10
                else proportion_report(wrong, len(answered))
            ),
            "mean_claims_per_answered_record": round(sum(k) / len(k), 2) if k else None,
            "verbosity_standardised_record_rate": (
                round(verbosity_reference(pooled_rate, k), 3) if k else None
            ),
        }

    chapters: dict[str, list[str]] = collections.defaultdict(list)
    forms: dict[str, list[str]] = collections.defaultdict(list)
    for question_id in records:
        record = records[question_id]
        chapters[str(record.get("chapter_no"))].append(question_id)
        forms[str(record.get("ely_form_no"))].append(question_id)
    coupling = inputs.optional("coupling")
    terciles: dict[str, list[str]] = collections.defaultdict(list)
    if coupling:
        for question_id, entry in (coupling.get("questions") or {}).items():
            if question_id in records and entry.get("tercile") is not None:
                terciles[str(entry["tercile"])].append(question_id)
    result["strata"] = {
        "by_chapter": {c: stratum(ids) for c, ids in sorted(chapters.items())},
        "by_ely_form": {f: stratum(ids) for f, ids in sorted(forms.items())},
        "by_coupling_tercile": (
            {t: stratum(ids) for t, ids in sorted(terciles.items())}
            if coupling
            else {"missing": "--coupling"}
        ),
        "precision_statement": (
            "Only chapter 6 against the rest admits a minimum detectable difference below "
            "0.25 at 80% power; every other contrast is descriptive."
        ),
    }
    high_overlap = [
        q for q in records if "REVIEW_HIGH_SOURCE_OVERLAP" in (index.get(q, {}).get("flags") or [])
    ]
    result["subgroups"] = {
        "review_high_source_overlap": {
            "n": len(high_overlap),
            "answered": proportion_report(
                sum(1 for q in high_overlap if outcome_of(records[q]) == "ANSWERED"),
                len(high_overlap),
            ),
        },
        "general_population_advice": stratum(
            [
                q
                for q in records
                if "GENERAL_POPULATION_ADVICE" in (index.get(q, {}).get("flags") or [])
            ]
        ),
        "near_duplicates_removed": stratum(
            [
                q
                for q in records
                if not any(
                    f.startswith("NEAR_DUPLICATE_OF") for f in (index.get(q, {}).get("flags") or [])
                )
            ]
        ),
    }
    result["verbosity_note"] = (
        "The any-claim rule makes the record-level bucket a function of verbosity; the "
        "standardised rate is 1 - (1 - p)^k at the pooled per-claim rate, an independence "
        "reference and not a prediction."
    )
    return result


def _axis_pairs(
    first: dict[str, Any], second: dict[str, Any], *, axis: str
) -> list[list[tuple[str, str]]]:
    """Record-clustered (label, label) pairs for one axis across two label files."""

    clusters = []
    first_records = first.get("records") or {}
    second_records = second.get("records") or {}
    for key, record in first_records.items():
        other = second_records.get(key)
        if other is None:
            continue
        pairs = []
        by_index = {int(c["index"]): c for c in other.get("claims") or []}
        for claim in record.get("claims") or []:
            twin = by_index.get(int(claim["index"]))
            if twin is None:
                continue
            if axis == "pair_attribution":
                for evidence_id, label in (claim.get("pair_attribution") or {}).items():
                    twin_label = (twin.get("pair_attribution") or {}).get(evidence_id)
                    if label in ATTRIBUTION_LABELS and twin_label in ATTRIBUTION_LABELS:
                        pairs.append((label, twin_label))
            elif axis == "eligibility_drop":
                pairs.append((str(bool(claim.get(axis))), str(bool(twin.get(axis)))))
            else:
                label, twin_label = claim.get(axis), twin.get(axis)
                allowed = ATTRIBUTION_LABELS if axis == "joint_attribution" else AGREEMENT_LABELS
                if label in allowed and twin_label in allowed:
                    pairs.append((label, twin_label))
        if axis == "presentation_defect":
            pairs = [(str(bool(record.get(axis))), str(bool(other.get(axis))))]
        if pairs:
            clusters.append(pairs)
    return clusters


def reliability(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for axis, categories, ordinal, unit in (
        ("pair_attribution", ATTRIBUTION_ORDER, True, "pairs"),
        ("joint_attribution", ATTRIBUTION_ORDER, True, "claims"),
        ("agreement", AGREEMENT_LABELS, False, "claims"),
        ("eligibility_drop", ("False", "True"), False, "claims"),
        ("presentation_defect", ("False", "True"), False, "records"),
    ):
        clusters = _axis_pairs(first, second, axis=axis)
        block = bootstrapped_agreement(clusters, categories, ordinal=ordinal)
        block["unit"] = unit
        block["ordinal_order"] = list(categories) if ordinal else None
        out[axis] = block
    return out


def section_4_5(inputs: Inputs) -> dict[str, Any]:
    labels = inputs.get("labels", section="4.5")
    result: dict[str, Any] = {
        "note": (
            "No confidence interval in this paper includes annotator error. The re-test is "
            "an upper bound on inter-rater reliability, not an estimate of validity; at 50 "
            "pairs the half-width on a coefficient near 0.7 is about 0.2, so the inter-rater "
            "statistics are descriptive with no threshold attached."
        )
    }
    if inputs.has("retest_labels"):
        retest = inputs.get("retest_labels", section="4.5")
        result["intra_rater_retest"] = reliability(labels, retest)
        result["intra_rater_retest"]["records"] = len(retest.get("records") or {})
    else:
        result["intra_rater_retest"] = {"missing": "--retest-labels"}
    if inputs.has("second_reader_labels"):
        second = inputs.get("second_reader_labels", section="4.5")
        result["inter_rater"] = reliability(labels, second)
        result["inter_rater"]["annotator"] = second.get("annotator")
    else:
        result["inter_rater"] = {
            "missing": "--second-reader-labels",
            "consequence": (
                "single annotator, no independent replication: guideline agreement is a "
                "self-labelled measurement rather than an estimate of accuracy"
            ),
        }
    timings = {}
    for name, block in (labels.get("passes") or {}).items():
        try:
            start = dt.datetime.fromisoformat(block["started_at"])
            end = dt.datetime.fromisoformat(block["finished_at"])
            timings[name] = round((end - start).total_seconds() / 60, 1)
        except (KeyError, TypeError, ValueError):
            timings[name] = None
    result["pass_minutes"] = timings
    return result


def section_5_2(inputs: Inputs) -> dict[str, Any]:
    run = inputs.get("production_a", section="5.2")
    labels = inputs.get("labels", section="5.2")
    checker = inputs.get("checker", section="5.2")
    production = label_records(labels, "production")
    result: dict[str, Any] = {"q3": checker_sensitivity(inputs, labels, run)}
    claim_scores = _checker_index(checker)
    pair_scores = {
        (p["question_id"], int(p["claim_index"]), p["evidence_id"]): p
        for p in checker.get("pairs") or []
    }

    claim_items: list[list[dict[str, Any]]] = []
    pair_items: list[list[dict[str, Any]]] = []
    disagreements = []
    for record in run["results"]:
        if outcome_of(record) != "ANSWERED":
            continue
        question_id = record["question_id"]
        cluster_claims, cluster_pairs = [], []
        for claim in production.get(question_id, {}).get("claims") or []:
            index = int(claim["index"])
            joint = claim.get("joint_attribution")
            scored = claim_scores.get((question_id, index))
            if joint in ATTRIBUTION_LABELS and scored and scored.get("score") is not None:
                item = {
                    "label": joint,
                    "score": float(scored["score"]),
                    "lexical": scored.get("lexical_score"),
                    "mix": scored.get("premise_mix"),
                }
                cluster_claims.append(item)
                if (item["score"] < 0.5) != (joint in NON_ATTRIBUTABLE):
                    disagreements.append(
                        {
                            "question_id": question_id,
                            "claim_index": index,
                            "human": joint,
                            "checker_score": item["score"],
                        }
                    )
            for evidence_id, label in (claim.get("pair_attribution") or {}).items():
                scored_pair = pair_scores.get((question_id, index, evidence_id))
                if (
                    label in ATTRIBUTION_LABELS
                    and scored_pair
                    and scored_pair.get("score") is not None
                ):
                    cluster_pairs.append(
                        {
                            "label": label,
                            "score": float(scored_pair["score"]),
                            "kind": str(scored_pair.get("premise_kind") or "").upper(),
                        }
                    )
        claim_items.append(cluster_claims)
        pair_items.append(cluster_pairs)
    claims = [c for cl in claim_items for c in cl]
    pairs = [p for cl in pair_items for p in cl]

    def auc_block(
        items: list[dict[str, Any]], key: str, positive: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any]:
        pos = [-i[key] for i in items if positive(i) and i.get(key) is not None]
        neg = [-i[key] for i in items if not positive(i) and i.get(key) is not None]
        value = auc_mann_whitney(pos, neg)
        return {
            "auc": round(value, 3) if value is not None else None,
            "positives": len(pos),
            "negatives": len(neg),
        }

    def two_by_two(items: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "flagged_and_non_attributable": sum(
                1 for i in items if i["score"] < 0.5 and i["label"] in NON_ATTRIBUTABLE
            ),
            "flagged_and_attributable": sum(
                1 for i in items if i["score"] < 0.5 and i["label"] == "ATTRIBUTABLE"
            ),
            "unflagged_and_non_attributable": sum(
                1 for i in items if i["score"] >= 0.5 and i["label"] in NON_ATTRIBUTABLE
            ),
            "unflagged_and_attributable": sum(
                1 for i in items if i["score"] >= 0.5 and i["label"] == "ATTRIBUTABLE"
            ),
        }

    result["claim_level"] = {
        "n": len(claims),
        "auc_non_attributable": auc_block(
            claims, "score", lambda i: i["label"] in NON_ATTRIBUTABLE
        ),
        "auc_unsupported": auc_block(claims, "score", lambda i: i["label"] in UNSUPPORTED),
        "lexical_floor_auc_non_attributable": auc_block(
            claims, "lexical", lambda i: i["label"] in NON_ATTRIBUTABLE
        ),
        "two_by_two_at_0.5": two_by_two(claims),
        "by_premise_mix": {
            mix: {"n": len(subset), "two_by_two_at_0.5": two_by_two(subset)}
            for mix in ("prose", "tabular", "mixed")
            for subset in [[c for c in claims if c.get("mix") == mix]]
        },
    }
    result["pair_level_secondary"] = {
        "n": len(pairs),
        "auc_non_attributable": auc_block(pairs, "score", lambda i: i["label"] in NON_ATTRIBUTABLE),
        "two_by_two_at_0.5": two_by_two(pairs),
        "by_premise": {
            name: {
                "n": len(subset),
                "two_by_two_at_0.5": two_by_two(subset),
                "auc": auc_block(subset, "score", lambda i: i["label"] in NON_ATTRIBUTABLE),
            }
            for name, subset in (
                ("prose", [p for p in pairs if is_prose(p["kind"])]),
                ("tabular", [p for p in pairs if p["kind"] and not is_prose(p["kind"])]),
                (
                    "data_dictionary_entry",
                    [p for p in pairs if p["kind"] == "DATA_DICTIONARY_ENTRY"],
                ),
            )
        },
    }
    result["disagreements_at_0.5"] = disagreements
    result["instrument"] = checker.get("instrument")
    result["timing"] = checker.get("timing")
    return result


def section_6_2(inputs: Inputs) -> dict[str, Any]:
    inputs.get("production_a", section="6.2")
    labels = inputs.get("labels", section="6.2")
    judge_files = inputs.get("judge", section="6.2")
    result: dict[str, Any] = {"judges": {}}
    by_judge: dict[str, dict[str, list[dict[str, Any]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    for document in judge_files:
        by_judge[document.get("judge", "unknown")][document.get("check", "primary")].append(
            document
        )
    for judge, checks in by_judge.items():
        block: dict[str, Any] = {}
        primary = checks.get("primary")
        if primary:
            human_vs_judge = {}
            for axis, categories, ordinal in (
                ("pair_attribution", ATTRIBUTION_ORDER, True),
                ("joint_attribution", ATTRIBUTION_ORDER, True),
                ("agreement", AGREEMENT_LABELS, False),
            ):
                clusters = _axis_pairs(labels, primary[0], axis=axis)
                human_vs_judge[axis] = bootstrapped_agreement(clusters, categories, ordinal=ordinal)
            attribution_ok = (
                human_vs_judge["joint_attribution"].get("gwet_ac2_ordinal") or 0
            ) >= 0.60
            agreement_ok = (human_vs_judge["agreement"].get("gwet_ac1") or 0) >= 0.60
            block["human_agreement"] = human_vs_judge
            block["corroborating_signal"] = bool(attribution_ok and agreement_ok)
        if checks.get("stability") and primary:
            block["stability"] = _judge_repeat_agreement(primary[0], checks["stability"][0])
            block["unstable"] = block["stability"]["percent_agreement"] < 0.90
        if checks.get("determinism") and primary:
            block["api_determinism"] = _judge_repeat_agreement(primary[0], checks["determinism"][0])
        if checks.get("no_gold") and primary:
            with_gold = {
                (key, int(c["index"])): c.get("agreement")
                for key, r in (primary[0].get("records") or {}).items()
                for c in r.get("claims") or []
            }
            reproduced = total = 0
            for key, r in (checks["no_gold"][0].get("records") or {}).items():
                for c in r.get("claims") or []:
                    if with_gold.get((key, int(c["index"]))) == "AGREES":
                        total += 1
                        reproduced += c.get("agreement") == "AGREES"
            share = reproduced / total if total else None
            block["no_gold"] = {
                "with_gold_agrees": total,
                "reproduced_without_gold": reproduced,
                "share": round(share, 3) if share is not None else None,
                "question_echo": bool(share is not None and share >= 0.85),
            }
        if checks.get("miscitation"):
            swaps = list((checks["miscitation"][0].get("miscitation") or {}).values())
            attributable = sum(1 for s in swaps if s.get("verdict") == "ATTRIBUTABLE")
            halves = {}
            for kind in ("cross_question", "within_question"):
                subset = [s for s in swaps if s.get("swap_kind") == kind]
                halves[kind] = proportion_report(
                    sum(1 for s in subset if s.get("verdict") == "ATTRIBUTABLE"), len(subset)
                )
            verdict = (
                "pass" if attributable <= 3 else ("fail" if attributable >= 8 else "indeterminate")
            )
            block["miscitation"] = {
                "swapped_claims": len(swaps),
                "judged_attributable": proportion_report(attributable, len(swaps)),
                "by_half": halves,
                "band": verdict,
            }
        result["judges"][judge] = block
    result["design"] = (
        "Two checkpoints of one vendor's model: a two-judge same-vendor panel, not a jury. "
        "The human census is the arbiter of every disagreement."
    )
    return result


def _judge_repeat_agreement(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    agree = total = 0
    for axis in ("joint_attribution", "agreement"):
        for cluster in _axis_pairs(first, second, axis=axis):
            for left, right in cluster:
                total += 1
                agree += left == right
    return {
        "compared_labels": total,
        "percent_agreement": round(agree / total, 3) if total else 0.0,
    }


def section_7_3(inputs: Inputs) -> dict[str, Any]:
    run = inputs.get("production_a", section="7.3")
    labels = inputs.get("labels", section="7.3")
    oracle = inputs.get("oracle", section="7.3")
    records = records_by_id(run)
    oracle_records = oracle.get("records") or {}

    gate_ids = [
        q
        for q, r in records.items()
        if outcome_of(r) == "ABSTAINED" and r.get("generation") is None
    ]
    model_ids = [
        q
        for q, r in records.items()
        if outcome_of(r) == "ABSTAINED" and r.get("generation") is not None
    ]
    gate_labels = labels.get("gate_blocked") or {}
    sample_block = labels.get("abstained_sample") or {}
    sample_ids = [q for q in model_ids if isinstance(sample_block.get(q), dict)]

    gate_present = sum(1 for q in gate_ids if (gate_labels.get(q) or {}).get("dak_has_answer"))
    sample_present = sum(
        1 for q in sample_ids if sample_block[q].get("any_retrieved_passage_answers")
    )
    stratified = stratified_presence(
        gate_blocked_present=gate_present,
        gate_blocked_total=len(gate_ids),
        sample_present=sample_present,
        sample_size=len(sample_ids),
        model_declared_total=len(model_ids),
    )

    controls = [r for r in oracle_records.values() if r.get("kind") == "answered_control"]
    known_item = proportion_report(
        sum(1 for r in controls if r.get("cited_in_pre_union_rankings")), len(controls)
    )
    retention = proportion_report(
        sum(1 for r in controls if r.get("cited_in_shortlist")), len(controls)
    )
    floor_cleared = _oracle_floor_cleared(oracle)

    judge_present = {
        q: (oracle_records.get(q) or {}).get("verdict") == "PRESENT" for q in model_ids
    }
    judged = [q for q in model_ids if q in oracle_records]
    judge_rate = rate_or_none(judged, lambda q: judge_present[q])
    sample_human = [
        bool(sample_block[q].get("any_retrieved_passage_answers"))
        for q in sample_ids
        if q in oracle_records
    ]
    sample_judge = [judge_present[q] for q in sample_ids if q in oracle_records]
    corrected = (
        difference_estimator(
            judge_rate_over_stratum=judge_rate,
            stratum_size=len(model_ids),
            sample_human=sample_human,
            sample_judge=sample_judge,
        )
        if judge_rate is not None
        else {"estimate": None}
    )
    read_ids = gate_ids + sample_ids
    human_judge_pairs = [
        [
            (
                "PRESENT" if human_presence(labels, q) else "ABSENT",
                "PRESENT"
                if judge_present.get(q, (oracle_records.get(q) or {}).get("verdict") == "PRESENT")
                else "ABSENT",
            )
        ]
        for q in read_ids
        if q in oracle_records and human_presence(labels, q) is not None
    ]
    judge_vs_human = bootstrapped_agreement(human_judge_pairs, ("ABSENT", "PRESENT"))
    gate_right = sum(1 for q in gate_ids if (gate_labels.get(q) or {}).get("gate_right"))

    def abstention_2x2(arm_run: dict[str, Any]) -> dict[str, Any]:
        arm_records = records_by_id(arm_run)
        should = did = both = 0
        n = 0
        for q, r in arm_records.items():
            if outcome_of(r) == "ERROR":
                continue
            presence = human_presence(labels, q)
            if presence is None:
                verdict = (oracle_records.get(q) or {}).get("verdict")
                if verdict not in {"PRESENT", "ABSENT"}:
                    continue
                presence = verdict == "PRESENT"
            n += 1
            should_abstain = not presence
            did_abstain = outcome_of(r) == "ABSTAINED"
            should += should_abstain
            did += did_abstain
            both += should_abstain and did_abstain
        return {
            "questions_with_presence_verdict": n,
            "abstention_precision": proportion_report(both, did),
            "abstention_recall": proportion_report(both, should),
        }

    result = {
        "strata": {
            "gate_blocked": len(gate_ids),
            "model_declared": len(model_ids),
            "sampled": len(sample_ids),
        },
        "sample_seed_and_size_recorded": {k: sample_block.get(k) for k in ("size", "seed")},
        "retrievable_corpus_presence_stratified": stratified,
        "judge_only_split_uncorrected": {
            "judged": len(judged),
            "present_rate": round(judge_rate, 3) if judge_rate is not None else None,
        },
        "judge_split_bias_corrected": corrected,
        "judge_versus_human_on_read_records": judge_vs_human,
        "judge_split_is_estimate": bool((judge_vs_human.get("gwet_ac1") or 0) >= 0.60),
        "oracle_known_item_recall_pre_union": known_item,
        "oracle_shortlist_retention": retention,
        "oracle_shortlist_size": oracle.get("shortlist_size"),
        "oracle_floor_cleared": floor_cleared,
        "gate_blocked_gate_right": proportion_report(gate_right, len(gate_ids)),
        "split_by_reason": {
            "gate_blocked_present": gate_present,
            "model_declared_sampled_present": sample_present,
        },
        "abstention_precision_recall": {"production_a": abstention_2x2(run)},
        "name": (
            "retrievable corpus presence: ABSENT over-counts ABSTAINED_CORRECT by whatever "
            "this retriever misses"
        ),
    }
    if inputs.has("naive"):
        result["abstention_precision_recall"]["naive"] = abstention_2x2(
            inputs.get("naive", section="7.3")
        )
    return result


SECTIONS: dict[str, Callable[[Inputs], dict[str, Any]]] = {
    "placeholders": section_placeholders,
    "3.5": section_3_5,
    "1.2": section_1_2,
    "4.6": section_4_6,
    "4.5": section_4_5,
    "5.2": section_5_2,
    "6.2": section_6_2,
    "7.3": section_7_3,
}


def rubric_check(inputs: Inputs, rubric: Path | None) -> dict[str, Any]:
    if rubric is None:
        return {"rubric": None}
    expected = sha256_of(rubric)
    report: dict[str, Any] = {"rubric": str(rubric), "sha256": expected, "files": {}}
    for name in ("labels", "retest_labels", "second_reader_labels", "oracle", "checker"):
        if inputs.has(name):
            document = inputs.get(name, section="rubric")
            stated = document.get("rubric_sha256")
            report["files"][name] = (
                "matches" if stated == expected else f"labelled under a revised rubric ({stated})"
            )
    for index, document in enumerate(inputs.optional("judge") or []):
        stated = document.get("rubric_sha256")
        report["files"][f"judge[{index}]"] = (
            "matches" if stated == expected else f"labelled under a revised rubric ({stated})"
        )
    return report


def write_mde(path: Path, inputs: Inputs) -> dict[str, Any]:
    claims, answered, mean_claims = 190, 73, 190 / 73
    source = "stage-2 placeholders"
    if inputs.has("production_a"):
        run = inputs.get("production_a", section="mde")
        answered_records = [r for r in run["results"] if outcome_of(r) == "ANSWERED"]
        if answered_records:
            claims = sum(len(claims_of(r)) for r in answered_records)
            answered = len(answered_records)
            mean_claims = claims / answered
            source = "production A"
    document = {
        "schema_version": 1,
        "written_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "A data-dependent design parameter fixed before the comparison arm is unblinded, "
            "labelled that way and not as fixed a priori. Non-significant results are "
            "reported as Tango intervals, never as the MDE."
        ),
        "mde": mde_tables(),
        "expected_half_widths": expected_half_widths(
            claims=claims, answered_records=answered, mean_claims_per_record=mean_claims
        ),
        "half_width_source": source,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--production-a", type=Path)
    parser.add_argument("--production-b", type=Path)
    parser.add_argument("--naive", type=Path)
    parser.add_argument("--closed-book", type=Path)
    parser.add_argument("--stage2", type=Path)
    parser.add_argument(
        "--questions",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "questions" / "mvp-coverage-who-hiv-v2.json",
    )
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--retest-labels", type=Path)
    parser.add_argument("--second-reader-labels", type=Path)
    parser.add_argument("--judge", type=Path, action="append", default=None)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--checker", type=Path)
    parser.add_argument(
        "--coupling", type=Path, help="coupling.json from scripts/lexical_coupling.py"
    )
    parser.add_argument(
        "--rubric",
        type=Path,
        help="the deposited plan; every input's rubric_sha256 is checked against it",
    )
    parser.add_argument(
        "--sections",
        default=",".join(SECTIONS),
        help="comma-separated subset of " + ", ".join(SECTIONS),
    )
    parser.add_argument("--write-mde", type=Path, help="write the design-stage mde.json and exit")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resamples", type=int, default=BOOTSTRAP_RESAMPLES)
    return parser


def main() -> int:
    global BOOTSTRAP_RESAMPLES
    arguments = build_parser().parse_args()
    BOOTSTRAP_RESAMPLES = arguments.resamples
    if arguments.judge is not None:
        arguments.judge = [Path(p) for p in arguments.judge]
    inputs = Inputs(arguments)
    if arguments.write_mde:
        document = write_mde(arguments.write_mde, inputs)
        for name, block in document["mde"].items():
            if isinstance(block, dict) and "primary" in block:
                print(
                    f"MDE {name}: {block['primary']['power_by_delta']} -> "
                    f"{block['primary']['mde_at_80_percent']}"
                )
        print(f"wrote {arguments.write_mde}")
        return 0

    requested = [s.strip() for s in arguments.sections.split(",") if s.strip()]
    unknown = [s for s in requested if s not in SECTIONS]
    if unknown:
        print(f"unknown sections: {unknown}; choose from {list(SECTIONS)}", file=sys.stderr)
        return 2
    output: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "seed": SEED,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "software": _software_versions(),
        "sections": {},
    }
    missing: list[str] = []
    for name in requested:
        try:
            output["sections"][name] = SECTIONS[name](inputs)
            print(f"section {name}: computed")
        except MissingInput as error:
            missing.append(str(error))
            print(f"section {name}: MISSING INPUT - {error}", file=sys.stderr)
    output["rubric_check"] = rubric_check(inputs, arguments.rubric)
    output["inputs"] = inputs.provenance
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(output, indent=2, default=_json_default) + "\n", encoding="utf-8"
        )
        print(f"wrote {arguments.output}")
    if missing:
        print(f"\n{len(missing)} section(s) not computed for want of an input.", file=sys.stderr)
        return 1
    return 0


def _json_default(value: Any) -> Any:
    if isinstance(value, (collections.Counter, dict)):
        return dict(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    raise TypeError(f"not serialisable: {type(value)!r}")


def _software_versions() -> dict[str, str]:
    import platform

    import scipy
    import statsmodels

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "statsmodels": statsmodels.__version__,
    }


if __name__ == "__main__":
    raise SystemExit(main())
