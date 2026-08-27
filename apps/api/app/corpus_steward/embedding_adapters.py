"""Concrete, local-only dense and BM25 sparse embedding adapters."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from itertools import groupby
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.corpus_steward.index_schemas import SparseVector
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    ModelArtifactRole,
    VerifiedModelArtifact,
    VerifiedModelArtifactPair,
)

BGE_M3_MODEL_ID = "BAAI/bge-m3"
BGE_M3_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BGE_M3_DIMENSION = 1024
BGE_M3_ADAPTER_ID = "med-rag/bge-m3-transformers"
BGE_M3_ADAPTER_REVISION = "1.0.0"

QWEN3_ADAPTER_ID = "med-rag/qwen3-embedding-transformers"
QWEN3_ADAPTER_REVISION = "1.0.0"
QWEN3_OPENVINO_ADAPTER_ID = "med-rag/qwen3-embedding-openvino"
QWEN3_OPENVINO_ADAPTER_REVISION = "1.0.0"
QWEN3_MODEL_DIMENSIONS = {
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "Qwen/Qwen3-Embedding-4B": 2560,
    "Qwen/Qwen3-Embedding-8B": 4096,
}

MEDCPT_PAIR_MODEL_ID = "ncbi/MedCPT"
MEDCPT_QUERY_MODEL_ID = "ncbi/MedCPT-Query-Encoder"
MEDCPT_ARTICLE_MODEL_ID = "ncbi/MedCPT-Article-Encoder"
MEDCPT_DIMENSION = 768
MEDCPT_MAX_POSITIONS = 512
MEDCPT_ADAPTER_ID = "med-rag/medcpt-dual-encoder-transformers"
MEDCPT_ADAPTER_REVISION = "1.0.0"
MEDCPT_PASSAGE_FORMAT = "medcpt-title-section-chunk-v1"
MEDCPT_PASSAGE_SEPARATOR = "\n\n"

BM25_MODEL_ID = "med-rag/qdrant-bm25-unicode"
BM25_MODEL_REVISION = "1.0.0"
BM25_ADAPTER_ID = "med-rag/qdrant-bm25"
BM25_ADAPTER_REVISION = "1.0.0"

_WORD_CONNECTORS = frozenset((".", "'", "’", "-"))


class AdapterConfigurationError(ValueError):
    """Raised when a sealed artifact cannot configure its declared adapter."""


class _AdapterParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BGEM3AdapterParameters(_AdapterParameters):
    """Behavior-affecting parameters that must be sealed into the dense manifest."""

    pooling: Literal["cls", "mean"]
    normalize: bool
    query_prefix: str = Field(max_length=200)
    document_prefix: str = Field(max_length=200)
    max_length: int = Field(gt=0, le=8192)
    batch_size: int = Field(gt=0, le=1024)
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16"]

    @field_validator("device")
    @classmethod
    def validate_explicit_device(cls, value: str) -> str:
        if value == "cpu" or value == "mps" or re.fullmatch(r"cuda(?::\d+)?", value):
            return value
        raise ValueError("device must be cpu, mps, cuda, or an explicit cuda device")


class Qwen3EmbeddingAdapterParameters(_AdapterParameters):
    """Sealed Qwen3 task formatting, pooling, truncation, and runtime choices."""

    pooling: Literal["last_token"]
    normalize: Literal[True]
    query_instruction: str = Field(min_length=1, max_length=2_000)
    document_prefix: Literal[""] = ""
    padding_side: Literal["left"] = "left"
    max_length: int = Field(gt=0, le=32_768)
    batch_size: int = Field(gt=0, le=1024)
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16"]

    @field_validator("device")
    @classmethod
    def validate_explicit_device(cls, value: str) -> str:
        return BGEM3AdapterParameters.validate_explicit_device(value)


class Qwen3OpenVINOAdapterParameters(_AdapterParameters):
    """Sealed CPU/int8 runtime settings for a locally exported OpenVINO IR."""

    pooling: Literal["last_token"]
    normalize: Literal[True]
    query_instruction: str = Field(min_length=1, max_length=2_000)
    document_prefix: Literal[""] = ""
    padding_side: Literal["left"] = "left"
    max_length: int = Field(gt=0, le=32_768)
    batch_size: int = Field(gt=0, le=1_024)
    device: Literal["CPU"] = "CPU"
    dtype: Literal["int8"] = "int8"
    weight_format: Literal["int8"] = "int8"
    openvino_version: str = Field(min_length=1, max_length=100)
    optimum_intel_version: str = Field(min_length=1, max_length=100)
    nncf_version: str = Field(min_length=1, max_length=100)


class MedCPTDualEncoderParameters(_AdapterParameters):
    """Sealed MedCPT encoding contract, shared by both halves of the pair.

    A paired candidate seals its parameters once, on the pair, so the query and article
    encoders can never drift apart in pooling, precision, or device. ``query_max_length``
    and ``document_max_length`` are separate because MedCPT truncates the two sides
    differently in official use (64 for queries, 512 for articles); both are capped at
    the 512 positions the underlying BERT encoders actually have.
    """

    pooling: Literal["cls"]
    normalize: bool
    query_max_length: int = Field(gt=0, le=MEDCPT_MAX_POSITIONS)
    document_max_length: int = Field(gt=0, le=MEDCPT_MAX_POSITIONS)
    passage_format: Literal[MEDCPT_PASSAGE_FORMAT]
    batch_size: int = Field(gt=0, le=1024)
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16"]

    @field_validator("device")
    @classmethod
    def validate_explicit_device(cls, value: str) -> str:
        return BGEM3AdapterParameters.validate_explicit_device(value)


class BM25AdapterParameters(_AdapterParameters):
    """The complete tokenization and BM25 term-frequency contract."""

    tokenizer: Literal["unicode-medical-v1"]
    k1: float = Field(gt=0, le=10)
    b: float = Field(ge=0, le=1)
    average_document_length: float = Field(gt=0)
    query_term_frequency: Literal["binary", "raw"]
    hash_algorithm: Literal["sha256-uint32-le"]
    hash_seed: str = Field(min_length=1, max_length=200)
    query_prefix: str = Field(max_length=200)
    document_prefix: str = Field(max_length=200)
    stopwords_path: str | None

    @field_validator("average_document_length", "k1", "b")
    @classmethod
    def validate_finite_number(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("BM25 numeric parameters must be finite")
        return value


class DenseInferenceRuntime(Protocol):
    """Small testable seam around the optional Torch/Transformers runtime."""

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: Literal["cls", "mean", "last_token"],
        normalize: bool,
    ) -> Sequence[Sequence[float]]: ...


class DualEncoderInferenceRuntime(Protocol):
    """A dense runtime that can also encode the article encoder's sequence pairs."""

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: Literal["cls", "mean", "last_token"],
        normalize: bool,
        text_pairs: Sequence[str] | None = None,
    ) -> Sequence[Sequence[float]]: ...


