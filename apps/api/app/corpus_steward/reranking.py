"""Artifact-bound Qwen3 reranking with the official yes/no scoring contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.corpus_steward.embedding_adapters import AdapterConfigurationError
from app.corpus_steward.index_schemas import EmbeddingModelReference
from app.corpus_steward.model_artifacts import ModelArtifactKind, VerifiedModelArtifact

QWEN3_RERANKER_ADAPTER_ID = "med-rag/qwen3-reranker-transformers"
QWEN3_RERANKER_ADAPTER_REVISION = "1.1.0"
RERANKER_SCORE_CACHE_VERSION = "qwen3-reranker-score-cache-v1"
QWEN3_RERANKER_MODEL_IDS = frozenset(
    {
        "Qwen/Qwen3-Reranker-0.6B",
        "Qwen/Qwen3-Reranker-4B",
        "Qwen/Qwen3-Reranker-8B",
    }
)

_OFFICIAL_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements based on '
    'the Query and the Instruct provided. Note that the answer can only be "yes" or '
    '"no".<|im_end|>\n<|im_start|>user\n'
)
_OFFICIAL_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


class Qwen3RerankerParameters(BaseModel):
    """Every behavior-affecting reranker parameter sealed into its artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction: str = Field(min_length=1, max_length=2_000)
    max_length: int = Field(gt=0, le=32_768)
    batch_size: int = Field(gt=0, le=1_024)
    padding_side: Literal["left"] = "left"
    truncation: Literal["longest_first"] = "longest_first"
    score_mode: Literal["yes_probability"] = "yes_probability"
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16", "int8_dynamic"]

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        if value == "cpu" or value == "mps" or re.fullmatch(r"cuda(?::\d+)?", value):
            return value
        raise ValueError("device must be cpu, mps, cuda, or an explicit cuda device")


class RerankerRuntime(Protocol):
    def score(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
    ) -> Sequence[float]: ...


class RerankerBackend(Protocol):
    @property
    def model_reference(self) -> EmbeddingModelReference: ...

    @property
    def adapter_identity(self) -> tuple[str, str]: ...

    @property
    def instruction_sha256(self) -> str: ...

    async def score(self, query: str, documents: Sequence[str]) -> Sequence[float]: ...


