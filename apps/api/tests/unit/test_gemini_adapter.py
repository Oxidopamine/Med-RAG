"""The Gemini lane is a comparator, so what is tested is that it fails the same way.

Every check here exists because the answer lane's safety argument rests on generation
failures becoming abstentions rather than partial answers. The schema test is the
load-bearing one: bounds are removed from what the model is told, and the point is that
removing them changes nothing about what is accepted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.reasoning.answer_service import GroundedAnswerComposer, RetrievedPassage
from app.reasoning.gemini_adapters import (
    GeminiAdapterParameters,
    GeminiGenerationAdapter,
    gemini_answer_schema,
)
from app.reasoning.generation_adapters import GenerationUnavailableError
from app.reasoning.generation_schemas import AbstentionReason, ModelAnswer

PARAMETERS = GeminiAdapterParameters(
    model_id="gemini-flash-latest",
    gcp_project_id="med-rag",
    gcp_region="global",
)

PASSAGES = (
    RetrievedPassage(evidence_id="EV_aaa", text="Monitor viral load at six months."),
)


def _part(text: str, *, thought: bool = False) -> SimpleNamespace:
    return SimpleNamespace(text=text, thought=thought)


def _response(
    *parts: SimpleNamespace,
    finish_reason: str | None = "STOP",
    block_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_feedback=SimpleNamespace(block_reason=block_reason),
        candidates=[
            SimpleNamespace(
                finish_reason=finish_reason,
                content=SimpleNamespace(parts=list(parts)),
            )
        ],
    )


class StubClient:
    """Stands in for ``genai.Client``, which is only reachable through ``.aio``."""

    def __init__(self, response: object) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _adapter(response: object) -> tuple[GeminiGenerationAdapter, StubClient]:
    client = StubClient(response)
    return GeminiGenerationAdapter(PARAMETERS, client=client), client


ANSWER_JSON = json.dumps(
    {
        "sufficient_evidence": True,
        "claims": [{"text": "Monitor at six months.", "evidence_ids": ["EV_aaa"]}],
        "conflicts": [],
        "insufficiency_note": None,
    }
)


def test_decoding_bounds_are_stripped_from_the_hint_only() -> None:
    serialized = json.dumps(gemini_answer_schema())
    # Gemini rejects the schema outright with these present.
    for keyword in ("maxLength", "minLength", "maxItems", "minItems"):
        assert keyword not in serialized
    # The structure the model is being asked for still survives.
    assert "sufficient_evidence" in serialized
    assert "evidence_ids" in serialized

    # And nothing about enforcement moved: ModelAnswer still rejects what the schema
    # no longer describes.
    with pytest.raises(ValueError):
        ModelAnswer.model_validate(
            {
                "sufficient_evidence": True,
                "claims": [{"text": "x" * 4_001, "evidence_ids": ["EV_aaa"]}],
            }
        )


async def test_valid_response_parses() -> None:
    adapter, client = _adapter(_response(_part(ANSWER_JSON)))
    answer = await adapter.generate(system_prompt="S", user_content="U")
    assert answer.sufficient_evidence is True
    assert answer.claims[0].evidence_ids == ["EV_aaa"]
    assert client.calls[0]["model"] == "gemini-flash-latest"


async def test_thought_parts_are_not_read_as_the_answer() -> None:
    """Flash models emit reasoning parts; concatenating them would corrupt the JSON."""

    adapter, _ = _adapter(
        _response(_part("deliberating about the passages", thought=True), _part(ANSWER_JSON))
    )
    answer = await adapter.generate(system_prompt="S", user_content="U")
    assert answer.claims[0].text == "Monitor at six months."


@pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST"])
async def test_a_declined_candidate_is_unavailable_not_empty(reason: str) -> None:
    adapter, _ = _adapter(_response(_part(ANSWER_JSON), finish_reason=reason))
    with pytest.raises(GenerationUnavailableError, match="declined"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_a_blocked_prompt_is_unavailable() -> None:
    adapter, _ = _adapter(_response(_part(ANSWER_JSON), block_reason="SAFETY"))
    with pytest.raises(GenerationUnavailableError, match="blocked the prompt"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_truncated_output_is_rejected_rather_than_parsed() -> None:
    """A cut-off answer is invalid, not short - it must never reach the grounding check."""

    adapter, _ = _adapter(
        _response(_part(ANSWER_JSON[:40]), finish_reason="MAX_TOKENS")
    )
    with pytest.raises(GenerationUnavailableError, match="output budget"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_a_candidate_with_no_text_part_is_rejected() -> None:
    adapter, _ = _adapter(_response(_part("thinking only", thought=True)))
    with pytest.raises(GenerationUnavailableError, match="no text part"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_no_candidate_is_rejected() -> None:
    adapter, _ = _adapter(
        SimpleNamespace(prompt_feedback=SimpleNamespace(block_reason=None), candidates=[])
    )
    with pytest.raises(GenerationUnavailableError, match="no candidate"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_malformed_json_does_not_escape_as_an_answer() -> None:
    adapter, _ = _adapter(_response(_part("{not json")))
    with pytest.raises(GenerationUnavailableError, match="answer contract"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_transport_failure_is_wrapped() -> None:
    adapter, _ = _adapter(RuntimeError("503 backend unavailable"))
    with pytest.raises(GenerationUnavailableError, match="503"):
        await adapter.generate(system_prompt="S", user_content="U")


async def test_a_declined_generation_abstains_through_the_composer() -> None:
    """The whole point of the wrapping: the lane abstains instead of raising."""

    adapter, _ = _adapter(_response(_part(ANSWER_JSON), finish_reason="SAFETY"))
    composed = await GroundedAnswerComposer(adapter).compose("q", PASSAGES)
    assert composed.abstention is not None
    assert composed.abstention.reason_code == AbstentionReason.GENERATION_UNAVAILABLE.value


async def test_ungrounded_claims_are_still_discarded_on_this_lane() -> None:
    """Swapping the vendor must not change what counts as grounded."""

    payload = json.dumps(
        {
            "sufficient_evidence": True,
            "claims": [
                {"text": "Grounded.", "evidence_ids": ["EV_aaa"]},
                {"text": "Invented.", "evidence_ids": ["EV_never_retrieved"]},
            ],
            "conflicts": [],
            "insufficiency_note": None,
        }
    )
    adapter, _ = _adapter(_response(_part(payload)))
    composed = await GroundedAnswerComposer(adapter).compose("q", PASSAGES)
    assert [claim.text for claim in composed.claims] == ["Grounded."]
    assert composed.verification.withheld_claims == 1


def test_parameters_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        GeminiAdapterParameters(
            model_id="gemini-flash-latest",
            gcp_project_id="med-rag",
            gcp_region="global",
            effort="high",
        )


async def test_sampling_is_pinned_for_reproducibility() -> None:
    """Two runs disagreeing on borderline questions is a measurement defect, not noise."""

    parameters = GeminiAdapterParameters(
        model_id="gemini-2.5-flash",
        gcp_project_id="med-rag",
        gcp_region="global",
        seed=20260827,
    )
    client = StubClient(_response(_part(ANSWER_JSON)))
    await GeminiGenerationAdapter(parameters, client=client).generate(
        system_prompt="S", user_content="U"
    )
    config = client.calls[0]["config"]
    assert config.temperature == 0.0
    assert config.seed == 20260827
