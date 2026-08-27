"""Artifact-bound rerankers that each keep their own official scoring contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol

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

BGE_RERANKER_V2_M3_ADAPTER_ID = "med-rag/bge-reranker-v2-m3-transformers"
BGE_RERANKER_V2_M3_ADAPTER_REVISION = "1.0.0"
BGE_RERANKER_V2_M3_SCORE_CACHE_VERSION = "bge-reranker-v2-m3-score-cache-v1"
BGE_RERANKER_V2_M3_MODEL_IDS = frozenset({"BAAI/bge-reranker-v2-m3"})
# BAAI/bge-reranker-v2-m3 is an XLM-RoBERTa sequence-classification cross-encoder with a
# single relevance label. XLM-R reserves two position slots (padding_idx offset), so a
# checkpoint declaring 8194 position embeddings can encode 8192 real tokens; that is the
# whole-pair ceiling, not a per-side budget. Unlike MedCPT this lane is multilingual,
# which is why it is retained as the multilingual reranking control.
BGE_RERANKER_V2_M3_MAX_LENGTH = 8192
BGE_RERANKER_V2_M3_POSITION_OFFSET = 2
# The published FlagEmbedding usage exposes both a raw logit and a sigmoid-normalized
# score. The normalized form is sealed here so the value is a bounded 0-1 relevance
# probability, matching the Qwen3 lane's domain and letting the same fusion and
# threshold code read either backend without rescaling.
BGE_RERANKER_V2_M3_INSTRUCTION = ""

MEDCPT_CROSS_ENCODER_ADAPTER_ID = "med-rag/medcpt-cross-encoder-transformers"
MEDCPT_CROSS_ENCODER_ADAPTER_REVISION = "1.0.0"
MEDCPT_CROSS_ENCODER_SCORE_CACHE_VERSION = "medcpt-cross-encoder-score-cache-v1"
MEDCPT_CROSS_ENCODER_MODEL_IDS = frozenset({"ncbi/MedCPT-Cross-Encoder"})
# The official ncbi/MedCPT-Cross-Encoder example tokenizes each ``[query, article]``
# pair with ``truncation=True, padding=True, max_length=512`` and reads the raw
# single-label logit, where a higher logit means higher relevance. The checkpoint is
# a BERT with 512 learned position embeddings, so 512 is the whole-pair ceiling, not
# a per-side budget, and it is deliberately not the Qwen3 reranker's 8192 default.
MEDCPT_CROSS_ENCODER_MAX_LENGTH = 512
# MedCPT is trained on short PubMed query/article pairs and its published contract has
# no instruction segment at all, so the pair-level instruction is the empty string and
# its digest is what binds "this backend applies no instruction" into a candidate pin.
MEDCPT_CROSS_ENCODER_INSTRUCTION = ""

_OFFICIAL_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements based on '
    'the Query and the Instruct provided. Note that the answer can only be "yes" or '
    '"no".<|im_end|>\n<|im_start|>user\n'
)
_OFFICIAL_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

_TORCH_DTYPE_NAMES = {
    "float32": "float32",
    "float16": "float16",
    "bfloat16": "bfloat16",
    "int8_dynamic": "float32",
}


def _validated_reranker_device(value: str) -> str:
    if value in ("cpu", "mps") or re.fullmatch(r"cuda(?::\d+)?", value):
        return value
    raise ValueError("device must be cpu, mps, cuda, or an explicit cuda device")


def _checked_torch_dtype(torch: Any, *, device: str, dtype: str) -> Any:
    """Reject device/precision pairs the local runtime cannot honour exactly."""

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise AdapterConfigurationError("the pinned CUDA device is unavailable")
    if device == "mps" and not torch.backends.mps.is_available():
        raise AdapterConfigurationError("the pinned MPS device is unavailable")
    if device == "cpu" and dtype == "float16":
        raise AdapterConfigurationError("float16 inference is not supported on CPU")
    if dtype == "int8_dynamic" and device != "cpu":
        raise AdapterConfigurationError("dynamic int8 inference is supported only on CPU")
    return getattr(torch, _TORCH_DTYPE_NAMES[dtype])


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
        return _validated_reranker_device(value)


class MedCPTCrossEncoderParameters(BaseModel):
    """The official MedCPT cross-encoder contract, sealed into its artifact.

    The defaults are the model card's, not this project's: a 512-token whole-pair
    budget, right-side BERT padding, ``longest_first`` truncation across the pair, and
    a raw single-label relevance logit. ``max_length`` is capped at the checkpoint's
    512 position embeddings so an artifact cannot seal an unrunnable longer budget,
    and there is no instruction field because MedCPT's contract has no instruction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_length: int = Field(
        default=MEDCPT_CROSS_ENCODER_MAX_LENGTH,
        gt=0,
        le=MEDCPT_CROSS_ENCODER_MAX_LENGTH,
    )
    batch_size: int = Field(gt=0, le=1_024)
    padding_side: Literal["right"] = "right"
    truncation: Literal["longest_first"] = "longest_first"
    score_mode: Literal["relevance_logit"] = "relevance_logit"
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16", "int8_dynamic"]

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        return _validated_reranker_device(value)


