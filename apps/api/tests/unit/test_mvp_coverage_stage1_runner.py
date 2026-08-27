"""The stage-1 coverage runner's decision logic.

These tests exist because `scripts/run_mvp_coverage_stage1.py` is the instrument the
pre-registered coverage decision is read off, and three of its behaviours are the
pre-registration expressed as code rather than as prose:

* the interval is the one `docs/mvp-definition.md` quotes, to the digit;
* a question set that has not been reviewed cannot be measured by accident; and
* the runner records what the system did without deciding what it meant.

A silent defect in any of them produces a number that looks like the measurement and is
not, which is worse than a crash.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.reasoning.retrieval_service import (  # noqa: E402
    ServingPassage,
    ServingRetrievalResult,
)
from app.schemas.corpus import EvidenceRole  # noqa: E402


def _load_runner():
    """Import the script by path: `scripts/` is not a package and has no __init__."""

    path = REPO_ROOT / "scripts" / "run_mvp_coverage_stage1.py"
    spec = importlib.util.spec_from_file_location("run_mvp_coverage_stage1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because `@dataclass` resolves its own module through
    # `sys.modules[cls.__module__]`, which is absent for a spec loaded by path alone.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _passage(*roles: EvidenceRole, recommendation_bearing: bool = True) -> ServingPassage:
    return ServingPassage(
        evidence_id="EV_test",
        content_exact="text",
        evidence_roles=tuple(roles),
        source_version_id="SV_test",
        publisher_id="WHO",
        jurisdiction="WORLD",
        language="en",
        render_allowed=False,
        fused_score=0.1,
        lanes=("dense",),
        rendered_text="text",
        is_recommendation_bearing=recommendation_bearing,
    )


def _result(
    passages: tuple[ServingPassage, ...] = (),
    missing: tuple[EvidenceRole, ...] = (),
) -> ServingRetrievalResult:
    return ServingRetrievalResult(
        passages=passages,
        lane_failures=(),
        missing_required_roles=missing,
        latency_ms=1.0,
    )


class TestPreRegisteredStatistics:
    """Every interval the MVP definition states, reproduced by the code that will run it.

    The document argues its staging from these exact numbers - that 30/50 does not clear
    0.50 and 8/50 does not clear 0.25 - so an interval that disagrees with them would
    change the decision the document pre-registered.
    """

    @pytest.mark.parametrize(
        ("successes", "total", "expected"),
        [
            (15, 50, (0.191, 0.438)),
            (8, 50, (0.083, 0.285)),
            (30, 50, (0.462, 0.724)),
            (52, 150, (0.275, 0.426)),
        ],
    )
    def test_wilson_interval_matches_the_definition(self, successes, total, expected):
        lower, upper = runner.wilson_interval(successes, total)
        assert round(lower, 3) == expected[0]
        assert round(upper, 3) == expected[1]

    @pytest.mark.parametrize(
        ("total", "expected_percent"),
        [(50, 5.8), (150, 2.0), (300, 1.0)],
    )
    def test_zero_occurrence_bound_matches_the_definition(self, total, expected_percent):
        bound = runner.zero_occurrence_upper_bound(total)
        assert round(bound * 100, 1) == expected_percent

    def test_the_bound_is_exact_rather_than_the_rule_of_three(self):
        """3/n would give 6.0% at n = 50, and the definition commits to 5.8%."""

        assert round(runner.zero_occurrence_upper_bound(50) * 100, 1) != 6.0

    def test_an_empty_run_claims_no_precision(self):
        assert runner.wilson_interval(0, 0) == (0.0, 1.0)


class TestQuestionSetLoading:
    def test_excludes_unusable_items(self, tmp_path: Path):
        """`usable: false` is the set's own exclusion flag, and stage 1 runs at n = 49.

        The one excluded item in v1 is an anaphoric fragment whose referent sits in a
        preceding block; counting it would score the corpus against a question that has
        no answer because it has no subject.
        """

        path = tmp_path / "questions.json"
        path.write_text(
            '{"set_id": "S", "review_status": "REVIEWED", "items": ['
            '{"question_id": "Q1", "question": "a", "usable": true},'
            '{"question_id": "Q2", "question": "b", "usable": false},'
            '{"question_id": "Q3", "question": "c", "usable": true}]}',
            encoding="utf-8",
        )
        _, items = runner.load_questions(path, limit=None)
        assert [item.question_id for item in items] == ["Q1", "Q3"]

    def test_limit_truncates_for_harness_validation(self, tmp_path: Path):
        path = tmp_path / "questions.json"
        path.write_text(
            '{"set_id": "S", "review_status": "REVIEWED", "items": ['
            '{"question_id": "Q1", "question": "a", "usable": true},'
            '{"question_id": "Q2", "question": "b", "usable": true}]}',
            encoding="utf-8",
        )
        _, items = runner.load_questions(path, limit=1)
        assert [item.question_id for item in items] == ["Q1"]


class TestGateReason:
    """The recorded reason is the product's own vocabulary, not a paraphrase.

    An abstention that misreports why is a correctness defect under done-criterion 2,
    so the runner must not invent a reason or collapse two into one.
    """

    def test_no_passages_is_no_evidence_retrieved(self):
        assert runner.gate_reason(_result()) == "NO_EVIDENCE_RETRIEVED"

    def test_missing_role_is_an_incomplete_role_set(self):
        result = _result(
            passages=(_passage(EvidenceRole.PRIMARY_SUPPORT),),
            missing=(EvidenceRole.APPLICABILITY,),
        )
        assert runner.gate_reason(result) == "INCOMPLETE_EVIDENCE_ROLE_SET"

    def test_a_complete_set_has_no_reason(self):
        result = _result(
            passages=(_passage(EvidenceRole.PRIMARY_SUPPORT, EvidenceRole.APPLICABILITY),),
        )
        assert runner.gate_reason(result) is None

    def test_absence_of_evidence_outranks_an_incomplete_set(self):
        """With no passages at all, the honest reason is that nothing matched."""

        result = _result(missing=(EvidenceRole.PRIMARY_SUPPORT,))
        assert runner.gate_reason(result) == "NO_EVIDENCE_RETRIEVED"


class TestSummary:
    @staticmethod
    def _records(passed: int, failed: int) -> list[dict]:
        records = []
        for _ in range(passed):
            records.append(
                {"retrieval": {"is_answerable": True}, "gate_reason": None}
            )
        for _ in range(failed):
            records.append(
                {
                    "retrieval": {"is_answerable": False},
                    "gate_reason": "NO_EVIDENCE_RETRIEVED",
                }
            )
        return records

    def test_reports_the_quantity_as_an_upper_bound(self):
        """Mislabelling this as p_answered would overstate coverage by construction."""

        summary = runner.summarize(self._records(5, 5))
        assert summary["interpretation"]["quantity"] == "upper bound on p_answered"
        assert len(summary["interpretation"]["why_upper_bound"]) == 2

    def test_condemning_branch_needs_the_whole_interval_below_the_floor(self):
        """2/49 puts the interval entirely under 0.25; the corpus branch is reachable."""

        summary = runner.summarize(self._records(2, 47))
        assert summary["gate_passed_wilson_95"][1] < runner.STAGE1_FLOOR
        assert "cannot carry" in summary["interpretation"]["reading"]

    def test_a_high_rate_decides_nothing_further(self):
        """Clearing the floor on an upper bound is not evidence of coverage.

        The gate tests role completeness rather than relevance, so a high pass rate is
        consistent with a corpus that answers nothing well. The runner must not read it
        as the 'proceed' branch, which belongs to p_answered and needs grounding.
        """

        summary = runner.summarize(self._records(45, 4))
        reading = summary["interpretation"]["reading"]
        assert "decides nothing further" in reading
        assert "proceed" not in reading

    def test_straddling_the_floor_reaches_no_branch(self):
        summary = runner.summarize(self._records(8, 41))
        lower, upper = summary["gate_passed_wilson_95"]
        assert lower < runner.STAGE1_FLOOR < upper
        assert "straddles" in summary["interpretation"]["reading"]

    def test_counts_reasons_separately(self):
        records = self._records(1, 1)
        records.append(
            {
                "retrieval": {"is_answerable": False},
                "gate_reason": "INCOMPLETE_EVIDENCE_ROLE_SET",
            }
        )
        summary = runner.summarize(records)
        assert summary["gate_reason_counts"] == {
            "NO_EVIDENCE_RETRIEVED": 1,
            "INCOMPLETE_EVIDENCE_ROLE_SET": 1,
        }
        assert summary["questions_run"] == 3
        assert summary["gate_passed"] == 1


class TestRegistrationStamp:
    """A run of an unreviewed set must be distinguishable from the measurement itself."""

    @pytest.mark.parametrize(
        ("status", "registered"),
        [
            ("REVIEWED", True),
            ("REVIEWED - checked by the project owner", True),
            ("DRAFT - model-authored, not reviewed by the project owner", False),
            ("", False),
            ("draft", False),
        ],
    )
    def test_only_a_reviewed_set_counts_as_registered(self, status, registered):
        assert status.upper().startswith(runner.REVIEWED_STATUS_PREFIX) is registered

    def test_the_bucket_scheme_is_the_definitions_five(self):
        assert runner.BUCKETS == (
            "ANSWERED_CORRECT",
            "ANSWERED_DEFECTIVE",
            "ANSWERED_WRONG",
            "ABSTAINED_CORRECT",
            "ABSTAINED_AVOIDABLE",
        )
