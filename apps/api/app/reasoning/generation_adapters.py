"""Provider-swappable Claude generation adapters for Bedrock and Vertex.

Both providers are reached through the Anthropic SDK's platform clients, which expose
the same Messages surface once constructed. Everything that differs between them -
client construction, model-ID prefixing, credential source - is confined to
``_build_client``; the request itself is identical, so moving between AWS and GCP, or
to a different Claude model on either, is a sealed-parameter change.

Two platform limits shape the request and are not incidental:

* Automatic (top-level) prompt caching is unavailable on both, so the cache breakpoint
  is placed explicitly on the frozen system block.
* Server-side ``fallbacks`` is unavailable on both. A refusal is surfaced as an
  abstention rather than silently retried against another model.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.reasoning.generation_schemas import (
    GenerationAdapterParameters,
    GenerationProvider,
    ModelAnswer,
)

GENERATION_ADAPTER_ID = "med-rag/anthropic-grounded-answer"
GENERATION_ADAPTER_REVISION = "1.0.0"


class GenerationUnavailableError(RuntimeError):
    """Raised when the generation lane cannot produce a validated answer."""


class GenerationBackend(Protocol):
    async def generate(
        self, *, system_prompt: str, user_content: str
    ) -> ModelAnswer: ...


def _answer_json_schema() -> dict[str, Any]:
    """Return the response schema, with additionalProperties closed at every level."""

    schema = ModelAnswer.model_json_schema()
    definitions = schema.get("$defs", {})
    for block in (schema, *definitions.values()):
        if block.get("type") == "object":
            block["additionalProperties"] = False
    return schema


class AnthropicGenerationAdapter:
    """One pinned Claude model served from Bedrock or Vertex."""

    def __init__(self, parameters: GenerationAdapterParameters, *, client: Any = None) -> None:
        self._parameters = parameters
        self._client = client or self._build_client(parameters)
        self._schema = _answer_json_schema()

    @staticmethod
    def _build_client(parameters: GenerationAdapterParameters) -> Any:
        if parameters.provider is GenerationProvider.AWS_BEDROCK:
            from anthropic import AsyncAnthropicBedrockMantle

            return AsyncAnthropicBedrockMantle(aws_region=parameters.aws_region)
        from anthropic import AsyncAnthropicVertex

        return AsyncAnthropicVertex(
            project_id=parameters.gcp_project_id,
            region=parameters.gcp_region,
        )

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        parameters = self._parameters
        system_block: dict[str, Any] = {"type": "text", "text": system_prompt}
        if parameters.cache_system_prompt:
            # Explicit breakpoint: the instruction block is byte-stable across every
            # question, the evidence that follows it is not.
            system_block["cache_control"] = {"type": "ephemeral"}
        try:
            response = await self._client.messages.create(
                model=parameters.qualified_model_id(),
                max_tokens=parameters.max_tokens,
                system=[system_block],
                messages=[{"role": "user", "content": user_content}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": parameters.effort.value,
                    "format": {"type": "json_schema", "schema": self._schema},
                },
                timeout=parameters.request_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001 - surfaced as a fail-closed abstention
            raise GenerationUnavailableError(str(error)) from error

        if getattr(response, "stop_reason", None) == "refusal":
            raise GenerationUnavailableError("generation model declined the request")
        text = next(
            (block.text for block in response.content if getattr(block, "type", None) == "text"),
            None,
        )
        if not text:
            raise GenerationUnavailableError("generation returned no text block")
        try:
            return ModelAnswer.model_validate(json.loads(text))
        except (ValueError, json.JSONDecodeError) as error:
            raise GenerationUnavailableError(
                f"generation response did not satisfy the answer contract: {error}"
            ) from error


class GenerationAdapterRegistry:
    """Fail-closed registry keyed by adapter ID and revision."""

    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], Any] = {}

    def register(self, adapter_id: str, adapter_revision: str, factory: Any) -> None:
        if not adapter_id or not adapter_revision:
            raise ValueError("generation adapter registry keys cannot be empty")
        key = (adapter_id, adapter_revision)
        if key in self._factories:
            raise ValueError(
                f"generation adapter is already registered: {adapter_id}@{adapter_revision}"
            )
        self._factories[key] = factory

    def build(
        self,
        adapter_id: str,
        adapter_revision: str,
        parameters: GenerationAdapterParameters,
        **kwargs: Any,
    ) -> GenerationBackend:
        try:
            factory = self._factories[(adapter_id, adapter_revision)]
        except KeyError as error:
            raise GenerationUnavailableError(
                f"generation adapter is not allowlisted: {adapter_id}@{adapter_revision}"
            ) from error
        return factory(parameters, **kwargs)


def default_generation_registry() -> GenerationAdapterRegistry:
    registry = GenerationAdapterRegistry()
    registry.register(
        GENERATION_ADAPTER_ID, GENERATION_ADAPTER_REVISION, AnthropicGenerationAdapter
    )
    return registry
