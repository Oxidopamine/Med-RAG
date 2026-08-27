"""Gemini grounded-answer adapter: a development comparator, not a sealed candidate.

The Claude lane in ``generation_adapters`` is the intended production path and is
blocked on Vertex quota for the ``anthropic-*`` base models. This module exists so the
answer lane can be exercised against real model output in the meantime. It satisfies
the same ``GenerationBackend`` protocol, so ``GroundedAnswerComposer`` and every
grounding rule it enforces are untouched: a claim citing evidence that was not
retrieved is still discarded whole, and a model that declares sufficiency still cannot
raise an outcome to an answer.

Deliberately kept out of ``GenerationProvider``. That enum documents itself as a choice
between two platforms serving the same Anthropic models through one SDK surface, and
half of ``GenerationAdapterParameters`` - the ``anthropic.`` prefix, ``effort``,
``adaptive_thinking``, the explicit cache breakpoint - has no Gemini meaning. Folding a
second vendor into it would leave fields that are silently dead under one provider,
which is how a sealed parameter file stops describing what actually ran.

Three differences from the Claude lane are load-bearing:

* **No cache breakpoint.** Gemini's context caching is a separate addressable resource
  with a minimum size, not a marker on a content block, so the frozen instruction block
  is re-sent on every call. That is a cost difference, not a correctness one.
* **Decoding bounds are stripped from the schema that is sent.** Gemini compiles the
  response schema into a decoding constraint and rejects ``ModelAnswer``'s length limits
  outright - "the specified schema produces a constraint that has too many states for
  serving". They are removed from the *hint* only. The returned JSON is still parsed
  into ``ModelAnswer``, whose validators enforce every one of those bounds, and a
  violation raises and abstains rather than being truncated to fit.
* **A refusal has a different shape.** Anthropic reports ``stop_reason == "refusal"``;
  Gemini blocks either the prompt (``prompt_feedback.block_reason``) or the candidate
  (``finish_reason`` in the safety set). Both map to the same fail-closed abstention,
  and neither is retried against another model.

Model IDs like ``gemini-flash-latest`` are floating aliases that move without notice.
That is acceptable for reading answers; it is not acceptable for any number that gets
recorded. Pin an explicit version before this lane backs a measurement.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.reasoning.generation_adapters import (
    GenerationUnavailableError,
    answer_json_schema,
)
from app.reasoning.generation_schemas import ModelAnswer

GEMINI_ADAPTER_ID = "med-rag/gemini-grounded-answer"
GEMINI_ADAPTER_REVISION = "1.0.0"

# Keywords Gemini's constrained decoder cannot compile. Dropping them costs nothing:
# ModelAnswer re-imposes each one after the response is parsed.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "maxLength",
        "minLength",
        "maxItems",
        "minItems",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)

# ``finish_reason`` values that mean the model declined rather than answered.
_DECLINED_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "PROHIBITED_CONTENT",
        "RECITATION",
        "BLOCKLIST",
        "SPII",
        "LANGUAGE",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)


def _without_decoding_bounds(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _without_decoding_bounds(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(node, list):
        return [_without_decoding_bounds(item) for item in node]
    return node


def gemini_answer_schema() -> dict[str, Any]:
    """Return the shared answer schema with the bounds Gemini cannot decode removed."""

    return _without_decoding_bounds(answer_json_schema())


def _enum_name(value: Any) -> str | None:
    """Return an enum member's name, tolerating the plain strings the API also sends."""

    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


