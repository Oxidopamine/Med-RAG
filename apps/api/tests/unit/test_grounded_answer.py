"""The grounding checks are the safety argument, so they are tested directly."""

from __future__ import annotations

import json

import pytest

from app.reasoning.answer_service import (
    GroundedAnswerComposer,
    RetrievedPassage,
    render_evidence_block,
)
from app.reasoning.generation_adapters import (
    GENERATION_ADAPTER_ID,
    GENERATION_ADAPTER_REVISION,
    AnthropicGenerationAdapter,
    GenerationUnavailableError,
    default_generation_registry,
)
from app.reasoning.generation_schemas import (
    AbstentionReason,
    ConflictType,
    GenerationAdapterParameters,
    GenerationProvider,
    ModelAnswer,
)

PASSAGES = (
    RetrievedPassage(
        evidence_id="EV_aaa",
        text="Do not start dolutegravir with rifampicin without dose adjustment.",
        evidence_roles=("EXCEPTION_OR_CONTRAINDICATION",),
        source_title="WHO SMART HIV DAK",
    ),
    RetrievedPassage(evidence_id="EV_bbb", text="Monitor viral load at six months."),
)


class StubBackend:
    def __init__(self, answer: ModelAnswer | Exception) -> None:
        self._answer = answer
        self.calls = 0

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        self.calls += 1
        self.last_user_content = user_content
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def answer(**overrides) -> ModelAnswer:
    payload = {
        "sufficient_evidence": True,
        "claims": [{"text": "Adjust the dose.", "evidence_ids": ["EV_aaa"]}],
        "conflicts": [],
        "insufficiency_note": None,
    }
    payload.update(overrides)
    return ModelAnswer.model_validate(payload)


async def test_empty_retrieval_abstains_without_calling_the_model() -> None:
    backend = StubBackend(answer())
    composed = await GroundedAnswerComposer(backend).compose("Any question?", ())

    assert backend.calls == 0
    assert composed.answered is False
    assert composed.abstention.reason_code == AbstentionReason.NO_EVIDENCE_RETRIEVED.value


async def test_inactive_release_abstains_without_calling_the_model() -> None:
    backend = StubBackend(answer())
    composed = await GroundedAnswerComposer(backend).compose(
        "Any question?", PASSAGES, release_active=False
    )

    assert backend.calls == 0
    assert composed.abstention.reason_code == AbstentionReason.NO_ACTIVE_RELEASE.value


async def test_claim_citing_unretrieved_evidence_is_withheld_whole() -> None:
    backend = StubBackend(
        answer(
            claims=[
                {"text": "Grounded.", "evidence_ids": ["EV_aaa"]},
                {"text": "Invented.", "evidence_ids": ["EV_zzz"]},
                {"text": "Half invented.", "evidence_ids": ["EV_bbb", "EV_zzz"]},
            ]
        )
    )
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert [item.text for item in composed.claims] == ["Grounded."]
    assert composed.verification.rendered_claims == 3
    assert composed.verification.supported_claims == 1
    assert composed.verification.withheld_claims == 2


async def test_all_claims_ungrounded_abstains_despite_model_confidence() -> None:
    backend = StubBackend(
        answer(claims=[{"text": "Invented.", "evidence_ids": ["EV_zzz"]}])
    )
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert composed.answered is False
    assert (
        composed.abstention.reason_code
        == AbstentionReason.NO_CLAIM_SURVIVED_GROUNDING.value
    )
    assert composed.abstention.closest_evidence_ids == ["EV_aaa", "EV_bbb"]


async def test_model_declared_insufficiency_abstains_and_keeps_its_note() -> None:
    backend = StubBackend(
        answer(
            sufficient_evidence=False,
            claims=[{"text": "Ignored.", "evidence_ids": ["EV_aaa"]}],
            insufficiency_note="No paediatric dosing in the retrieved passages.",
        )
    )
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert composed.answered is False
    assert composed.abstention.message == "No paediatric dosing in the retrieved passages."


async def test_generation_failure_abstains_rather_than_propagating() -> None:
    backend = StubBackend(GenerationUnavailableError("bedrock timeout"))
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert composed.answered is False
    assert composed.abstention.reason_code == AbstentionReason.GENERATION_UNAVAILABLE.value
    assert "bedrock timeout" in composed.abstention.message


async def test_conflicts_are_typed_and_filtered_to_retrieved_evidence() -> None:
    backend = StubBackend(
        answer(
            conflicts=[
                {
                    "conflict_type": ConflictType.OUTDATED_INFORMATION.value,
                    "summary": "Two thresholds differ by revision.",
                    "evidence_ids": ["EV_aaa", "EV_zzz"],
                },
                {
                    "conflict_type": ConflictType.CONTRADICTORY_SOURCE.value,
                    "summary": "Entirely unretrieved.",
                    "evidence_ids": ["EV_yyy", "EV_zzz"],
                },
            ]
        )
    )
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert len(composed.conflicts) == 1
    assert composed.conflicts[0]["conflict_type"] == "OUTDATED_INFORMATION"
    assert composed.conflicts[0]["evidence_ids"] == "EV_aaa"


async def test_prompt_carries_only_retrieved_passages() -> None:
    backend = StubBackend(answer())
    await GroundedAnswerComposer(backend).compose("Can I co-prescribe?", PASSAGES)

    assert "EV_aaa" in backend.last_user_content
    assert "EV_bbb" in backend.last_user_content
    assert "Can I co-prescribe?" in backend.last_user_content