def _validated_parameters(
    model: type[_AdapterParameters],
    artifact: VerifiedModelArtifact | VerifiedModelArtifactPair,
):
    try:
        return model.model_validate(artifact.manifest.content.adapter_parameters)
    except ValidationError as error:
        raise AdapterConfigurationError(
            f"invalid parameters for {artifact.manifest.content.adapter_id}: {error}"
        ) from error


def _prefixed(prefix: str, text: str) -> str:
    return f"{prefix} {text}" if prefix else text


def _require_adapter(
    artifact: VerifiedModelArtifact,
    *,
    kind: ModelArtifactKind,
    model_id: str,
    model_revision: str | None,
    dimension: int | None,
    adapter_id: str,
    adapter_revision: str,
) -> None:
    content = artifact.manifest.content
    if content.artifact_kind is not kind:
        raise AdapterConfigurationError(
            f"{adapter_id} requires a {kind.value.lower()} artifact"
        )
    if content.model_id != model_id:
        raise AdapterConfigurationError(
            f"{adapter_id} requires model_id {model_id!r}"
        )
    if model_revision is not None and content.revision != model_revision:
        raise AdapterConfigurationError(
            f"{adapter_id} requires model revision {model_revision!r}"
        )
    if dimension is not None and content.dimension != dimension:
        raise AdapterConfigurationError(
            f"{model_id} must declare dimension {dimension}"
        )
    if content.adapter_id != adapter_id or content.adapter_revision != adapter_revision:
        raise AdapterConfigurationError(
            f"artifact requires {adapter_id}@{adapter_revision}"
        )