class _Qwen3TransformersRerankerRuntime:
    """Offline implementation of Qwen's official causal-LM reranking example."""

    def __init__(self, root: Path, *, device: str, dtype: str) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as error:
            raise AdapterConfigurationError(
                "the Qwen3 reranker requires the 'retrieval' optional dependencies"
            ) from error
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise AdapterConfigurationError("the pinned CUDA device is unavailable")
        if device == "mps" and not torch.backends.mps.is_available():
            raise AdapterConfigurationError("the pinned MPS device is unavailable")
        if device == "cpu" and dtype == "float16":
            raise AdapterConfigurationError("float16 inference is not supported on CPU")
        if dtype == "int8_dynamic" and device != "cpu":
            raise AdapterConfigurationError("dynamic int8 inference is supported only on CPU")
        torch_dtype = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "int8_dynamic": torch.float32,
        }[dtype]
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                padding_side="left",
                use_fast=True,
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                dtype=torch_dtype,
            )
            if dtype == "int8_dynamic":
                self._model = torch.ao.quantization.quantize_dynamic(
                    self._model,
                    {torch.nn.Linear},
                    dtype=torch.qint8,
                    inplace=False,
                )
            self._model.to(device)
            self._model.eval()
            self._false_token_id = self._tokenizer.convert_tokens_to_ids("no")
            self._true_token_id = self._tokenizer.convert_tokens_to_ids("yes")
            unknown = self._tokenizer.unk_token_id
            if (
                self._false_token_id == self._true_token_id
                or self._false_token_id == unknown
                or self._true_token_id == unknown
            ):
                raise AdapterConfigurationError(
                    "Qwen3 reranker tokenizer does not expose distinct yes/no tokens"
                )
            self._prefix_tokens = self._tokenizer.encode(
                _OFFICIAL_PREFIX, add_special_tokens=False
            )
            self._suffix_tokens = self._tokenizer.encode(
                _OFFICIAL_SUFFIX, add_special_tokens=False
            )
        except AdapterConfigurationError:
            raise
        except Exception as error:
            raise AdapterConfigurationError(
                "local Qwen3 reranker artifact could not be loaded: "
                f"{error.__class__.__name__}"
            ) from error
        self._device = device
        self._torch = torch
        self._lock = threading.Lock()

    def score(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
    ) -> Sequence[float]:
        formatted = [
            f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
            for query, document in pairs
        ]
        content_budget = max_length - len(self._prefix_tokens) - len(self._suffix_tokens)
        if content_budget <= 0:
            raise ValueError("reranker maximum length cannot fit the sealed prompt")
        torch = self._torch
        with self._lock, torch.inference_mode():
            encoded = self._tokenizer(
                formatted,
                padding=False,
                truncation="longest_first",
                max_length=content_budget,
                return_attention_mask=False,
            )
            input_ids = [
                self._prefix_tokens + item + self._suffix_tokens
                for item in encoded["input_ids"]
            ]
            inputs = self._tokenizer.pad(
                {"input_ids": input_ids},
                padding=True,
                return_tensors="pt",
            )
            inputs = {name: value.to(self._device) for name, value in inputs.items()}
            logits = self._model(**inputs).logits[:, -1, :]
            yes = logits[:, self._true_token_id]
            no = logits[:, self._false_token_id]
            probabilities = torch.softmax(torch.stack((no, yes), dim=1), dim=1)[:, 1]
            return probabilities.detach().to(device="cpu", dtype=torch.float32).tolist()