def test_evidence_block_labels_every_passage_with_its_id() -> None:
    block = render_evidence_block(PASSAGES)

    assert "[1] evidence_id: EV_aaa" in block
    assert "roles: EXCEPTION_OR_CONTRAINDICATION" in block
    assert "[2] evidence_id: EV_bbb" in block


def bedrock_parameters(**overrides) -> GenerationAdapterParameters:
    payload = {
        "provider": GenerationProvider.AWS_BEDROCK,
        "model_id": "claude-opus-5",
        "max_tokens": 16_000,
        "aws_region": "us-east-1",
    }
    payload.update(overrides)
    return GenerationAdapterParameters.model_validate(payload)


def test_provider_binding_is_validated_both_ways() -> None:
    assert bedrock_parameters().qualified_model_id() == "anthropic.claude-opus-5"

    vertex = GenerationAdapterParameters(
        provider=GenerationProvider.GCP_VERTEX,
        model_id="claude-opus-5",
        max_tokens=16_000,
        gcp_project_id="med-rag",
        gcp_region="global",
    )
    assert vertex.qualified_model_id() == "claude-opus-5"

    with pytest.raises(ValueError, match="requires an AWS region"):
        GenerationAdapterParameters(
            provider=GenerationProvider.AWS_BEDROCK, model_id="m", max_tokens=10
        )
    with pytest.raises(ValueError, match="cannot pin an AWS region"):
        GenerationAdapterParameters(
            provider=GenerationProvider.GCP_VERTEX,
            model_id="m",
            max_tokens=10,
            gcp_project_id="p",
            gcp_region="global",
            aws_region="us-east-1",
        )


def test_registry_is_fail_closed_for_unknown_adapters() -> None:
    registry = default_generation_registry()

    with pytest.raises(GenerationUnavailableError, match="not allowlisted"):
        registry.build("med-rag/other", "1.0.0", bedrock_parameters())


class FakeMessages:
    def __init__(self, payload, stop_reason: str = "end_turn") -> None:
        self._payload = payload
        self._stop_reason = stop_reason
        self.request: dict = {}

    async def create(self, **kwargs):
        self.request = kwargs

        class Block:
            type = "text"
            text = json.dumps(self._payload)

        class Response:
            content = [Block()]
            stop_reason = self._stop_reason

        return Response()


class FakeClient:
    def __init__(self, payload, stop_reason: str = "end_turn") -> None:
        self.messages = FakeMessages(payload, stop_reason)


async def test_adapter_request_shape_matches_platform_constraints() -> None:
    client = FakeClient(
        {"sufficient_evidence": True, "claims": [{"text": "t", "evidence_ids": ["EV_aaa"]}]}
    )
    adapter = AnthropicGenerationAdapter(bedrock_parameters(), client=client)

    result = await adapter.generate(system_prompt="rules", user_content="question")

    assert result.sufficient_evidence is True
    request = client.messages.request
    assert request["model"] == "anthropic.claude-opus-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["effort"] == "high"
    assert request["output_config"]["format"]["type"] == "json_schema"
    # Neither Bedrock nor Vertex supports automatic caching, so the breakpoint is explicit.
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    # A fixed thinking budget and server-side fallbacks are unsupported here.
    assert "budget_tokens" not in json.dumps(request)
    assert "fallbacks" not in request


async def test_adapter_treats_a_refusal_as_unavailable() -> None:
    client = FakeClient({"sufficient_evidence": True}, stop_reason="refusal")
    adapter = AnthropicGenerationAdapter(bedrock_parameters(), client=client)

    with pytest.raises(GenerationUnavailableError, match="declined"):
        await adapter.generate(system_prompt="rules", user_content="question")


async def test_adapter_rejects_a_response_outside_the_answer_contract() -> None:
    client = FakeClient({"claims": [{"text": "t", "evidence_ids": ["EV_aaa"]}]})
    adapter = AnthropicGenerationAdapter(bedrock_parameters(), client=client)

    with pytest.raises(GenerationUnavailableError, match="answer contract"):
        await adapter.generate(system_prompt="rules", user_content="question")


def test_answer_schema_closes_additional_properties() -> None:
    adapter = AnthropicGenerationAdapter(bedrock_parameters(), client=FakeClient({}))

    schema = adapter._schema
    assert schema["additionalProperties"] is False
    assert all(
        block["additionalProperties"] is False
        for block in schema.get("$defs", {}).values()
        if block.get("type") == "object"
    )
    assert GENERATION_ADAPTER_ID and GENERATION_ADAPTER_REVISION


async def test_uncited_passages_are_reported_as_ranked_candidates() -> None:
    # PASSAGES arrives in retrieval order; only EV_aaa (rank 1) is cited.
    backend = StubBackend(answer())
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert composed.answered is True
    assert [(item.evidence_id, item.retrieval_rank) for item in composed.candidates] == [
        ("EV_bbb", 2)
    ]


async def test_a_passage_cited_by_a_surviving_claim_is_not_a_candidate() -> None:
    backend = StubBackend(
        answer(
            claims=[
                {"text": "Adjust the dose.", "evidence_ids": ["EV_aaa"]},
                {"text": "Recheck at six months.", "evidence_ids": ["EV_bbb"]},
            ]
        )
    )
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert composed.candidates == ()


async def test_an_abstention_reports_no_ranked_candidates() -> None:
    backend = StubBackend(answer(sufficient_evidence=False, insufficiency_note="Not covered."))
    composed = await GroundedAnswerComposer(backend).compose("Q?", PASSAGES)

    assert composed.answered is False
    assert composed.candidates == ()