class _TorchTransformerRuntime:
    """Offline Hugging Face runtime with no remote-code or download path."""

    def __init__(
        self,
        root: Path,
        *,
        device: str,
        dtype: str,
        expected_dimension: int,
        padding_side: Literal["left", "right"] | None = None,
    ) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as error:
            raise AdapterConfigurationError(
                "the BGE-M3 adapter requires the 'retrieval' optional dependencies"
            ) from error

        if device.startswith("cuda") and not torch.cuda.is_available():
            raise AdapterConfigurationError("the pinned CUDA device is unavailable")
        if device == "mps" and not torch.backends.mps.is_available():
            raise AdapterConfigurationError("the pinned MPS device is unavailable")
        if device == "cpu" and dtype == "float16":
            raise AdapterConfigurationError("float16 inference is not supported on CPU")

        torch_dtype = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
        }[dtype]
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                use_fast=True,
            )
            if padding_side is not None:
                self._tokenizer.padding_side = padding_side
            self._model = AutoModel.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                torch_dtype=torch_dtype,
            )
            hidden_size = getattr(self._model.config, "hidden_size", None)
            if hidden_size != expected_dimension:
                raise AdapterConfigurationError(
                    "local model hidden size does not match the sealed dimension"
                )
            self._model.to(device)
            self._model.eval()
        except AdapterConfigurationError:
            raise
        except Exception as error:
            raise AdapterConfigurationError(
                f"local BGE-M3 artifact could not be loaded: {error.__class__.__name__}"
            ) from error

        self._device = device
        self._torch = torch
        self._lock = threading.Lock()

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: Literal["cls", "mean", "last_token"],
        normalize: bool,
        text_pairs: Sequence[str] | None = None,
    ) -> Sequence[Sequence[float]]:
        torch = self._torch
        if text_pairs is not None and len(text_pairs) != len(texts):
            raise ValueError("paired encoder inputs must align with their first segments")
        with self._lock, torch.inference_mode():
            encoded = self._tokenizer(
                list(texts),
                *([list(text_pairs)] if text_pairs is not None else []),
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            inputs = {name: value.to(self._device) for name, value in encoded.items()}
            outputs = self._model(**inputs)
            hidden = outputs.last_hidden_state
            if pooling == "cls":
                pooled = hidden[:, 0]
            elif pooling == "mean":
                mask = inputs["attention_mask"].unsqueeze(-1).to(dtype=hidden.dtype)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            else:
                attention_mask = inputs["attention_mask"]
                if bool((attention_mask[:, -1].sum() == attention_mask.shape[0]).item()):
                    pooled = hidden[:, -1]
                else:
                    sequence_lengths = attention_mask.sum(dim=1) - 1
                    pooled = hidden[
                        torch.arange(hidden.shape[0], device=hidden.device),
                        sequence_lengths,
                    ]
            if normalize:
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            return pooled.detach().to(device="cpu", dtype=torch.float32).tolist()


class _OpenVINOTransformerRuntime:
    """Offline Optimum Intel runtime for a verified local OpenVINO IR artifact."""

    def __init__(
        self,
        root: Path,
        *,
        device: str,
        expected_dimension: int,
        padding_side: Literal["left", "right"] | None = None,
    ) -> None:
        try:
            import numpy as np
            from optimum.intel.openvino import OVModelForFeatureExtraction
            from transformers import AutoTokenizer
        except ImportError as error:
            raise AdapterConfigurationError(
                "the Qwen3 OpenVINO adapter requires the 'cpu-optimized' dependencies"
            ) from error
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                use_fast=True,
            )
            if padding_side is not None:
                self._tokenizer.padding_side = padding_side
            self._model = OVModelForFeatureExtraction.from_pretrained(
                str(root),
                device=device,
                compile=True,
                local_files_only=True,
                trust_remote_code=False,
            )
            hidden_size = getattr(self._model.config, "hidden_size", None)
            if hidden_size != expected_dimension:
                raise AdapterConfigurationError(
                    "OpenVINO model hidden size does not match the sealed dimension"
                )
        except AdapterConfigurationError:
            raise
        except Exception as error:
            raise AdapterConfigurationError(
                f"local Qwen3 OpenVINO artifact could not be loaded: "
                f"{error.__class__.__name__}"
            ) from error
        self._np = np
        self._lock = threading.Lock()

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: Literal["cls", "mean", "last_token"],
        normalize: bool,
    ) -> Sequence[Sequence[float]]:
        if pooling != "last_token":
            raise AdapterConfigurationError("Qwen3 OpenVINO requires last-token pooling")
        np = self._np
        with self._lock:
            encoded = self._tokenizer(
                list(texts),
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="np",
            )
            outputs = self._model(**encoded)
            hidden = np.asarray(outputs.last_hidden_state)
            attention_mask = np.asarray(encoded["attention_mask"])
            if bool(np.all(attention_mask[:, -1] == 1)):
                pooled = hidden[:, -1]
            else:
                sequence_lengths = attention_mask.sum(axis=1) - 1
                pooled = hidden[np.arange(hidden.shape[0]), sequence_lengths]
            if normalize:
                norms = np.linalg.norm(pooled, axis=1, keepdims=True)
                if bool(np.any(~np.isfinite(norms))) or bool(np.any(norms == 0)):
                    raise RuntimeError("OpenVINO runtime returned non-normalizable vectors")
                pooled = pooled / norms
            return pooled.astype(np.float32).tolist()


