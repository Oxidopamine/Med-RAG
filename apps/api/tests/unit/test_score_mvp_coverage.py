"""The scorer turns a classification into the pre-registered decision, so it must refuse.

`p_answered` is defined over the whole draw and `ANSWERED_WRONG` stops the MVP on a single
occurrence. Both properties are destroyed quietly rather than loudly: a missing bucket
scored as anything at all shrinks the denominator, and a bucket transposed against what
the system did moves mass across the answered/abstained boundary. Neither produces an
error at the arithmetic, only a wrong number that looks right - which is what these tests
are for.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scorer = _load("score_mvp_coverage")
runner = _load("run_mvp_coverage_stage1")

BUCKETS = runner.BUCKETS


def _record(question_id: str, *, answered: bool, bucket: str | None = None, chapter_no: int = 2):
    generation = None
    if answered:
        generation = {"abstained": False, "claims": [], "verification": {}}
    else:
        generation = {"abstained": True, "reason_code": "MODEL_DECLARED_INSUFFICIENT"}
    return {
        "question_id": question_id,
        "chapter_no": chapter_no,
        "chapter": "Testing",
        "generation": generation,
        "bucket": bucket,
    }


def _run(*records) -> dict:
    return {"results": list(records), "bucket_scheme": list(BUCKETS), "registered": True}


def _score(run: dict):
    buckets = scorer.resolve_buckets(run, None)
    assert scorer.validate(run, buckets, BUCKETS) == []
    return scorer.score(run, buckets, runner)


class TestItRefuses:
    def test_an_unclassified_question_is_an_error_not_a_gap(self) -> None:
        run = _run(
            _record("Q1", answered=True, bucket="ANSWERED_CORRECT"),
            _record("Q2", answered=True, bucket=None),
        )
        problems = scorer.validate(run, scorer.resolve_buckets(run, None), BUCKETS)
        assert any("not classified" in problem for problem in problems)

    def test_a_bucket_transposed_against_the_system_is_caught(self) -> None:
        """An answered question classified as an abstention moves p_answered's numerator."""

        run = _run(_record("Q1", answered=True, bucket="ABSTAINED_CORRECT"))
        problems = scorer.validate(run, scorer.resolve_buckets(run, None), BUCKETS)
        assert any("system ANSWERED" in problem for problem in problems)

    def test_an_abstention_classified_as_answered_is_caught(self) -> None:
        run = _run(_record("Q1", answered=False, bucket="ANSWERED_CORRECT"))
        problems = scorer.validate(run, scorer.resolve_buckets(run, None), BUCKETS)
        assert any("system ABSTAINED" in problem for problem in problems)

    def test_an_unknown_bucket_is_caught(self) -> None:
        run = _run(_record("Q1", answered=True, bucket="ANSWERED_PROBABLY_FINE"))
        problems = scorer.validate(run, scorer.resolve_buckets(run, None), BUCKETS)
        assert any("unknown bucket" in problem for problem in problems)

    def test_a_gate_abstention_counts_as_abstained(self) -> None:
        record = _record("Q1", answered=False, bucket="ABSTAINED_CORRECT")
        record["generation"] = None
        assert scorer.validate(_run(record), {"Q1": "ABSTAINED_CORRECT"}, BUCKETS) == []


class TestPWrongOutranksCoverage:
    def test_zero_occurrences_reports_a_bound_never_zero(self) -> None:
        run = _run(*[_record(f"Q{i}", answered=True, bucket="ANSWERED_CORRECT") for i in range(20)])
        result = _score(run)
        assert result["p_wrong"]["answered_wrong"] == 0
        assert result["p_wrong"]["stops_the_mvp"] is False
        # The bound is the reported quantity: "zero" is not claimable at this n.
        assert result["p_wrong"]["zero_occurrence_upper_bound_95"] > 0.1

    def test_one_occurrence_stops_the_mvp_regardless_of_coverage(self) -> None:
        records = [_record(f"Q{i}", answered=True, bucket="ANSWERED_CORRECT") for i in range(19)]
        records.append(_record("Q19", answered=True, bucket="ANSWERED_WRONG"))
        result = _score(_run(*records))
        assert result["p_wrong"]["stops_the_mvp"] is True
        # Coverage is perfect and must not rescue it.
        assert result["p_answered"]["rate"] == 1.0
        assert result["p_wrong"]["zero_occurrence_upper_bound_95"] is None