class BGERerankerV2M3Parameters(BaseModel):
    """The official BAAI/bge-reranker-v2-m3 contract, sealed into its artifact.

    Unlike MedCPT's fixed 512, this checkpoint's pair budget is genuinely tunable, so
    ``max_length`` is required rather than defaulted: a candidate must state the budget
    it was measured at. ``score_mode`` is the sigmoid-normalized form of the published
    usage, which puts this lane on the same bounded 0-1 domain as the Qwen3 reranker so
    fusion and threshold code can read either without rescaling.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_length: int = Field(gt=0, le=BGE_RERANKER_V2_M3_MAX_LENGTH)
    batch_size: int = Field(gt=0, le=1_024)
    padding_side: Literal["right"] = "right"
    truncation: Literal["longest_first"] = "longest_first"
    score_mode: Literal["sigmoid_probability"] = "sigmoid_probability"
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16", "int8_dynamic"]

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        return _validated_reranker_device(value)


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
        torch_dtype = _checked_torch_dtype(torch, device=device, dtype=dtype)
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


class _MedCPTCrossEncoderRuntime:
    """Offline implementation of NCBI's official MedCPT cross-encoder example.

    The official snippet tokenizes ``[query, article]`` pairs with ``truncation=True,
    padding=True, max_length=512`` and reads ``model(**encoded).logits.squeeze(dim=1)``.
    Both halves are reproduced exactly: ``truncation=True`` is spelled out as
    ``longest_first`` so the strategy is explicit in the sealed parameters, and the raw
    logit is returned without a sigmoid or softmax so ranking stays on MedCPT's own
    scale instead of borrowing the Qwen3 reranker's probability semantics.
    """

    def __init__(self, root: Path, *, device: str, dtype: str) -> None:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:
            raise AdapterConfigurationError(
                "the MedCPT cross-encoder requires the 'retrieval' optional dependencies"
            ) from error
        torch_dtype = _checked_torch_dtype(torch, device=device, dtype=dtype)
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                padding_side="right",
                use_fast=True,
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                dtype=torch_dtype,
            )
            if self._model.config.num_labels != 1:
                raise AdapterConfigurationError(
                    "the MedCPT cross-encoder must expose a single relevance logit"
                )
            positions = getattr(
                self._model.config,
                "max_position_embeddings",
                MEDCPT_CROSS_ENCODER_MAX_LENGTH,
            )
            if positions < MEDCPT_CROSS_ENCODER_MAX_LENGTH:
                raise AdapterConfigurationError(
                    "the MedCPT cross-encoder artifact cannot fit its official 512-token pair"
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
        except AdapterConfigurationError:
            raise
        except Exception as error:
            raise AdapterConfigurationError(
                "local MedCPT cross-encoder artifact could not be loaded: "
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
        if instruction:
            raise ValueError(
                "the official MedCPT cross-encoder contract has no instruction segment"
            )
        if not 0 < max_length <= MEDCPT_CROSS_ENCODER_MAX_LENGTH:
            raise ValueError(
                "the MedCPT cross-encoder pair budget must fit its official 512 tokens"
            )
        formatted = [[query, document] for query, document in pairs]
        torch = self._torch
        with self._lock, torch.inference_mode():
            encoded = self._tokenizer(
                formatted,
                truncation="longest_first",
                padding=True,
                max_length=max_length,
                return_tensors="pt",
            )
            inputs = {name: value.to(self._device) for name, value in encoded.items()}
            logits = self._model(**inputs).logits.squeeze(dim=1)
            converted = logits.detach().to(device="cpu", dtype=torch.float32)
            return converted.reshape(-1).tolist()


class _BGERerankerV2M3Runtime:
    """Offline implementation of BAAI's official bge-reranker-v2-m3 example.

    The published usage tokenizes ``[query, passage]`` pairs and reads
    ``model(**inputs).logits.view(-1).float()``; the normalized variant applies a sigmoid
    to map that logit onto 0-1. The sigmoid form is used here so the score domain is
    bounded and comparable across queries, which the raw logit is not.

    The checkpoint is XLM-RoBERTa, which reserves two position slots for its padding
    index, so a config declaring 8194 position embeddings encodes 8192 real tokens. That
    ceiling covers the whole pair, not each side.
    """

    def __init__(self, root: Path, *, device: str, dtype: str) -> None:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:
            raise AdapterConfigurationError(
                "the BGE reranker requires the 'retrieval' optional dependencies"
            ) from error
        torch_dtype = _checked_torch_dtype(torch, device=device, dtype=dtype)
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                padding_side="right",
                use_fast=True,
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                dtype=torch_dtype,
            )
            if self._model.config.num_labels != 1:
                raise AdapterConfigurationError(
                    "the BGE reranker must expose a single relevance logit"
                )
            declared_positions = getattr(
                self._model.config,
                "max_position_embeddings",
                BGE_RERANKER_V2_M3_MAX_LENGTH + BGE_RERANKER_V2_M3_POSITION_OFFSET,
            )
            self._usable_positions = declared_positions - BGE_RERANKER_V2_M3_POSITION_OFFSET
            if self._usable_positions < BGE_RERANKER_V2_M3_MAX_LENGTH:
                raise AdapterConfigurationError(
                    "the BGE reranker artifact cannot fit its official 8192-token pair"
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
        except AdapterConfigurationError:
            raise
        except Exception as error:
            raise AdapterConfigurationError(
                "local BGE reranker artifact could not be loaded: "
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
        if instruction:
            raise ValueError(
                "the official BGE reranker contract has no instruction segment"
            )
        if not 0 < max_length <= self._usable_positions:
            raise ValueError(
                "the BGE reranker pair budget exceeds the artifact's usable positions"
            )
        formatted = [[query, document] for query, document in pairs]
        torch = self._torch
        with self._lock, torch.inference_mode():
            encoded = self._tokenizer(
                formatted,
                truncation="longest_first",
                padding=True,
                max_length=max_length,
                return_tensors="pt",
            )
            inputs = {name: value.to(self._device) for name, value in encoded.items()}
            logits = self._model(**inputs).logits.view(-1)
            probabilities = torch.sigmoid(logits)
            return probabilities.detach().to(device="cpu", dtype=torch.float32).tolist()


class _CachedRerankerAdapter:
    """Bounded batching plus a digest-checked score cache bound to one artifact.

    Subclasses supply their model's identity checks and score domain; everything here
    is the part that must behave identically no matter which reranker a candidate
    seals, so a candidate's cached scores can never be read back under a different
    adapter, artifact digest, instruction, or truncation budget.
    """

    _label: ClassVar[str]
    _cache_version: ClassVar[str]
    _invalid_score_message: ClassVar[str]
    _score_bounds: ClassVar[tuple[float, float] | None] = None

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        instruction: str,
        max_length: int,
        batch_size: int,
        runtime: RerankerRuntime,
        score_cache: Path | None,
    ) -> None:
        content = artifact.manifest.content
        self._artifact = artifact
        self._instruction = instruction
        self._max_length = max_length
        self._batch_size = batch_size
        self._runtime = runtime
        self._score_cache_path = score_cache
        self._score_cache_identity = {
            "adapter_id": content.adapter_id,
            "adapter_revision": content.adapter_revision,
            "artifact_sha256": artifact.manifest.artifact_sha256,
            "instruction_sha256": self.instruction_sha256,
            "max_length": max_length,
            "version": self._cache_version,
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
        return hashlib.sha256(self._instruction.encode("utf-8")).hexdigest()

    def clear_memory_score_cache(self) -> None:
        """Drop in-memory scores so a repeated run measures the model again.

        Every adapter caches scores in memory even when no cache path is configured,
        which is correct for serving and wrong for timing: without this, run 2 of a
        measurement loop is served entirely from run 1 and reports a throughput no
        deployment would ever see. A timing harness must call this between measured
        runs. Any configured on-disk cache is deliberately left alone — it is keyed to
        this artifact, instruction, and budget, so it is a correctness-preserving
        optimization rather than a measurement artifact.
        """

        self._score_cache.clear()

    @classmethod
    def _score_is_valid(cls, value: float) -> bool:
        if not math.isfinite(value):
            return False
        if cls._score_bounds is None:
            return True
        lower, upper = cls._score_bounds
        return lower <= value <= upper

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
        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            batch_scores = tuple(
                await asyncio.to_thread(
                    self._runtime.score,
                    tuple((query, item[1][0]) for item in batch),
                    instruction=self._instruction,
                    max_length=self._max_length,
                )
            )
            if len(batch_scores) != len(batch):
                raise RuntimeError(f"{self._label} returned an incomplete batch")
            for (key, (_document, indices)), score in zip(batch, batch_scores, strict=True):
                converted = float(score)
                if not self._score_is_valid(converted):
                    raise RuntimeError(self._invalid_score_message)
                self._score_cache[key] = converted
                for index in indices:
                    scores[index] = converted
        if pending:
            self._write_score_cache()
        if any(score is None for score in scores):
            raise RuntimeError(f"{self._label} cache resolution was incomplete")
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
                re.fullmatch(r"[a-f0-9]{64}", key) is None or not self._score_is_valid(value)
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


def _validate_reranker_artifact(
    artifact: VerifiedModelArtifact,
    *,
    label: str,
    model_ids: frozenset[str],
    adapter_id: str,
    adapter_revision: str,
) -> None:
    """Fail closed unless the manifest is exactly this adapter's sealed identity."""

    content = artifact.manifest.content
    if content.artifact_kind is not ModelArtifactKind.RERANKER:
        raise AdapterConfigurationError(f"{label} requires a reranker artifact")
    if content.model_id not in model_ids:
        raise AdapterConfigurationError(f"{label} requires an allowlisted {label} model")
    if re.fullmatch(r"[a-f0-9]{40,64}", content.revision) is None:
        raise AdapterConfigurationError(
            f"{label} requires an immutable lowercase commit revision"
        )
    if content.dimension != 1:
        raise AdapterConfigurationError("reranker artifacts must declare dimension 1")
    if content.adapter_id != adapter_id or content.adapter_revision != adapter_revision:
        raise AdapterConfigurationError(
            f"artifact requires the allowlisted {label} adapter revision"
        )