class BGEM3DenseAdapter:
    """Pinned BAAI/bge-m3 dense encoder using only a verified local artifact."""

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: DenseInferenceRuntime | None = None,
    ) -> None:
        _require_adapter(
            artifact,
            kind=ModelArtifactKind.DENSE,
            model_id=BGE_M3_MODEL_ID,
            model_revision=BGE_M3_MODEL_REVISION,
            dimension=BGE_M3_DIMENSION,
            adapter_id=BGE_M3_ADAPTER_ID,
            adapter_revision=BGE_M3_ADAPTER_REVISION,
        )
        parameters = _validated_parameters(BGEM3AdapterParameters, artifact)
        assert isinstance(parameters, BGEM3AdapterParameters)
        if device is not None and device != parameters.device:
            raise AdapterConfigurationError(
                "requested device does not match the device sealed in the dense manifest"
            )
        self._artifact = artifact
        self._parameters = parameters
        self._runtime = runtime or _TorchTransformerRuntime(
            artifact.root,
            device=parameters.device,
            dtype=parameters.dtype,
            expected_dimension=BGE_M3_DIMENSION,
        )

    @property
    def artifact(self) -> VerifiedModelArtifact:
        return self._artifact

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]:
        return await self._embed(texts, prefix=self._parameters.document_prefix)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return await self._embed(texts, prefix=self._parameters.query_prefix)

    async def _embed(
        self, texts: Sequence[str], *, prefix: str
    ) -> tuple[tuple[float, ...], ...]:
        inputs = tuple(texts)
        if any(not isinstance(text, str) or not text.strip() for text in inputs):
            raise ValueError("dense inputs must be non-empty text")
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(inputs), self._parameters.batch_size):
            batch = tuple(
                _prefixed(prefix, text)
                for text in inputs[start : start + self._parameters.batch_size]
            )
            encoded = await asyncio.to_thread(
                self._runtime.encode,
                batch,
                max_length=self._parameters.max_length,
                pooling=self._parameters.pooling,
                normalize=self._parameters.normalize,
            )
            if len(encoded) != len(batch):
                raise RuntimeError("BGE-M3 runtime returned an incomplete batch")
            vectors.extend(tuple(float(value) for value in vector) for vector in encoded)
        return tuple(vectors)