class TestTheDecisionRule:
    def test_all_three_answered_buckets_count_toward_coverage(self) -> None:
        run = _run(
            _record("Q1", answered=True, bucket="ANSWERED_CORRECT"),
            _record("Q2", answered=True, bucket="ANSWERED_DEFECTIVE"),
            _record("Q3", answered=True, bucket="ANSWERED_WRONG"),
            _record("Q4", answered=False, bucket="ABSTAINED_CORRECT"),
        )
        assert _score(run)["p_answered"]["answered"] == 3

    def test_an_interval_entirely_below_the_floor_condemns_the_corpus(self) -> None:
        records = [_record(f"Q{i}", answered=False, bucket="ABSTAINED_CORRECT") for i in range(60)]
        records[0] = _record("Q0", answered=True, bucket="ANSWERED_CORRECT")
        result = _score(_run(*records))
        assert result["p_answered"]["wilson_95"][1] < runner.STAGE1_FLOOR
        assert result["p_answered"]["decision"] == "CORPUS_CANNOT_CARRY_THE_PRODUCT"

    def test_an_interval_entirely_above_the_ceiling_proceeds(self) -> None:
        records = [_record(f"Q{i}", answered=True, bucket="ANSWERED_CORRECT") for i in range(60)]
        result = _score(_run(*records))
        assert result["p_answered"]["wilson_95"][0] > runner.STAGE1_CEILING
        assert result["p_answered"]["decision"] == "PROCEED_ON_THIS_CORPUS"

    def test_a_straddling_interval_reaches_stage_2(self) -> None:
        records = [_record(f"Q{i}", answered=True, bucket="ANSWERED_CORRECT") for i in range(26)]
        records += [
            _record(f"A{i}", answered=False, bucket="ABSTAINED_CORRECT") for i in range(23)
        ]
        result = _score(_run(*records))
        assert result["p_answered"]["decision"] == "GO_TO_STAGE_2"


class TestBucketSources:
    def test_an_external_file_supplies_buckets_the_run_lacks(self) -> None:
        run = _run(_record("Q1", answered=True, bucket=None))
        buckets = scorer.resolve_buckets(run, {"buckets": {"Q1": "ANSWERED_CORRECT"}})
        assert buckets == {"Q1": "ANSWERED_CORRECT"}
        assert scorer.validate(run, buckets, BUCKETS) == []

    def test_the_avoidable_split_is_reported_for_the_negative_set(self) -> None:
        run = _run(
            _record("Q1", answered=False, bucket="ABSTAINED_CORRECT"),
            _record("Q2", answered=False, bucket="ABSTAINED_AVOIDABLE"),
        )
        split = _score(run)["abstention_split"]
        assert split == {
            "abstained_correct": 1,
            "abstained_avoidable": 1,
            "avoidable_are_system_defects": True,
        }


def _error_record(question_id: str, *, bucket: str | None = None) -> dict:
    return {
        "question_id": question_id,
        "chapter_no": 2,
        "chapter": "Testing",
        "generation": {"error": "429", "error_class": "RESOURCE_EXHAUSTED", "abstained": None},
        "bucket": bucket,
    }


def test_error_records_are_excluded_from_every_denominator():
    """Plan Section 2.2 item 4: a missing measurement enters no denominator."""

    run = _run(
        _record("Q1", answered=True, bucket="ANSWERED_CORRECT"),
        _record("Q2", answered=False, bucket="ABSTAINED_CORRECT"),
        _error_record("Q3"),
    )
    result = _score(run)
    assert result["questions"] == 2
    assert result["error_records"]["count"] == 1
    assert result["error_records"]["question_ids"] == ["Q3"]
    assert result["p_answered"]["rate"] == 0.5
    assert result["by_chapter"]["2 Testing"]["total"] == 2


def test_an_error_record_may_not_carry_a_bucket():
    run = _run(
        _record("Q1", answered=True, bucket="ANSWERED_CORRECT"),
        _error_record("Q3", bucket="ABSTAINED_CORRECT"),
    )
    problems = scorer.validate(run, scorer.resolve_buckets(run, None), BUCKETS)
    assert len(problems) == 1
    assert "missing measurement" in problems[0]


def test_a_legacy_quota_failure_is_an_error_not_an_abstention():
    legacy = {"generation": {"abstained": True, "reason_code": "GENERATION_UNAVAILABLE"}}
    assert scorer.system_outcome(legacy) == "ERROR"
    assert scorer.system_outcome({"generation": None}) == "ABSTAINED"