class Qwen3RerankerAdapter:
    """Verified local Qwen3 reranker with bounded batching and stable score semantics."""

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: RerankerRuntime | None = None,
        score_cache: Path | None = None,
    ) -> None:
        content = artifact.manifest.content
        if content.artifact_kind is not ModelArtifactKind.RERANKER:
            raise AdapterConfigurationError("Qwen3 reranker requires a reranker artifact")
        if content.model_id not in QWEN3_RERANKER_MODEL_IDS:
            raise AdapterConfigurationError(
                "Qwen3 reranker requires an allowlisted Qwen3 reranker model"
            )
        if re.fullmatch(r"[a-f0-9]{40,64}", content.revision) is None:
            raise AdapterConfigurationError(
                "Qwen3 reranker requires an immutable lowercase commit revision"
            )
        if content.dimension != 1:
            raise AdapterConfigurationError("reranker artifacts must declare dimension 1")
        if (
            content.adapter_id != QWEN3_RERANKER_ADAPTER_ID
            or content.adapter_revision != QWEN3_RERANKER_ADAPTER_REVISION
        ):
            raise AdapterConfigurationError(
                "artifact requires the allowlisted Qwen3 reranker adapter revision"
            )
        try:
            parameters = Qwen3RerankerParameters.model_validate(
                content.adapter_parameters
            )
        except ValidationError as error:
            raise AdapterConfigurationError(
                f"invalid parameters for {content.adapter_id}: {error}"
            ) from error
        if device is not None and device != parameters.device:
            raise AdapterConfigurationError(
                "requested device does not match the device sealed in the reranker manifest"
            )
        self._artifact = artifact
        self._parameters = parameters
        self._runtime = runtime or _Qwen3TransformersRerankerRuntime(
            artifact.root,
            device=parameters.device,
            dtype=parameters.dtype,
        )
        self._score_cache_path = score_cache
        self._score_cache_identity = {
            "adapter_id": content.adapter_id,
            "adapter_revision": content.adapter_revision,
            "artifact_sha256": artifact.manifest.artifact_sha256,
            "instruction_sha256": hashlib.sha256(
                parameters.instruction.encode("utf-8")
            ).hexdigest(),
            "max_length": parameters.max_length,
            "version": RERANKER_SCORE_CACHE_VERSION,
        }
        self._score_cache = self._load_score_cache()

    @property
    def model_reference(self) -> EmbeddingModelReference:
        return self._artifact.reference

    @property
    def adapter_identity(self) -> tuple[str, str]:
        content = self._artifact.manifest.content
        return content.adapter_id, content.adapter_revision

    @property
    def instruction_sha256(self) -> str:
        return hashlib.sha256(self._parameters.instruction.encode("utf-8")).hexdigest()

    async def score(self, query: str, documents: Sequence[str]) -> Sequence[float]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("reranker query must be non-empty text")
        inputs = tuple(documents)
        if any(not isinstance(document, str) or not document.strip() for document in inputs):
            raise ValueError("reranker documents must be non-empty text")
        scores: list[float | None] = [None] * len(inputs)
        misses: dict[str, tuple[str, list[int]]] = {}
        for index, document in enumerate(inputs):
            key = self._score_key(query, document)
            cached = self._score_cache.get(key)
            if cached is not None:
                scores[index] = cached
            else:
                existing = misses.get(key)
                if existing is None:
                    misses[key] = (document, [index])
                else:
                    existing[1].append(index)
        pending = tuple(
            sorted(
                misses.items(),
                key=lambda item: (len(item[1][0]), item[0]),
            )
        )
        for start in range(0, len(pending), self._parameters.batch_size):
            batch = pending[start : start + self._parameters.batch_size]
            batch_scores = tuple(
                await asyncio.to_thread(
                    self._runtime.score,
                    tuple((query, item[1][0]) for item in batch),
                    instruction=self._parameters.instruction,
                    max_length=self._parameters.max_length,
                )
            )
            if len(batch_scores) != len(batch):
                raise RuntimeError("Qwen3 reranker returned an incomplete batch")
            for (key, (_document, indices)), score in zip(batch, batch_scores, strict=True):
                converted = float(score)
                if not math.isfinite(converted) or not 0 <= converted <= 1:
                    raise RuntimeError("Qwen3 reranker returned an invalid probability")
                self._score_cache[key] = converted
                for index in indices:
                    scores[index] = converted
        if pending:
            self._write_score_cache()
        if any(score is None for score in scores):
            raise RuntimeError("Qwen3 reranker cache resolution was incomplete")
        return tuple(float(score) for score in scores)

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _sha256_json(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical_json(value).encode("utf-8")).hexdigest()

    @classmethod
    def _score_key(cls, query: str, document: str) -> str:
        return cls._sha256_json((query, document))

    def _load_score_cache(self) -> dict[str, float]:
        path = self._score_cache_path
        if path is None or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("identity") != self._score_cache_identity:
                raise ValueError("identity mismatch")
            if raw.get("identity_sha256") != self._sha256_json(
                self._score_cache_identity
            ):
                raise ValueError("identity digest mismatch")
            scores = raw.get("scores")
            if not isinstance(scores, dict) or raw.get("scores_sha256") != self._sha256_json(
                scores
            ):
                raise ValueError("score digest mismatch")
            converted = {str(key): float(value) for key, value in scores.items()}
            if any(
                re.fullmatch(r"[a-f0-9]{64}", key) is None
                or not math.isfinite(value)
                or not 0 <= value <= 1
                for key, value in converted.items()
            ):
                raise ValueError("invalid score entry")
            return converted
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise AdapterConfigurationError(
                "reranker score cache is inconsistent with the sealed runtime"
            ) from error

    def _write_score_cache(self) -> None:
        path = self._score_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "identity": self._score_cache_identity,
            "identity_sha256": self._sha256_json(self._score_cache_identity),
            "scores": self._score_cache,
            "scores_sha256": self._sha256_json(self._score_cache),
        }
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(self._canonical_json(payload) + "\n", encoding="utf-8")
        temporary.replace(path)