class Qwen3DenseAdapter:
    """Artifact-bound Qwen3 embedding family with exact official role semantics."""

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: DenseInferenceRuntime | None = None,
    ) -> None:
        content = artifact.manifest.content
        maximum_dimension = QWEN3_MODEL_DIMENSIONS.get(content.model_id)
        if maximum_dimension is None:
            raise AdapterConfigurationError(
                "Qwen3 adapter requires an allowlisted Qwen3 embedding model"
            )
        if re.fullmatch(r"[a-f0-9]{40,64}", content.revision) is None:
            raise AdapterConfigurationError(
                "Qwen3 adapter requires an immutable lowercase commit revision"
            )
        _require_adapter(
            artifact,
            kind=ModelArtifactKind.DENSE,
            model_id=content.model_id,
            model_revision=None,
            dimension=None,
            adapter_id=QWEN3_ADAPTER_ID,
            adapter_revision=QWEN3_ADAPTER_REVISION,
        )
        if not 32 <= content.dimension <= maximum_dimension:
            raise AdapterConfigurationError(
                f"{content.model_id} dimension must be between 32 and {maximum_dimension}"
            )
        parameters = _validated_parameters(Qwen3EmbeddingAdapterParameters, artifact)
        assert isinstance(parameters, Qwen3EmbeddingAdapterParameters)
        if device is not None and device != parameters.device:
            raise AdapterConfigurationError(
                "requested device does not match the device sealed in the dense manifest"
            )
        self._artifact = artifact
        self._parameters = parameters
        self._maximum_dimension = maximum_dimension
        self._runtime = runtime or _TorchTransformerRuntime(
            artifact.root,
            device=parameters.device,
            dtype=parameters.dtype,
            expected_dimension=maximum_dimension,
            padding_side=parameters.padding_side,
        )

    @property
    def artifact(self) -> VerifiedModelArtifact:
        return self._artifact

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]:
        return await self._embed(texts, is_query=False)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return await self._embed(texts, is_query=True)

    async def _embed(
        self, texts: Sequence[str], *, is_query: bool
    ) -> tuple[tuple[float, ...], ...]:
        inputs = tuple(texts)
        if any(not isinstance(text, str) or not text.strip() for text in inputs):
            raise ValueError("Qwen3 inputs must be non-empty text")
        formatted = tuple(
            (
                f"Instruct: {self._parameters.query_instruction}\nQuery:{text}"
                if is_query
                else text
            )
            for text in inputs
        )
        vectors: list[tuple[float, ...]] = []
        output_dimension = self._artifact.manifest.content.dimension
        for start in range(0, len(formatted), self._parameters.batch_size):
            batch = formatted[start : start + self._parameters.batch_size]
            encoded = await asyncio.to_thread(
                self._runtime.encode,
                batch,
                max_length=self._parameters.max_length,
                pooling=self._parameters.pooling,
                # MRL truncation must happen before final normalization.
                normalize=False,
            )
            if len(encoded) != len(batch):
                raise RuntimeError("Qwen3 runtime returned an incomplete batch")
            for vector in encoded:
                if len(vector) != self._maximum_dimension:
                    raise RuntimeError("Qwen3 runtime returned the wrong hidden dimension")
                truncated = tuple(float(value) for value in vector[:output_dimension])
                norm = math.sqrt(sum(value * value for value in truncated))
                if not math.isfinite(norm) or norm == 0:
                    raise RuntimeError("Qwen3 runtime returned a non-normalizable vector")
                vectors.append(tuple(value / norm for value in truncated))
        return tuple(vectors)


class Qwen3OpenVINODenseAdapter(Qwen3DenseAdapter):
    """Qwen3 embedding semantics backed by a digest-sealed CPU/int8 OpenVINO IR."""

    def __init__(
        self,
        artifact: VerifiedModelArtifact,
        *,
        device: str | None = None,
        runtime: DenseInferenceRuntime | None = None,
    ) -> None:
        content = artifact.manifest.content
        maximum_dimension = QWEN3_MODEL_DIMENSIONS.get(content.model_id)
        if maximum_dimension is None:
            raise AdapterConfigurationError(
                "Qwen3 OpenVINO adapter requires an allowlisted Qwen3 model"
            )
        if re.fullmatch(r"[a-f0-9]{40,64}", content.revision) is None:
            raise AdapterConfigurationError(
                "Qwen3 OpenVINO adapter requires an immutable commit revision"
            )
        _require_adapter(
            artifact,
            kind=ModelArtifactKind.DENSE,
            model_id=content.model_id,
            model_revision=None,
            dimension=None,
            adapter_id=QWEN3_OPENVINO_ADAPTER_ID,
            adapter_revision=QWEN3_OPENVINO_ADAPTER_REVISION,
        )
        if not 32 <= content.dimension <= maximum_dimension:
            raise AdapterConfigurationError(
                f"{content.model_id} dimension must be between 32 and {maximum_dimension}"
            )
        parameters = _validated_parameters(Qwen3OpenVINOAdapterParameters, artifact)
        assert isinstance(parameters, Qwen3OpenVINOAdapterParameters)
        if device is not None and device.upper() != parameters.device:
            raise AdapterConfigurationError(
                "requested device does not match the OpenVINO manifest"
            )
        self._artifact = artifact
        self._parameters = parameters
        self._maximum_dimension = maximum_dimension
        self._runtime = runtime or _OpenVINOTransformerRuntime(
            artifact.root,
            device=parameters.device,
            expected_dimension=maximum_dimension,
            padding_side=parameters.padding_side,
        )


