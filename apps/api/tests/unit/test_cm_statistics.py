"""The frozen analysis code of the correctness measurement plan, checked against the plan.

`scripts/cm_statistics.py` is deposited before any label exists, so the only way to know
its estimators are the ones Section 1.4 names is to reproduce, to the digit, every number
the plan states as a consequence of them: the exact bounds of Section 1.2, the Wilson
cross-check against statsmodels, the three MDE power tables of Section 3.5, and the
derivation rules of Section 4.4. A drift in any of them would make the deposited analysis
disagree with the deposited document.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS = REPO_ROOT / "benchmarks" / "results"
QUESTIONS = REPO_ROOT / "benchmarks" / "questions" / "mvp-coverage-who-hiv-v2.json"


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cm = _load("cm_statistics")


class TestExactBounds:
    """Section 1.2: both denominators, named in one sentence, to the stated digit."""

    @pytest.mark.parametrize(
        ("total", "percent"), [(149, 1.99), (69, 4.25), (50, 5.82), (150, 1.98)]
    )
    def test_zero_occurrence_bound(self, total, percent):
        assert round(cm.zero_occurrence_upper_bound(total) * 100, 2) == percent

    def test_the_answered_record_bound_is_exact_not_wilson(self):
        """The plan quotes 4.25% at 69, never the two-sided Wilson limit of 5.27%."""

        assert round(cm.wilson_interval(0, 69)[1] * 100, 2) == 5.27
        report = cm.proportion_report(0, 69)
        assert report["interval_95"][1] == 0.042
        assert "exact" in report["method"]

    def test_clopper_pearson_lower_bound_for_one_occurrence(self):
        bounds = cm.exact_one_sided_bounds(1, 149)
        assert 0 < bounds["lower"] < 0.001
        assert bounds["upper"] > bounds["lower"]


class TestWilson:
    @pytest.mark.parametrize(("k", "n"), [(21, 49), (73, 164), (0, 69), (69, 149), (1, 1)])
    def test_matches_statsmodels(self, k, n):
        from statsmodels.stats.proportion import proportion_confint

        ours = cm.wilson_interval(k, n)
        theirs = proportion_confint(k, n, method="wilson")
        assert ours == pytest.approx(theirs, abs=1e-9)


class TestSimultaneousIntervals:
    def test_goodman_at_two_cells_is_wilson_at_the_bonferroni_z(self):
        from scipy import stats

        z = math.sqrt(stats.chi2.ppf(1 - 0.05 / 2, 1))
        goodman = cm.goodman_intervals([10, 10])
        assert goodman[0] == pytest.approx(cm.wilson_interval(10, 20, z), abs=1e-9)

    def test_goodman_contains_the_observed_share(self):
        counts = [30, 25, 3, 60, 46]
        for count, (lower, upper) in zip(counts, cm.goodman_intervals(counts), strict=True):
            assert lower <= count / sum(counts) <= upper

    def test_sison_glaz_reproduces_the_desctools_example(self):
        """x = c(56, 72, 73, 59, 62, 87, 58) in DescTools::MultinomCI, method sisonglaz."""

        intervals = cm.sison_glaz_intervals([56, 72, 73, 59, 62, 87, 58])
        assert intervals[0][0] == pytest.approx(0.0771, abs=1e-3)
        assert intervals[0][1] == pytest.approx(0.1662, abs=1e-3)

    def test_sison_glaz_is_clipped_at_zero_for_an_empty_cell(self):
        intervals = cm.sison_glaz_intervals([30, 25, 0, 60, 46])
        assert intervals[2][0] == 0.0


class TestPairedIntervals:
    def test_tango_is_symmetric_under_swapping_the_arms(self):
        lower, upper = cm.tango_interval(10, 3, 100)
        swapped = cm.tango_interval(3, 10, 100)
        assert (lower, upper) == pytest.approx((-swapped[1], -swapped[0]), abs=1e-6)

    def test_tango_with_no_discordance_is_symmetric_about_zero(self):
        lower, upper = cm.tango_interval(0, 0, 164)
        assert lower == pytest.approx(-upper, abs=1e-6)
        assert upper == pytest.approx(0.0229, abs=1e-4)

    def test_tango_restricted_mle_maximises_the_likelihood(self):
        """The closed form is the root of the score equation; check it numerically."""

        from scipy import optimize

        b, c, n, delta = 7, 3, 60, 0.02

        def negative_log_likelihood(p21: float) -> float:
            p12 = p21 + delta
            p11 = 1 - p12 - p21
            return -(b * math.log(p12) + c * math.log(p21) + (n - b - c) * math.log(p11))

        numeric = optimize.minimize_scalar(
            negative_log_likelihood, bounds=(1e-9, (1 - delta) / 2 - 1e-9), method="bounded"
        ).x
        assert cm._tango_restricted_p21(b, c, n, delta) == pytest.approx(numeric, abs=1e-6)

    def test_tango_approaches_wald_for_large_counts(self):
        b, c, n = 30, 20, 200
        estimate = (b - c) / n
        wald_half = 1.96 * math.sqrt((b + c - (b - c) ** 2 / n) / n**2)
        lower, upper = cm.tango_interval(b, c, n)
        assert lower == pytest.approx(estimate - wald_half, abs=0.01)
        assert upper == pytest.approx(estimate + wald_half, abs=0.01)

    def test_newcombe_brackets_the_estimate(self):
        lower, upper = cm.newcombe_paired_interval(19, 1, 2, 27)
        assert lower < (1 - 2) / 49 < upper

    def test_mcnemar_exact_matches_the_readme_lane_comparison(self):
        """README section 7.2 quotes p = 0.219 for the 2.5-versus-3.7 lane comparison."""

        assert cm.mcnemar_exact_p(1, 2) == 1.0
        assert round(cm.mcnemar_exact_p(6, 0), 5) == 0.03125

    def test_paired_comparison_reports_both_intervals_and_the_2x2(self):
        first = [True] * 21 + [False] * 28
        second = [True] * 20 + [False] * 29
        report = cm.paired_comparison(first, second)
        assert report["table"] == {"both": 20, "first_only": 1, "second_only": 0, "neither": 28}
        assert report["discordant"] == 1
        assert report["tango_95"][0] < 0 < report["tango_95"][1]


class TestMinimumDetectableEffect:
    """Section 3.5, enumerated on 2026-09-06 and quoted in the plan."""

    def test_primary_parameterisation_at_the_corrected_anchor(self):
        assert round(cm.mcnemar_power(2 / 48 / 2 + 0.05, 2 / 48 / 2, 164), 2) == 0.48
        assert round(cm.mcnemar_power(2 / 48 / 2 + 0.08, 2 / 48 / 2, 164), 2) == 0.83

    def test_primary_parameterisation_at_the_uncorrected_anchor(self):
        assert round(cm.mcnemar_power(3 / 49 / 2 + 0.05, 3 / 49 / 2, 164), 2) == 0.41
        assert round(cm.mcnemar_power(3 / 49 / 2 + 0.09, 3 / 49 / 2, 164), 2) == 0.82

    def test_secondary_parameterisation_is_the_optimistic_one(self):
        pi0 = 3 / 49
        assert round(cm.mcnemar_power((pi0 + 0.05) / 2, (pi0 - 0.05) / 2, 164), 2) == 0.70

    def test_the_tables_name_eight_and_nine_points(self):
        tables = cm.mde_tables(deltas=(0.07, 0.08, 0.09))
        assert tables["corrected"]["primary"]["mde_at_80_percent"] == 0.08
        assert tables["uncorrected"]["primary"]["mde_at_80_percent"] == 0.09
        assert tables["uncorrected"]["secondary"]["power_by_delta"]["0.07"] is None


class TestAgreementCoefficients:
    def test_ac1_kappa_and_pabak_on_a_hand_computed_table(self):
        pairs = [("A", "A")] * 40 + [("A", "B")] * 5 + [("B", "A")] * 3 + [("B", "B")] * 12
        report = cm.agreement_coefficients(pairs, ("A", "B"))
        assert report["percent_agreement"] == 0.867
        assert report["cohen_kappa"] == 0.66
        assert report["gwet_ac1"] == 0.781
        assert report["pabak"] == 0.733

    def test_ordinal_weights_follow_the_support_gradient(self):
        weights = cm._ordinal_weights(cm.ATTRIBUTION_ORDER)
        assert weights[("ATTRIBUTABLE", "ATTRIBUTABLE")] == 1.0
        assert weights[("ATTRIBUTABLE", "EXTRAPOLATORY")] == pytest.approx(5 / 6)
        assert weights[("ATTRIBUTABLE", "CONTRADICTORY")] == 0.0

    def test_ac2_with_identity_weights_is_ac1(self):
        pairs = [("A", "A")] * 10 + [("A", "B")] * 2 + [("B", "B")] * 4
        plain = cm.agreement_coefficients(pairs, ("A", "B"))
        ordinal = cm.agreement_coefficients(pairs, ("A", "B"), ordinal=True)
        # With two categories the ordinal weight matrix is the identity.
        assert ordinal["gwet_ac2_ordinal"] == plain["gwet_ac1"]

    def test_auc_by_the_mann_whitney_identity(self):
        assert cm.auc_mann_whitney([0.9, 0.8, 0.7], [0.1, 0.2, 0.75]) == pytest.approx(8 / 9)

    def test_operating_point_keeps_ninety_percent_of_attributable_unflagged(self):
        scores = [i / 10 for i in range(1, 11)]
        threshold = cm._threshold_for_specificity(scores)
        assert sum(1 for s in scores if s >= threshold) == 9


class TestEstimators:
    def test_verbosity_reference_runs_from_five_to_forty_percent(self):
        assert round(cm.verbosity_reference(0.05, [1]), 4) == 0.05
        assert round(cm.verbosity_reference(0.05, [10]), 4) == 0.4013

    def test_stratified_presence_weights_the_census_stratum_by_its_size(self):
        report = cm.stratified_presence(
            gate_blocked_present=3,
            gate_blocked_total=14,
            sample_present=5,
            sample_size=20,
            model_declared_total=77,
        )
        assert report["estimate"] == round((3 + 77 * 0.25) / 91, 3)
        assert report["fpc"] == round(1 - 20 / 77, 3)
        plain = report["interval_95_rescaled_wilson"]
        corrected = report["interval_95_rescaled_wilson_fpc"]
        assert corrected[1] - corrected[0] < plain[1] - plain[0]

    def test_difference_estimator_corrects_by_the_sampled_discrepancy(self):
        report = cm.difference_estimator(
            judge_rate_over_stratum=0.3,
            stratum_size=77,
            sample_human=[True, True, False, False],
            sample_judge=[True, False, False, False],
        )
        assert report["mean_discrepancy"] == 0.25
        assert report["estimate"] == 0.55

    def test_clustered_bootstrap_is_seeded_and_reports_undefined_resamples(self):
        clusters = [[1, 0, 0], [1, 1], [0], [0, 0, 1]]
        first = cm.clustered_bootstrap(clusters, lambda xs: sum(xs) / len(xs), resamples=500)
        second = cm.clustered_bootstrap(clusters, lambda xs: sum(xs) / len(xs), resamples=500)
        assert first == second
        assert first["undefined_resamples"] == 0


class TestDerivations:
    """Section 4.4: support and bucket are derived mechanically, never at the keyboard."""

    def test_one_irrelevant_citation_is_a_citation_defect_not_a_wrong_answer(self):
        claim = {
            "joint_attribution": "ATTRIBUTABLE",
            "pair_attribution": {"EV_a": "ATTRIBUTABLE", "EV_b": "NO_SUPPORT"},
        }
        assert cm.claim_support(claim) == "PARTIAL"
        assert cm.answered_bucket({"claims": [claim]}) == "ANSWERED_DEFECTIVE"

    def test_joint_no_support_is_wrong(self):
        claim = {"joint_attribution": "NO_SUPPORT", "pair_attribution": {"EV_a": "ATTRIBUTABLE"}}
        assert cm.claim_support(claim) == "UNSUPPORTED"
        assert cm.answered_bucket({"claims": [claim]}) == "ANSWERED_WRONG"

    def test_eligibility_drop_and_presentation_defect_move_defective(self):
        supported = {
            "joint_attribution": "ATTRIBUTABLE",
            "pair_attribution": {"EV_a": "ATTRIBUTABLE"},
        }
        assert cm.answered_bucket({"claims": [supported]}) == "ANSWERED_CORRECT"
        assert (
            cm.answered_bucket({"claims": [supported], "presentation_defect": True})
            == "ANSWERED_DEFECTIVE"
        )
        assert (
            cm.answered_bucket({"claims": [dict(supported, eligibility_drop=True)]})
            == "ANSWERED_DEFECTIVE"
        )

    def test_an_unlabelable_claim_leaves_the_record_unclassified(self):
        claim = {"joint_attribution": "UNLABELABLE", "pair_attribution": {}}
        assert cm.answered_bucket({"claims": [claim]}) is None

    def test_five_trigger_classes(self):
        claim = {
            "joint_attribution": "EXTRAPOLATORY",
            "pair_attribution": {"EV_a": "NO_SUPPORT"},
            "agreement": "PARTIAL",
            "eligibility_drop": True,
        }
        assert cm.claim_is_flagged(claim, {"presentation_defect": True}) == list(cm.TRIGGER_CLASSES)
        clean = {
            "joint_attribution": "ATTRIBUTABLE",
            "pair_attribution": {"EV_a": "ATTRIBUTABLE"},
            "agreement": "AGREES",
            "eligibility_drop": False,
        }
        assert cm.claim_is_flagged(clean, {"presentation_defect": False}) == []

    def test_q4_outcome_rules(self):
        assert cm._q4_outcome([]) == (False, False)
        assert cm._q4_outcome([{"agreement": "NOT_ADJUDICABLE"}]) == (None, True)
        assert cm._q4_outcome([{"agreement": "PARTIAL"}]) == (False, False)
        assert cm._q4_outcome([{"agreement": "AGREES"}, {"agreement": "DISAGREES"}]) == (
            False,
            False,
        )
        assert cm._q4_outcome([{"agreement": "AGREES"}, {"agreement": "NOT_ADJUDICABLE"}]) == (
            True,
            False,
        )

    def test_judge_derived_abstention_buckets_are_withheld_below_the_floor(self):
        run = {
            "results": [
                {"question_id": "Q1", "generation": {"abstained": True, "reason_code": "X"}},
                {"question_id": "Q2", "generation": None, "gate_reason": "INCOMPLETE"},
            ]
        }
        labels = {
            "records": {},
            "gate_blocked": {"Q2": {"gate_right": True, "dak_has_answer": False}},
        }
        oracle = {"records": {"Q1": {"verdict": "PRESENT"}}}
        withheld = cm.derive_buckets(run, labels, oracle, oracle_cleared_floor=False)
        assert withheld["buckets"] == {"Q1": None, "Q2": "ABSTAINED_CORRECT"}
        cleared = cm.derive_buckets(run, labels, oracle, oracle_cleared_floor=True)
        assert cleared["buckets"]["Q1"] == "ABSTAINED_AVOIDABLE"
        assert cleared["sources"]["Q1"] == "judge"

    def test_a_legacy_quota_failure_is_an_error_record(self):
        record = {"generation": {"abstained": True, "reason_code": "GENERATION_UNAVAILABLE"}}
        assert cm.outcome_of(record) == "ERROR"
        assert cm.outcome_of({"generation": {"error": "429", "abstained": None}}) == "ERROR"
        assert cm.outcome_of({"generation": None}) == "ABSTAINED"


@pytest.mark.skipif(not RESULTS.exists(), reason="published evidence is not present")
class TestSectionsOnPublishedRuns:
    def _inputs(self, tmp_path: Path, **paths: Path):
        namespace = cm.build_parser().parse_args([])
        for name, path in paths.items():
            setattr(namespace, name, path)
        return cm.Inputs(namespace)

    def test_placeholders_recompute_from_the_stage2_run_without_moving(self, tmp_path):
        """The plan's placeholders were read off this file, so recomputation reproduces them."""

        inputs = self._inputs(
            tmp_path,
            production_a=RESULTS / "coverage-stage2-gemini-3.7-flash.json",
            questions=QUESTIONS,
        )
        section = cm.section_placeholders(inputs)
        assert section["moved_beyond_tolerance"] == []
        for key, placeholder in cm.STAGE2_PLACEHOLDERS.items():
            assert section["recomputed"][key] == placeholder, key
        assert section["strata"]["chapter"]["6"] == {"answered": 21, "total": 71}
        assert section["strata"]["ely_form"]["4"] == {"answered": 0, "total": 4}

    def test_a_section_fails_loudly_without_its_input(self, tmp_path):
        inputs = self._inputs(
            tmp_path,
            production_a=RESULTS / "coverage-stage2-gemini-3.7-flash.json",
            questions=QUESTIONS,
        )
        with pytest.raises(cm.MissingInput, match="--labels"):
            cm.section_1_2(inputs)

    def test_noise_floor_of_a_run_against_itself_is_zero(self, tmp_path):
        stage2 = RESULTS / "coverage-stage2-gemini-3.7-flash.json"
        naive = RESULTS / "coverage-stage1-naive-baseline.json"
        inputs = self._inputs(tmp_path, production_a=stage2, production_b=stage2, naive=naive)
        section = cm.section_3_5(inputs)
        noise = section["noise_floor"]
        assert noise["answered_versus_abstained_disagreement"]["count"] == 0
        assert section["negative_control_a_versus_b"]["mcnemar_exact_p"] == 1.0
        # The naive file carries one legacy quota failure, excluded from the pairing.
        assert section["q5_production_a_versus_naive"]["error_pairs_excluded"] == 1
        assert section["q5_production_a_versus_naive"]["n"] == 48
        assert section["mde"]["corrected"]["primary"]["mde_at_80_percent"] == 0.08

    def test_write_mde_records_both_anchors_and_the_prior_grid(self, tmp_path):
        inputs = self._inputs(
            tmp_path, production_a=RESULTS / "coverage-stage2-gemini-3.7-flash.json"
        )
        document = cm.write_mde(tmp_path / "mde.json", inputs)
        written = json.loads((tmp_path / "mde.json").read_text(encoding="utf-8"))
        assert written["mde"]["uncorrected"]["secondary"]["mde_at_80_percent"] == 0.06
        assert "0.05" in written["expected_half_widths"]["grid"]
        assert document["half_width_source"] == "production A"
