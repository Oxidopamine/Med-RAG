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
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
STAGE2 = REPO_ROOT / "benchmarks" / "results" / "coverage-stage2-gemini-3.7-flash.json"
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


class TestGenerationWiring:
    """The --generate path is reached only at runtime, so nothing else type-checks it.

    Regression test: the first version imported `scripts.ask`, which cannot resolve
    because `scripts/` is a directory of entry points and not a package. The failure
    surfaced only when a real run reached the composer, after the embedding model had
    already loaded.
    """

    def test_the_ask_entrypoint_loads_by_path(self):
        module = runner._load_ask_module()
        assert callable(module.gemini_parameters)
        assert callable(module.vertex_parameters)

    def test_the_parameter_readers_refuse_an_unset_project(self, monkeypatch):
        """An unset project is a configuration error rather than a default to guess at."""

        module = runner._load_ask_module()
        monkeypatch.delenv("MEDRAG_VERTEX_PROJECT_ID", raising=False)
        for read in (module.gemini_parameters, module.vertex_parameters):
            with pytest.raises(SystemExit, match="MEDRAG_VERTEX_PROJECT_ID"):
                read()


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


class TestErrorRecords:
    """Plan Sections 1.1, 2.2 and 3.3: a generation failure is a missing measurement."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("... (429 RESOURCE_EXHAUSTED. {'error': {'code': 429}})", "RESOURCE_EXHAUSTED"),
            ("... (generation model declined the request: SAFETY)", "DECLINED"),
            ("... (generation model blocked the prompt: PROHIBITED_CONTENT)", "DECLINED"),
            ("... (generation exceeded the output budget before completing)", "MAX_TOKENS"),
            (
                "... (generation response did not satisfy the answer contract: 1 error)",
                "CONTRACT_VALIDATION",
            ),
            ("... (generation returned no candidate)", "NO_CANDIDATE"),
            ("... (generation returned no text part)", "NO_CANDIDATE"),
            ("... (ReadTimeout)", "OTHER"),
            (None, "OTHER"),
        ],
    )
    def test_the_cause_is_classified_from_the_adapter_message(self, message, expected):
        assert runner.classify_generation_error(message) == expected

    def test_record_outcome_reads_errors_and_legacy_quota_failures(self):
        assert runner.record_outcome({"generation": None}) == "ABSTAINED"
        insufficient = {"abstained": True, "reason_code": "MODEL_DECLARED_INSUFFICIENT"}
        assert runner.record_outcome({"generation": insufficient}) == "ABSTAINED"
        assert runner.record_outcome({"generation": {"abstained": False, "claims": [{}]}}) == (
            "ANSWERED"
        )
        error = {"error": "429", "error_class": "RESOURCE_EXHAUSTED", "abstained": None}
        assert runner.record_outcome({"generation": error}) == "ERROR"
        legacy = {"abstained": True, "reason_code": "GENERATION_UNAVAILABLE"}
        assert runner.record_outcome({"generation": legacy}) == "ERROR"

    @staticmethod
    def _record(generation: dict | None) -> dict:
        return {"retrieval": {"is_answerable": True}, "gate_reason": None, "generation": generation}

    def test_summary_excludes_error_records_and_withholds_every_bound(self):
        records = [
            self._record({"abstained": False, "claims": [{}]}),
            self._record({"abstained": False, "claims": [{}]}),
            self._record({"abstained": True, "reason_code": "MODEL_DECLARED_INSUFFICIENT"}),
            self._record({"error": "429", "error_class": "RESOURCE_EXHAUSTED", "abstained": None}),
        ]
        summary = runner.summarize(records)
        assert (summary["answered"], summary["abstained"], summary["error_records"]) == (2, 1, 1)
        assert summary["error_classes"] == {"RESOURCE_EXHAUSTED": 1}
        assert summary["missing_measurements"] == 1
        assert summary["model_behaviour_errors"] == 0
        assert summary["answered_rate"] == round(2 / 3, 4)
        assert summary["p_wrong_zero_occurrence_upper_bound_95_questions"] is None
        assert summary["p_wrong_zero_occurrence_upper_bound_95_answered"] is None

    def test_model_behaviours_are_counted_under_their_own_class(self):
        records = [
            self._record({"error": "budget", "error_class": "MAX_TOKENS", "abstained": None}),
            self._record({"abstained": False, "claims": [{}]}),
        ]
        summary = runner.summarize(records)
        assert summary["model_behaviour_errors"] == 1
        assert summary["missing_measurements"] == 0
        assert summary["p_wrong_zero_occurrence_upper_bound_95_answered"] is None

    @pytest.mark.skipif(not STAGE2.exists(), reason="published evidence is not present")
    def test_both_bounds_on_the_stage2_record_list(self):
        """Plan Section 2.2 item 3: 0.0181 over 164 questions and 0.0402 over 73 answered."""

        records = json.loads(STAGE2.read_text(encoding="utf-8"))["results"]
        summary = runner.summarize(records)
        assert (summary["answered"], summary["abstained"], summary["error_records"]) == (73, 91, 0)
        assert summary["p_wrong_zero_occurrence_upper_bound_95_questions"] == 0.0181
        assert summary["p_wrong_zero_occurrence_upper_bound_95_answered"] == 0.0402

    def test_merge_replaces_by_question_id_and_keeps_the_base_order(self):
        base = [{"question_id": q, "v": 1} for q in ("Q1", "Q2", "Q3")]
        fresh = [{"question_id": "Q2", "v": 2}, {"question_id": "Q9", "v": 2}]
        merged = runner.merge_records(base, fresh)
        assert [(r["question_id"], r["v"]) for r in merged] == [
            ("Q1", 1),
            ("Q2", 2),
            ("Q3", 1),
            ("Q9", 2),
        ]


class _Composer:
    """A composer whose outcomes are scripted: an answer, an insufficiency, or an error."""

    def __init__(self, outcomes: list[str]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def compose(self, question: str, passages: tuple) -> SimpleNamespace:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if outcome == "answer":
            return SimpleNamespace(
                abstention=None,
                claims=[SimpleNamespace(text="c", evidence_ids=["EV_test"])],
                conflicts=[],
                verification=SimpleNamespace(
                    rendered_claims=1, supported_claims=1, withheld_claims=0
                ),
            )
        if outcome == "insufficient":
            return SimpleNamespace(
                abstention=SimpleNamespace(
                    reason_code="MODEL_DECLARED_INSUFFICIENT", message="not covered"
                )
            )
        return SimpleNamespace(
            abstention=SimpleNamespace(reason_code="GENERATION_UNAVAILABLE", message=outcome)
        )


QUOTA = "The answer service could not produce a verifiable answer (429 RESOURCE_EXHAUSTED)"
RETRIEVED = _result((_passage(EvidenceRole.PRIMARY_SUPPORT),))


class TestComposeAnswerRetry:
    """Plan Section 3.3: the fix is in compose_answer, classified by cause."""

    async def test_a_quota_failure_is_retried_with_backoff_then_answered(self):
        composer = _Composer([QUOTA, QUOTA, "answer"])
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        record = await runner.compose_answer(composer, "q", RETRIEVED, sleep=sleep)
        assert record["abstained"] is False
        assert composer.calls == 3
        assert sleeps == [2.0, 4.0]

    async def test_five_quota_failures_become_an_error_record(self):
        composer = _Composer([QUOTA] * 5)
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        record = await runner.compose_answer(composer, "q", RETRIEVED, sleep=sleep)
        assert record == {
            "error": QUOTA,
            "error_class": "RESOURCE_EXHAUSTED",
            "abstained": None,
            "attempts": 5,
        }
        assert len(sleeps) == 4

    async def test_a_declined_finish_is_an_error_without_retry(self):
        composer = _Composer(["(generation model declined the request: SAFETY)"])

        async def sleep(seconds: float) -> None:
            raise AssertionError("a declined finish is not retried")

        record = await runner.compose_answer(composer, "q", RETRIEVED, sleep=sleep)
        assert record["error_class"] == "DECLINED"
        assert record["attempts"] == 1
        assert record["abstained"] is None

    async def test_an_output_budget_overrun_is_a_model_behaviour(self):
        composer = _Composer(["(generation exceeded the output budget before completing)"])
        record = await runner.compose_answer(composer, "q", RETRIEVED)
        assert record["error_class"] == "MAX_TOKENS"

    async def test_a_model_declared_insufficiency_is_still_an_abstention(self):
        composer = _Composer(["insufficient"])
        record = await runner.compose_answer(composer, "q", RETRIEVED)
        assert record == {
            "abstained": True,
            "reason_code": "MODEL_DECLARED_INSUFFICIENT",
            "message": "not covered",
        }


class TestRunNaming:
    """Plan Section 3.3: name the run, and re-run errored questions into a merge."""

    REQUIRED = [
        "--questions", "q.json", "--bundle", "b.json", "--vectors", "v.json",
        "--collection", "c", "--output", "o.json",
    ]

    def test_the_parser_accepts_the_plan_flags(self):
        arguments = runner.build_parser().parse_args(
            [
                *self.REQUIRED,
                "--run-label", "production-a",
                "--notes", "09:00-09:26",
                "--only-question-ids", "MVPQ-C3-057,MVPQ-C3-063",
                "--merge-base", "base.json",
            ]
        )
        assert arguments.run_label == "production-a"
        assert arguments.notes == "09:00-09:26"
        assert arguments.only_question_ids == "MVPQ-C3-057,MVPQ-C3-063"
        assert arguments.merge_base == Path("base.json")

    def test_write_output_records_the_label_and_notes(self, tmp_path: Path):
        questions = tmp_path / "q.json"
        questions.write_text('{"set_id": "S", "review_status": "REVIEWED", "items": []}')
        output = tmp_path / "out.json"
        arguments = runner.build_parser().parse_args(
            [
                "--questions", str(questions), "--bundle", "b.json", "--vectors", "v.json",
                "--collection", "c", "--output", str(output),
                "--run-label", "production-a", "--notes", "wall clock",
            ]
        )
        runner.write_output(arguments, {"set_id": "S"}, [], registered=True, elapsed=1.0)
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["run_label"] == "production-a"
        assert payload["notes"] == "wall clock"
        assert payload["summary"]["error_records"] == 0
