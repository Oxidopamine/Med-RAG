"""The closed-book arm and the research-lane Gemini helper of the correctness measurement plan.

Three things are fixed by Section 3.4 and are asserted here rather than trusted: the
closed-book prompt is the production prompt minus its passage rules and nothing else; the
helper sends the request the serving adapter would (schema stripped of decoding bounds,
same decoding parameters) and classifies failures the way the coverage harness does; and
the arm's output has the harness's shape, so every downstream reader takes it unchanged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.reasoning.answer_service import SYSTEM_PROMPT  # noqa: E402
from app.reasoning.gemini_adapters import GeminiAdapterParameters  # noqa: E402


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gemini_json = _load("gemini_json")
closed_book = _load("run_closed_book")
runner = _load("run_mvp_coverage_stage1")

PARAMETERS = GeminiAdapterParameters(
    model_id="gemini-3.7-flash",
    gcp_project_id="project",
    gcp_region="global",
    max_output_tokens=8192,
    temperature=0.0,
    seed=20260906,
)


def _part(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, thought=False)


def _response(text: str, *, finish_reason: str = "STOP") -> SimpleNamespace:
    return SimpleNamespace(
        prompt_feedback=SimpleNamespace(block_reason=None),
        candidates=[
            SimpleNamespace(
                finish_reason=finish_reason, content=SimpleNamespace(parts=[_part(text)])
            )
        ],
    )


class _Client:
    """Scripted responses or exceptions, and the configs it was called with."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.configs: list = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, *, model: str, contents: str, config) -> SimpleNamespace:
        self.configs.append(config)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestPromptDerivation:
    def test_the_closed_book_prompt_keeps_the_two_non_passage_rules(self):
        prompt = closed_book.CLOSED_BOOK_PROMPT
        assert prompt.startswith("You answer clinical questions.")
        assert "knows_answer" in prompt
        assert "Do not state a claim unless it bears on the question asked." in prompt
        for passage_rule in ("Use only the supplied passages", "evidence_id", "conflict"):
            assert passage_rule not in prompt

    def test_the_production_prompt_still_has_the_rules_the_arm_removes(self):
        """If production's rules change, the derivation in Section 3.4 has to be redone."""

        assert "Use only the supplied passages" in SYSTEM_PROMPT
        assert "Every claim must cite the evidence_id" in SYSTEM_PROMPT
        assert "Report it as a conflict" in SYSTEM_PROMPT
        assert "Do not restate a passage as a claim unless it bears on the question asked." in (
            SYSTEM_PROMPT
        )

    def test_source_priming_is_appended_only_when_asked(self):
        assert closed_book.user_content("Q?", source_primed=False) == "Question:\nQ?"
        assert closed_book.SOURCE_PRIMED_SUFFIX.strip() in closed_book.user_content(
            "Q?", source_primed=True
        )