class MedCPTDualEncoderAdapter:
    """MedCPT's two asymmetric checkpoints driven as one paired dense candidate.

    Queries go through the query encoder and passages through the article encoder, each
    from its own verified root. Both preserve the official contract: ``[CLS]`` pooling,
    768 dimensions, and BERT's 512-position truncation ceiling. Passages are encoded as
    the official title/abstract sequence pair, filled here by the versioned
    title/section-plus-chunk format sealed in the manifest.
    """

    def __init__(
        self,
        pair: VerifiedModelArtifactPair,
        *,
        device: str | None = None,
        query_runtime: DualEncoderInferenceRuntime | None = None,
        document_runtime: DualEncoderInferenceRuntime | None = None,
    ) -> None:
        content = pair.manifest.content
        if content.artifact_kind is not ModelArtifactKind.DENSE:
            raise AdapterConfigurationError("MedCPT requires a dense artifact pair")
        if content.model_id != MEDCPT_PAIR_MODEL_ID:
            raise AdapterConfigurationError(
                f"MedCPT requires the paired model_id {MEDCPT_PAIR_MODEL_ID!r}"
            )
        if content.dimension != MEDCPT_DIMENSION:
            raise AdapterConfigurationError(
                f"MedCPT must declare dimension {MEDCPT_DIMENSION}"
            )
        if (
            content.adapter_id != MEDCPT_ADAPTER_ID
            or content.adapter_revision != MEDCPT_ADAPTER_REVISION
        ):
            raise AdapterConfigurationError(
                f"artifact pair requires {MEDCPT_ADAPTER_ID}@{MEDCPT_ADAPTER_REVISION}"
            )
        for role, model_id in (
            (ModelArtifactRole.QUERY, MEDCPT_QUERY_MODEL_ID),
            (ModelArtifactRole.DOCUMENT, MEDCPT_ARTICLE_MODEL_ID),
        ):
            member = pair.member(role).manifest.content
            if member.model_id != model_id:
                raise AdapterConfigurationError(
                    f"the MedCPT {role.value.lower()} member must be {model_id!r}"
                )
            if re.fullmatch(r"[a-f0-9]{40,64}", member.revision) is None:
                raise AdapterConfigurationError(
                    "MedCPT requires an immutable lowercase commit revision per encoder"
                )
        parameters = _validated_parameters(MedCPTDualEncoderParameters, pair)
        assert isinstance(parameters, MedCPTDualEncoderParameters)
        if device is not None and device != parameters.device:
            raise AdapterConfigurationError(
                "requested device does not match the device sealed in the paired manifest"
            )
        self._artifact = pair
        self._parameters = parameters
        self._query_runtime = query_runtime or _TorchTransformerRuntime(
            pair.query.root,
            device=parameters.device,
            dtype=parameters.dtype,
            expected_dimension=MEDCPT_DIMENSION,
        )
        self._document_runtime = document_runtime or _TorchTransformerRuntime(
            pair.document.root,
            device=parameters.device,
            dtype=parameters.dtype,
            expected_dimension=MEDCPT_DIMENSION,
        )

    @property
    def artifact(self) -> VerifiedModelArtifactPair:
        return self._artifact

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        inputs = tuple(texts)
        if any(not isinstance(text, str) or not text.strip() for text in inputs):
            raise ValueError("MedCPT queries must be non-empty text")
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(inputs), self._parameters.batch_size):
            batch = inputs[start : start + self._parameters.batch_size]
            vectors.extend(
                await self._encode(
                    self._query_runtime,
                    batch,
                    max_length=self._parameters.query_max_length,
                )
            )
        return tuple(vectors)

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return await self.embed_passages(
            tuple(self._split_passage(text) for text in texts)
        )

    async def embed_passages(
        self, passages: Sequence[tuple[str, str]]
    ) -> Sequence[Sequence[float]]:
        """Encode explicit (title/section, chunk) pairs the article encoder expects."""

        inputs = tuple(passages)
        if any(
            not isinstance(passage, tuple)
            or len(passage) != 2
            or not isinstance(passage[0], str)
            or not isinstance(passage[1], str)
            or not passage[1].strip()
            for passage in inputs
        ):
            raise ValueError("MedCPT passages must be a title/section and a non-empty chunk")
        vectors: list[tuple[float, ...] | None] = [None] * len(inputs)
        for start in range(0, len(inputs), self._parameters.batch_size):
            batch = tuple(
                enumerate(inputs[start : start + self._parameters.batch_size], start=start)
            )
            # Group by whether a title/section is present so a chunk always sees the same
            # token sequence regardless of which rows share its micro-batch.
            for titled in (True, False):
                rows = tuple(item for item in batch if bool(item[1][0].strip()) is titled)
                if not rows:
                    continue
                encoded = await self._encode(
                    self._document_runtime,
                    tuple(row[1][0] if titled else row[1][1] for row in rows),
                    max_length=self._parameters.document_max_length,
                    text_pairs=tuple(row[1][1] for row in rows) if titled else None,
                )
                for (index, _passage), vector in zip(rows, encoded, strict=True):
                    vectors[index] = vector
        if any(vector is None for vector in vectors):
            raise RuntimeError("MedCPT passage encoding was incomplete")
        return tuple(vector for vector in vectors if vector is not None)

    def _split_passage(self, text: str) -> tuple[str, str]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("MedCPT inputs must be non-empty text")
        head, separator, tail = text.partition(MEDCPT_PASSAGE_SEPARATOR)
        if not separator or not tail.strip():
            return "", text
        return head, tail

    async def _encode(
        self,
        runtime: DualEncoderInferenceRuntime,
        texts: Sequence[str],
        *,
        max_length: int,
        text_pairs: Sequence[str] | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        options: dict[str, object] = {}
        if text_pairs is not None:
            options["text_pairs"] = text_pairs
        encoded = await asyncio.to_thread(
            runtime.encode,
            texts,
            max_length=max_length,
            pooling=self._parameters.pooling,
            normalize=self._parameters.normalize,
            **options,
        )
        if len(encoded) != len(texts):
            raise RuntimeError("MedCPT runtime returned an incomplete batch")
        vectors: list[tuple[float, ...]] = []
        for vector in encoded:
            converted = tuple(float(value) for value in vector)
            if len(converted) != MEDCPT_DIMENSION:
                raise RuntimeError("MedCPT runtime returned the wrong hidden dimension")
            if any(not math.isfinite(value) for value in converted):
                raise RuntimeError("MedCPT runtime returned a non-finite vector")
            vectors.append(converted)
        return tuple(vectors)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _unicode_word_surfaces(text: str) -> tuple[str, ...]:
    surfaces: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            surfaces.append("".join(current))
            current.clear()

    for position, character in enumerate(text):
        category = unicodedata.category(character)
        if category[0] in ("L", "N") or (category[0] == "M" and current):
            current.append(character)
            continue
        next_is_word = position + 1 < len(text) and unicodedata.category(
            text[position + 1]
        )[0] in ("L", "N")
        if character in _WORD_CONNECTORS and current and next_is_word:
            current.append(character)
            continue
        flush()
    flush()
    return tuple(surfaces)


def unicode_medical_tokens(text: str) -> tuple[str, ...]:
    """Normalized lexical/accent-folded terms plus CJK characters and bigrams."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    output: list[str] = []
    for token in _unicode_word_surfaces(normalized):
        if not any(_is_cjk(character) for character in token):
            output.append(token)
            accent_folded = "".join(
                character
                for character in unicodedata.normalize("NFKD", token)
                if unicodedata.category(character) != "Mn"
            )
            if accent_folded != token:
                output.append(accent_folded)
            continue
        for cjk_segment, characters in groupby(token, key=_is_cjk):
            segment = list(characters)
            if cjk_segment:
                output.extend(segment)
                output.extend(
                    left + right
                    for left, right in zip(segment, segment[1:], strict=False)
                )
            else:
                output.append("".join(segment))
    return tuple(output)


class QdrantBM25SparseAdapter:
    """BM25 TF encoder designed for Qdrant's collection-level IDF modifier.

    Document vectors carry BM25 length-normalized term-frequency values. Query vectors
    carry binary or raw query term frequency; Qdrant supplies collection IDF at query
    time through the sparse vector definition's required ``idf`` modifier.
    """

    def __init__(self, artifact: VerifiedModelArtifact) -> None:
        _require_adapter(
            artifact,
            kind=ModelArtifactKind.SPARSE,
            model_id=BM25_MODEL_ID,
            model_revision=BM25_MODEL_REVISION,
            dimension=None,
            adapter_id=BM25_ADAPTER_ID,
            adapter_revision=BM25_ADAPTER_REVISION,
        )
        parameters = _validated_parameters(BM25AdapterParameters, artifact)
        assert isinstance(parameters, BM25AdapterParameters)
        self._artifact = artifact
        self._parameters = parameters
        self._dimension = artifact.manifest.content.dimension
        self._stopwords = self._load_stopwords(parameters.stopwords_path)

    @property
    def artifact(self) -> VerifiedModelArtifact:
        return self._artifact

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[SparseVector]:
        return tuple(self._embed_document(text) for text in texts)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[SparseVector]:
        return tuple(self._embed_query(text) for text in texts)

    def _tokens(self, text: str, *, prefix: str) -> tuple[str, ...]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("BM25 inputs must be non-empty text")
        tokens = tuple(
            token
            for token in unicode_medical_tokens(_prefixed(prefix, text))
            if token not in self._stopwords
        )
        if not tokens:
            raise ValueError("BM25 input contains no searchable terms after tokenization")
        return tokens

    def _term_index(self, term: str) -> int:
        material = f"{self._parameters.hash_seed}\0{term}".encode()
        return int.from_bytes(hashlib.sha256(material).digest()[:4], "little") % self._dimension

    def _embed_document(self, text: str) -> SparseVector:
        tokens = self._tokens(text, prefix=self._parameters.document_prefix)
        counts = Counter(tokens)
        length_normalization = 1 - self._parameters.b + (
            self._parameters.b
            * len(tokens)
            / self._parameters.average_document_length
        )
        values_by_index: defaultdict[int, float] = defaultdict(float)
        for term, frequency in counts.items():
            denominator = frequency + self._parameters.k1 * length_normalization
            weight = frequency * (self._parameters.k1 + 1) / denominator
            values_by_index[self._term_index(term)] += weight
        return self._sparse_vector(values_by_index)

    def _embed_query(self, text: str) -> SparseVector:
        tokens = self._tokens(text, prefix=self._parameters.query_prefix)
        counts = Counter(tokens)
        values_by_index: defaultdict[int, float] = defaultdict(float)
        for term, frequency in counts.items():
            value = 1.0 if self._parameters.query_term_frequency == "binary" else float(frequency)
            values_by_index[self._term_index(term)] += value
        return self._sparse_vector(values_by_index)

    @staticmethod
    def _sparse_vector(values_by_index: dict[int, float]) -> SparseVector:
        indices = tuple(sorted(values_by_index))
        return SparseVector(
            indices=indices,
            values=tuple(values_by_index[index] for index in indices),
        )

    def _load_stopwords(self, configured_path: str | None) -> frozenset[str]:
        if configured_path is None:
            return frozenset()
        path = PurePosixPath(configured_path)
        if (
            path.is_absolute()
            or configured_path != path.as_posix()
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise AdapterConfigurationError("stopwords_path must be a normalized relative path")
        declared_paths = {item.path for item in self._artifact.manifest.content.files}
        if configured_path not in declared_paths:
            raise AdapterConfigurationError(
                "stopwords_path is not present in the verified artifact inventory"
            )
        stopwords: set[str] = set()
        try:
            lines = (self._artifact.root / Path(*path.parts)).read_text(
                encoding="utf-8"
            ).splitlines()
        except (OSError, UnicodeError) as error:
            raise AdapterConfigurationError("stopwords file is not valid UTF-8") from error
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            tokens = unicode_medical_tokens(stripped)
            if not tokens:
                raise AdapterConfigurationError(
                    f"stopwords file line {line_number} contains no searchable token"
                )
            stopwords.update(tokens)
        return frozenset(stopwords)