class Qwen3RerankerAdapter(_CachedRerankerAdapter):
    """Verified local Qwen3 reranker with bounded batching and stable score semantics."""

    _label = "Qwen3 reranker"
    _cache_version = RERANKER_SCORE_CACHE_VERSION
    _invalid_score_message = "Qwen3 reranker returned an invalid probability"
    _score_bounds = (0.0, 1.0)

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: RerankerRuntime | None = None,
        score_cache: Path | None = None,
    ) -> None:
        _validate_reranker_artifact(
            artifact,
            label="Qwen3 reranker",
            model_ids=QWEN3_RERANKER_MODEL_IDS,
            adapter_id=QWEN3_RERANKER_ADAPTER_ID,
            adapter_revision=QWEN3_RERANKER_ADAPTER_REVISION,
        )
        content = artifact.manifest.content
        try:
            parameters = Qwen3RerankerParameters.model_validate(content.adapter_parameters)
        except ValidationError as error:
            raise AdapterConfigurationError(
                f"invalid parameters for {content.adapter_id}: {error}"
            ) from error
        if device is not None and device != parameters.device:
            raise AdapterConfigurationError(
                "requested device does not match the device sealed in the reranker manifest"
            )
        self._parameters = parameters
        super().__init__(
            artifact,
            instruction=parameters.instruction,
            max_length=parameters.max_length,
            batch_size=parameters.batch_size,
            runtime=runtime
            or _Qwen3TransformersRerankerRuntime(
                artifact.root,
                device=parameters.device,
                dtype=parameters.dtype,
            ),
            score_cache=score_cache,
        )