class TestGeminiJsonClient:
    async def test_the_request_strips_decoding_bounds_and_pins_the_binding(self):
        client = _Client(
            [
                _response(
                    '{"knows_answer": true, "claims": [{"text": "x"}], "insufficiency_note": null}'
                )
            ]
        )
        helper = gemini_json.GeminiJsonClient(PARAMETERS, client=client)
        schema = gemini_json.closed_schema(closed_book.ClosedBookAnswer)
        result = await helper.generate_json(system_prompt="S", user_content="U", schema=schema)
        assert result["error"] is None
        assert result["json"]["knows_answer"] is True
        config = client.configs[0]
        assert config.response_mime_type == "application/json"
        assert config.temperature == 0.0 and config.seed == 20260906
        assert config.max_output_tokens == 8192
        assert config.thinking_config is None
        sent = str(config.response_json_schema)
        assert "maxLength" not in sent and "minLength" not in sent and "maxItems" not in sent
        assert result["config_sha256"] == helper.config_sha256("S", schema)
        assert result["prompt_sha256"] == gemini_json.sha256_text("U")

    async def test_a_quota_failure_is_retried_then_recorded(self):
        client = _Client([RuntimeError("429 RESOURCE_EXHAUSTED")] * 5)
        helper = gemini_json.GeminiJsonClient(PARAMETERS, client=client)
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        result = await helper.generate_json(
            system_prompt="S", user_content="U", schema={}, sleep=sleep
        )
        assert result["error_class"] == "RESOURCE_EXHAUSTED"
        assert result["attempts"] == 5
        assert sleeps == [2.0, 4.0, 8.0, 16.0]

    async def test_a_declined_finish_is_recorded_once(self):
        client = _Client([_response("{}", finish_reason="SAFETY")])
        helper = gemini_json.GeminiJsonClient(PARAMETERS, client=client)
        result = await helper.generate_json(system_prompt="S", user_content="U", schema={})
        assert result["error_class"] == "DECLINED"
        assert result["attempts"] == 1

    async def test_truncated_json_is_a_model_behaviour(self):
        client = _Client([_response('{"knows_answer": tr', finish_reason="MAX_TOKENS")])
        helper = gemini_json.GeminiJsonClient(PARAMETERS, client=client)
        result = await helper.generate_json(system_prompt="S", user_content="U", schema={})
        assert result["error_class"] == "MAX_TOKENS"

    async def test_unparseable_text_is_a_contract_violation(self):
        client = _Client([_response("not json")])
        helper = gemini_json.GeminiJsonClient(PARAMETERS, client=client)
        result = await helper.generate_json(system_prompt="S", user_content="U", schema={})
        assert result["error_class"] == "CONTRACT_VALIDATION"

    @pytest.mark.parametrize(
        "message",
        [
            "429 RESOURCE_EXHAUSTED",
            "generation model declined the request: SAFETY",
            "generation exceeded the output budget before completing",
            "generation returned no candidate",
            "ReadTimeout",
        ],
    )
    def test_the_helper_and_the_harness_classify_alike(self, message):
        assert gemini_json.classify_error(message) == runner.classify_generation_error(message)


class TestClosedBookRecords:
    def _result(self, payload) -> dict:
        return {
            "json": payload,
            "error": None,
            "attempts": 1,
            "prompt_sha256": "p",
            "config_sha256": "c",
        }

    def test_an_answer_becomes_claims_with_no_evidence_ids(self):
        generation = closed_book.generation_record(
            self._result(
                {
                    "knows_answer": True,
                    "claims": [{"text": "Start ART."}],
                    "insufficiency_note": None,
                }
            )
        )
        assert generation["abstained"] is False
        assert generation["claims"] == [{"text": "Start ART.", "evidence_ids": []}]
        assert generation["verification"]["supported_claims"] == 0

    def test_not_knowing_is_a_closed_book_abstention_not_model_declared_insufficient(self):
        generation = closed_book.generation_record(
            self._result({"knows_answer": False, "claims": [], "insufficiency_note": "unknown"})
        )
        assert generation["abstained"] is True
        assert generation["reason_code"] == "CLOSED_BOOK_DOES_NOT_KNOW"
        assert generation["knows_answer"] is False

    def test_a_contract_violation_is_an_error_record(self):
        generation = closed_book.generation_record(self._result({"claims": "no"}))
        assert generation["abstained"] is None
        assert generation["error_class"] == "CONTRACT_VALIDATION"

    def test_the_summary_counts_like_the_harness(self):
        records = [
            {"generation": {"abstained": False, "claims": [{"text": "a"}, {"text": "b"}]}},
            {"generation": {"abstained": True, "reason_code": "CLOSED_BOOK_DOES_NOT_KNOW"}},
            {"generation": {"error": "x", "error_class": "DECLINED", "abstained": None}},
        ]
        summary = closed_book.summarize(records)
        assert (summary["answered"], summary["abstained"], summary["error_records"]) == (1, 1, 1)
        assert summary["claims"] == 2
        assert summary["error_classes"] == {"DECLINED": 1}
        assert "p_wrong" not in " ".join(summary)

    def test_downstream_readers_take_the_closed_book_shape_unchanged(self):
        record = {
            "question_id": "Q1",
            "retrieval": None,
            "generation": {"abstained": False, "claims": [{"text": "a", "evidence_ids": []}]},
        }
        assert runner.record_outcome(record) == "ANSWERED"
        assert runner.summarize([{**record, "gate_reason": None}])["answered"] == 1
