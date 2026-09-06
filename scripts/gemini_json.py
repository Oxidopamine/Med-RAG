"""Structured JSON calls to Gemini on Vertex for the research lane, on the serving lane's terms.

The closed-book arm, the claim judge and the abstention oracle of
[docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md) all need a
Gemini call that returns JSON against a caller-supplied schema. None of them can go through
`GeminiGenerationAdapter`, which is hard-wired to the `ModelAnswer` contract whose claims
require at least one `evidence_id`. This helper reproduces what that adapter does to a
request, so that the research lane and the serving lane send the same shape and any drift
between them is detectable:

* the request config of `GeminiGenerationAdapter._request_config`: system instruction, JSON
  response type, the response schema, `max_output_tokens`, `temperature`, `seed`, thinking
  budget by absence, and the request timeout;
* the adapter's `_without_decoding_bounds` applied to the caller's schema, because Gemini's
  constrained decoder rejects `maxLength`, `minItems`, `pattern` and their kin, and the
  adapter strips them in `gemini_answer_schema`, not in `_request_config`;
* the adapter's `_reject_declined`, so a blocked prompt, a declined finish and an output-
  budget overrun are refused on the same terms; and
* a retry of `RESOURCE_EXHAUSTED` with exponential backoff, up to five attempts, with every
  other failure recorded once under its class (plan Sections 1.1 and 3.3).

Every result carries `config_sha256`, a digest of the request configuration that was sent
(model, system prompt, stripped schema, decoding parameters), and `prompt_sha256`, a digest
of the filled user content. Released outputs carry the digests and never the filled prompt,
because a filled attribution prompt carries WHO passage text.

Loaded by path like `scripts/ask.py`; not production code.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.reasoning.gemini_adapters import (  # noqa: E402
    GeminiAdapterParameters,
    GeminiGenerationAdapter,
    _without_decoding_bounds,
)
from app.reasoning.generation_adapters import GenerationUnavailableError  # noqa: E402

RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 2.0

# The same causes the coverage harness names, so the two lanes count failures alike. A
# quota failure, a declined finish and an unreadable candidate are missing measurements;
# an output-budget overrun and a contract violation are model behaviours.
MISSING_MEASUREMENT_CLASSES = frozenset(
    {"RESOURCE_EXHAUSTED", "DECLINED", "NO_CANDIDATE", "OTHER"}
)
MODEL_BEHAVIOUR_CLASSES = frozenset({"MAX_TOKENS", "CONTRACT_VALIDATION"})


def classify_error(message: str | None) -> str:
    text = message or ""
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return "RESOURCE_EXHAUSTED"
    if "declined the request" in text or "blocked the prompt" in text:
        return "DECLINED"
    if "exceeded the output budget" in text:
        return "MAX_TOKENS"
    if "did not satisfy" in text or "JSONDecodeError" in text or "Expecting" in text:
        return "CONTRACT_VALIDATION"
    if "returned no candidate" in text or "returned no text part" in text:
        return "NO_CANDIDATE"
    return "OTHER"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def closed_schema(model: Any) -> dict[str, Any]:
    """A pydantic model's JSON schema with `additionalProperties` closed at every level.

    Mirrors `generation_adapters.answer_json_schema` so a research schema is built the
    way the serving schema is.
    """

    schema = model.model_json_schema()
    definitions = schema.get("$defs", {})
    for block in (schema, *definitions.values()):
        if block.get("type") == "object":
            block["additionalProperties"] = False
    return schema


def parameters_from_environment() -> GeminiAdapterParameters:
    """The Gemini binding as `scripts/ask.py` reads it, so there is one definition."""

    path = REPO_ROOT / "scripts" / "ask.py"
    spec = importlib.util.spec_from_file_location("medrag_ask_entrypoint_for_gemini_json", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.gemini_parameters()


def binding_of(parameters: GeminiAdapterParameters) -> dict[str, Any]:
    """The six-field generation binding the coverage harness records."""

    return {
        "model_id": parameters.model_id,
        "max_output_tokens": parameters.max_output_tokens,
        "thinking_budget": parameters.thinking_budget,
        "gcp_region": parameters.gcp_region,
        "temperature": parameters.temperature,
        "seed": parameters.seed,
    }


class GeminiJsonClient:
    """One pinned Gemini model on Vertex, returning JSON against a caller-supplied schema."""

    def __init__(self, parameters: GeminiAdapterParameters, *, client: Any = None) -> None:
        self._parameters = parameters
        self._client = client or GeminiGenerationAdapter._build_client(parameters)

    @property
    def parameters(self) -> GeminiAdapterParameters:
        return self._parameters

    def stripped_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return _without_decoding_bounds(schema)

    def config_sha256(self, system_prompt: str, schema: dict[str, Any]) -> str:
        """Digest of everything that determines the request apart from the user content."""

        parameters = self._parameters
        payload = {
            "model_id": parameters.model_id,
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
            "response_json_schema": self.stripped_schema(schema),
            "max_output_tokens": parameters.max_output_tokens,
            "thinking_budget": parameters.thinking_budget,
            "temperature": parameters.temperature,
            "seed": parameters.seed,
            "request_timeout_seconds": parameters.request_timeout_seconds,
        }
        return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))

    def request_config(self, system_prompt: str, schema: dict[str, Any]) -> Any:
        """`GeminiGenerationAdapter._request_config`, with the caller's stripped schema."""

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
            response_json_schema=self.stripped_schema(schema),
            max_output_tokens=parameters.max_output_tokens,
            thinking_config=thinking,
            temperature=parameters.temperature,
            seed=parameters.seed,
            http_options=types.HttpOptions(
                timeout=int(parameters.request_timeout_seconds * 1000)
            ),
        )

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        schema: dict[str, Any],
        sleep: Any = asyncio.sleep,
    ) -> dict[str, Any]:
        """Return `{"json": ..., "error": None, ...}` or `{"json": None, "error": ..., ...}`.

        The dictionary always carries `attempts`, `config_sha256` and `prompt_sha256`; an
        error also carries `error_class`.
        """

        digests = {
            "config_sha256": self.config_sha256(system_prompt, schema),
            "prompt_sha256": sha256_text(user_content),
        }
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._parameters.model_id,
                    contents=user_content,
                    config=self.request_config(system_prompt, schema),
                )
            except Exception as error:  # noqa: BLE001 - every cause is classified below
                message = str(error)
                error_class = classify_error(message)
                if error_class == "RESOURCE_EXHAUSTED" and attempts < RETRY_ATTEMPTS:
                    await sleep(RETRY_BASE_SECONDS * 2 ** (attempts - 1))
                    continue
                return {
                    "json": None,
                    "error": message,
                    "error_class": error_class,
                    "attempts": attempts,
                    **digests,
                }
            try:
                GeminiGenerationAdapter._reject_declined(response)
                text = GeminiGenerationAdapter._answer_text(response)
            except GenerationUnavailableError as error:
                message = str(error)
                return {
                    "json": None,
                    "error": message,
                    "error_class": classify_error(message),
                    "attempts": attempts,
                    **digests,
                }
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as error:
                return {
                    "json": None,
                    "error": f"response did not satisfy the schema: {error}",
                    "error_class": "CONTRACT_VALIDATION",
                    "attempts": attempts,
                    **digests,
                }
            return {"json": parsed, "error": None, "attempts": attempts, **digests}