class BGERerankerV2M3Adapter(_CachedRerankerAdapter):
    """Verified local BAAI/bge-reranker-v2-m3, the multilingual reranking control.

    Scores are sigmoid-normalized relevance probabilities, so they share the Qwen3 lane's
    bounded 0-1 domain. That shared domain is a property of this adapter's sealed
    ``score_mode``, not of reranking in general: the MedCPT specialist deliberately keeps
    its raw unbounded logits. The separate cache version keeps the two from ever being
    served back as one another.
    """

    _label = "BGE reranker"
    _cache_version = BGE_RERANKER_V2_M3_SCORE_CACHE_VERSION
    _invalid_score_message = "BGE reranker returned an invalid probability"
    _score_bounds = (0.0, 1.0)

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: RerankerRuntime | None = None,
        score_cache: Path | None = None,
    ) -> None:
        _validate_reranker_artifact(
            artifact,
            label="BGE reranker",
            model_ids=BGE_RERANKER_V2_M3_MODEL_IDS,
            adapter_id=BGE_RERANKER_V2_M3_ADAPTER_ID,
            adapter_revision=BGE_RERANKER_V2_M3_ADAPTER_REVISION,
        )
        content = artifact.manifest.content
        try:
            parameters = BGERerankerV2M3Parameters.model_validate(
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
        self._parameters = parameters
        super().__init__(
            artifact,
            instruction=BGE_RERANKER_V2_M3_INSTRUCTION,
            max_length=parameters.max_length,
            batch_size=parameters.batch_size,
            runtime=runtime
            or _BGERerankerV2M3Runtime(
                artifact.root,
                device=parameters.device,
                dtype=parameters.dtype,
            ),
            score_cache=score_cache,
        )


class MedCPTCrossEncoderAdapter(_CachedRerankerAdapter):
    """Verified local ncbi/MedCPT-Cross-Encoder, the English-biomedical specialist.

    Scores are the model's raw relevance logits, so they are unbounded and comparable
    only within one query's candidate pool. They are deliberately not squashed into
    the Qwen3 adapter's yes-probability, which would present a different model's
    output as the same quantity, and MedCPT is English-only: it is a specialist lane,
    never the multilingual default.
    """

    _label = "MedCPT cross-encoder"
    _cache_version = MEDCPT_CROSS_ENCODER_SCORE_CACHE_VERSION
    _invalid_score_message = "MedCPT cross-encoder returned a non-finite relevance score"
    _score_bounds = None

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: RerankerRuntime | None = None,
        score_cache: Path | None = None,
    ) -> None:
        _validate_reranker_artifact(
            artifact,
            label="MedCPT cross-encoder",
            model_ids=MEDCPT_CROSS_ENCODER_MODEL_IDS,
            adapter_id=MEDCPT_CROSS_ENCODER_ADAPTER_ID,
            adapter_revision=MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
        )
        content = artifact.manifest.content
        try:
            parameters = MedCPTCrossEncoderParameters.model_validate(
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
        self._parameters = parameters
        super().__init__(
            artifact,
            instruction=MEDCPT_CROSS_ENCODER_INSTRUCTION,
            max_length=parameters.max_length,
            batch_size=parameters.batch_size,
            runtime=runtime
            or _MedCPTCrossEncoderRuntime(
                artifact.root,
                device=parameters.device,
                dtype=parameters.dtype,
            ),
            score_cache=score_cache,
        )