class GeminiAdapterParameters(BaseModel):
    """Invocation settings for the Gemini comparator.

    Deliberately not a ``CanonicalModel`` with a ``schema_version``: this lane is not
    sealed, not digest-bound, and not eligible to back a recorded measurement. Giving it
    the shape of a sealed contract would invite it to be used as one.
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    gcp_project_id: str = Field(min_length=1, max_length=200)
    gcp_region: str = Field(min_length=1, max_length=64)
    max_output_tokens: int = Field(default=8192, gt=0, le=65_536)
    # None leaves the model's own default, which is thinking-on for the flash family.
    thinking_budget: int | None = Field(default=None, ge=0)
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    # Defaulted to the reproducible setting rather than the model's own. Two runs of the
    # 49-question coverage set at the sampling defaults disagreed on 7 questions - all of
    # them borderline abstain/answer calls - which is wide enough to move `p_answered`
    # across a pre-registered threshold. Sampling is opt-in here, not opt-out.
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = Field(default=None)


class GeminiGenerationAdapter:
    """One pinned Gemini model served from Vertex, behind the answer-lane protocol."""

    def __init__(self, parameters: GeminiAdapterParameters, *, client: Any = None) -> None:
        self._parameters = parameters
        self._client = client or self._build_client(parameters)
        self._schema = gemini_answer_schema()

    @staticmethod
    def _build_client(parameters: GeminiAdapterParameters) -> Any:
        from google import genai

        # Vertex rather than the Gemini API: authentication is application-default
        # credentials, so no API key is introduced into a project that holds none.
        return genai.Client(
            vertexai=True,
            project=parameters.gcp_project_id,
            location=parameters.gcp_region,
        )

    def _request_config(self, system_prompt: str) -> Any:
        from google.genai import types

        parameters = self._parameters
        thinking = (
            types.ThinkingConfig(thinking_budget=parameters.thinking_budget)
            if parameters.thinking_budget is not None
            else None
        )
        return types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_json_schema=self._schema,
            max_output_tokens=parameters.max_output_tokens,
            thinking_config=thinking,
            temperature=parameters.temperature,
            seed=parameters.seed,
            # HttpOptions.timeout is milliseconds; every other timeout here is seconds.
            http_options=types.HttpOptions(
                timeout=int(parameters.request_timeout_seconds * 1000)
            ),
        )

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._parameters.model_id,
                contents=user_content,
                config=self._request_config(system_prompt),
            )
        except Exception as error:  # noqa: BLE001 - surfaced as a fail-closed abstention
            raise GenerationUnavailableError(str(error)) from error

        self._reject_declined(response)
        text = self._answer_text(response)
        try:
            return ModelAnswer.model_validate(json.loads(text))
        except (ValueError, json.JSONDecodeError) as error:
            raise GenerationUnavailableError(
                f"generation response did not satisfy the answer contract: {error}"
            ) from error

    @staticmethod
    def _reject_declined(response: Any) -> None:
        block_reason = _enum_name(
            getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
        )
        if block_reason is not None:
            raise GenerationUnavailableError(
                f"generation model blocked the prompt: {block_reason}"
            )

        candidates = getattr(response, "candidates", None) or ()
        if not candidates:
            raise GenerationUnavailableError("generation returned no candidate")

        finish_reason = _enum_name(getattr(candidates[0], "finish_reason", None))
        if finish_reason in _DECLINED_FINISH_REASONS:
            raise GenerationUnavailableError(
                f"generation model declined the request: {finish_reason}"
            )
        if finish_reason == "MAX_TOKENS":
            # Truncated JSON is not a shorter answer, it is an invalid one. Failing here
            # keeps a half-written claim from ever reaching the grounding check.
            raise GenerationUnavailableError(
                "generation exceeded the output budget before completing"
            )

    @staticmethod
    def _answer_text(response: Any) -> str:
        """Join the answer parts, skipping the thought parts flash models emit.

        ``response.text`` does much the same, but returns ``None`` where a candidate
        carries no text part at all. That case is a fail-closed condition here, not an
        empty answer, so it is raised rather than passed on as a falsy string.
        """

        content = getattr(response.candidates[0], "content", None)
        parts = getattr(content, "parts", None) or ()
        text = "".join(
            part.text
            for part in parts
            if getattr(part, "text", None) and not getattr(part, "thought", False)
        )
        if not text.strip():
            raise GenerationUnavailableError("generation returned no text part")
        return text
