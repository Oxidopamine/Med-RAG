"""Model-pinned production of sealed dense and sparse vector batches."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import struct
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from numbers import Real
from typing import Protocol

from app.corpus_steward.index_schemas import (
    DenseVectorDefinition,
    EmbeddingModelReference,
    EvidenceVectorRecord,
    IndexVectorBatch,
    IndexVectorBatchContent,
    SparseVector,
    SparseVectorDefinition,
)
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    VerifiedModelArtifact,
)
from app.schemas.corpus import (
    CorpusReleaseBundle,
    EvidenceApprovalStatus,
    canonical_sha256,
)
from app.schemas.domain import utc_now

HASHING_VECTORIZER_REVISION = "1.0.0"
_TOKEN_PATTERN = re.compile(r"[\w]+(?:['’\-][\w]+)*", re.UNICODE)


class VectorProductionError(RuntimeError):
    """Raised when an embedding backend returns incomplete or invalid material."""


class RetryableEmbeddingError(RuntimeError):
    """An adapter failure that the bounded production backend may retry."""


@dataclass(frozen=True)
class EmbeddedText:
    dense: tuple[float, ...]
    sparse: SparseVector


class EmbeddingBackend(Protocol):
    """Backend seam with distinct document and query encoding operations."""

    @property
    def dense_definition(self) -> DenseVectorDefinition: ...

    @property
    def sparse_definition(self) -> SparseVectorDefinition: ...

    @property
    def dense_adapter_identity(self) -> tuple[str, str]: ...

    @property
    def sparse_adapter_identity(self) -> tuple[str, str]: ...

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddedText]: ...

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[EmbeddedText]: ...


class DenseEmbeddingAdapter(Protocol):
    """Artifact-bound dense model runtime supplied by a production integration."""

    @property
    def artifact(self) -> VerifiedModelArtifact: ...

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]: ...

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class SparseEmbeddingAdapter(Protocol):
    """Artifact-bound sparse runtime, allowing asymmetric BM25 query encoding."""

    @property
    def artifact(self) -> VerifiedModelArtifact: ...

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[SparseVector]: ...

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[SparseVector]: ...


@dataclass(frozen=True)
class EmbeddingExecutionPolicy:
    """Hard resource and retry limits for production model calls."""

    max_batch_size: int = 32
    timeout_seconds: float = 120.0
    max_attempts: int = 2
    retry_delay_seconds: float = 0.25
    max_retry_delay_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.max_batch_size <= 0:
            raise ValueError("embedding maximum batch size must be positive")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("embedding timeout must be finite and positive")
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("embedding maximum attempts must be between 1 and 5")
        if (
            not math.isfinite(self.retry_delay_seconds)
            or self.retry_delay_seconds < 0
            or not math.isfinite(self.max_retry_delay_seconds)
            or self.max_retry_delay_seconds < 0
        ):
            raise ValueError("embedding retry delays must be finite and non-negative")


class ProductionEmbeddingBackend:
    """Compose independently verified dense and sparse production adapters.

    The backend never downloads artifacts and never infers their identity from runtime
    output. Adapter behavior, parameters, dimensions, and every local artifact byte are
    bound by the verified manifests used to construct the vector definitions.
    """

    def __init__(
        self,
        dense: DenseEmbeddingAdapter,
        sparse: SparseEmbeddingAdapter,
        *,
        policy: EmbeddingExecutionPolicy | None = None,
        dense_name: str = "dense",
        dense_distance: str = "Cosine",
        sparse_name: str = "sparse",
    ) -> None:
        if dense.artifact.manifest.content.artifact_kind is not ModelArtifactKind.DENSE:
            raise ValueError("dense adapter is not bound to a verified dense artifact")
        if sparse.artifact.manifest.content.artifact_kind is not ModelArtifactKind.SPARSE:
            raise ValueError("sparse adapter is not bound to a verified sparse artifact")
        if dense_name == sparse_name:
            raise ValueError("dense and sparse vector names must differ")
        self._dense_adapter = dense
        self._sparse_adapter = sparse
        self._policy = policy or EmbeddingExecutionPolicy()
        self._dense = DenseVectorDefinition(
            name=dense_name,
            dimension=dense.artifact.manifest.content.dimension,
            distance=dense_distance,
            model=dense.artifact.reference,
        )
        self._sparse = SparseVectorDefinition(
            name=sparse_name,
            dimension=sparse.artifact.manifest.content.dimension,
            model=sparse.artifact.reference,
        )

    @property
    def dense_definition(self) -> DenseVectorDefinition:
        return self._dense

    @property
    def sparse_definition(self) -> SparseVectorDefinition:
        return self._sparse

    @property
    def dense_adapter_identity(self) -> tuple[str, str]:
        content = self._dense_adapter.artifact.manifest.content
        return content.adapter_id, content.adapter_revision

    @property
    def sparse_adapter_identity(self) -> tuple[str, str]:
        content = self._sparse_adapter.artifact.manifest.content
        return content.adapter_id, content.adapter_revision

    @property
    def maximum_batch_size(self) -> int:
        return self._policy.max_batch_size

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        return await self._embed(texts, input_kind="documents")

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        return await self._embed(texts, input_kind="queries")

    async def _embed(
        self, texts: Sequence[str], *, input_kind: str
    ) -> tuple[EmbeddedText, ...]:
        inputs = tuple(texts)
        if not inputs:
            return ()
        if len(inputs) > self._policy.max_batch_size:
            raise VectorProductionError(
                "embedding request exceeds the verified backend batch-size limit"
            )
        if any(not isinstance(text, str) or not text.strip() for text in inputs):
            raise VectorProductionError("embedding inputs must be non-empty text")

        dense_method = getattr(self._dense_adapter, f"embed_{input_kind}")
        sparse_method = getattr(self._sparse_adapter, f"embed_{input_kind}")
        dense = tuple(
            await self._bounded_call("dense", lambda: dense_method(inputs))
        )
        sparse = tuple(
            await self._bounded_call("sparse", lambda: sparse_method(inputs))
        )
        if len(dense) != len(inputs):
            raise VectorProductionError(
                "dense adapter returned a different number of vectors than inputs"
            )
        if len(sparse) != len(inputs):
            raise VectorProductionError(
                "sparse adapter returned a different number of vectors than inputs"
            )

        embedded: list[EmbeddedText] = []
        for position, (dense_vector, sparse_vector) in enumerate(
            zip(dense, sparse, strict=True)
        ):
            try:
                converted_dense = tuple(_float32(value) for value in dense_vector)
            except (OverflowError, struct.error, TypeError, ValueError) as error:
                raise VectorProductionError(
                    f"dense adapter returned invalid values at input {position}"
                ) from error
            if len(converted_dense) != self._dense.dimension:
                raise VectorProductionError(
                    f"dense adapter returned the wrong dimension at input {position}"
                )
            if sparse_vector.indices[-1] >= self._sparse.dimension:
                raise VectorProductionError(
                    f"sparse adapter returned the wrong dimension at input {position}"
                )
            converted_sparse = SparseVector(
                indices=sparse_vector.indices,
                values=tuple(_float32(value) for value in sparse_vector.values),
            )
            embedded.append(
                EmbeddedText(dense=converted_dense, sparse=converted_sparse)
            )
        return tuple(embedded)

    async def _bounded_call(
        self,
        adapter_name: str,
        operation: Callable[[], Awaitable[Sequence[object]]],
    ) -> Sequence[object]:
        delay = self._policy.retry_delay_seconds
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                return await asyncio.wait_for(
                    operation(), timeout=self._policy.timeout_seconds
                )
            except asyncio.TimeoutError as error:
                failure: BaseException = error
            except RetryableEmbeddingError as error:
                failure = error
            except Exception as error:
                raise VectorProductionError(
                    f"{adapter_name} adapter failed with a non-retryable error: "
                    f"{error.__class__.__name__}"
                ) from error
            if attempt == self._policy.max_attempts:
                raise VectorProductionError(
                    f"{adapter_name} adapter exhausted {attempt} bounded attempts"
                ) from failure
            if delay:
                await asyncio.sleep(delay)
            delay = min(delay * 2, self._policy.max_retry_delay_seconds)
        raise AssertionError("bounded embedding retry loop did not terminate")


def _float32(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("embedding value is not a real number")
    converted = struct.unpack("<f", struct.pack("<f", value))[0]
    if not math.isfinite(converted):
        raise VectorProductionError("embedding value is not a finite float32")
    return converted


def _tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = tuple(_TOKEN_PATTERN.findall(normalized))
    if tokens:
        return tokens
    fallback = "".join(character for character in normalized if not character.isspace())
    if not fallback:
        raise VectorProductionError("embedding input contains no searchable text")
    return (fallback,)


def _feature_hash(feature: str, dimension: int) -> tuple[int, float]:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "little") % dimension
    sign = -1.0 if digest[8] & 1 else 1.0
    return index, sign


class DeterministicHashingBackend:
    """Dependency-free lexical baseline for plumbing tests and benchmark ablations.

    This is deliberately identified as a baseline rather than a clinically selected
    semantic model. Production model adapters implement :class:`EmbeddingBackend` and
    must expose immutable model revision and artifact digests through their definitions.
    """

    def __init__(
        self,
        *,
        dense_dimension: int = 384,
        sparse_dimension: int = 2**18,
    ) -> None:
        dense_artifact = canonical_sha256(
            {
                "implementation": "med-rag-deterministic-hashing-dense",
                "revision": HASHING_VECTORIZER_REVISION,
                "tokenizer": "unicode-nfkc-casefold-word-unigram-bigram-v1",
                "dimension": dense_dimension,
            }
        )
        sparse_artifact = canonical_sha256(
            {
                "implementation": "med-rag-deterministic-hashing-sparse",
                "revision": HASHING_VECTORIZER_REVISION,
                "tokenizer": "unicode-nfkc-casefold-word-unigram-v1",
                "term_frequency": "1+ln(tf)",
                "dimension": sparse_dimension,
            }
        )
        self._dense = DenseVectorDefinition(
            dimension=dense_dimension,
            model=EmbeddingModelReference(
                model_id="med-rag/baseline-hashing-dense",
                revision=HASHING_VECTORIZER_REVISION,
                artifact_sha256=dense_artifact,
            ),
        )
        self._sparse = SparseVectorDefinition(
            dimension=sparse_dimension,
            model=EmbeddingModelReference(
                model_id="med-rag/baseline-hashing-sparse",
                revision=HASHING_VECTORIZER_REVISION,
                artifact_sha256=sparse_artifact,
            ),
        )

    @property
    def dense_definition(self) -> DenseVectorDefinition:
        return self._dense

    @property
    def sparse_definition(self) -> SparseVectorDefinition:
        return self._sparse

    @property
    def dense_adapter_identity(self) -> tuple[str, str]:
        return "med-rag/deterministic-dense", HASHING_VECTORIZER_REVISION

    @property
    def sparse_adapter_identity(self) -> tuple[str, str]:
        return "med-rag/deterministic-sparse", HASHING_VECTORIZER_REVISION

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        return await self.embed(texts)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        return await self.embed(texts)

    async def embed(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        """Compatibility alias for callers that do not distinguish input roles."""

        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> EmbeddedText:
        tokens = _tokens(text)
        dense = [0.0] * self._dense.dimension
        dense_features = [(f"u:{token}", 1.0) for token in tokens]
        dense_features.extend(
            (f"b:{left}\x1f{right}", 0.5)
            for left, right in zip(tokens, tokens[1:], strict=False)
        )
        for feature, weight in dense_features:
            index, sign = _feature_hash(feature, self._dense.dimension)
            dense[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in dense))
        if norm == 0:
            index, _ = _feature_hash(f"fallback:{tokens[0]}", self._dense.dimension)
            dense[index] = 1.0
            norm = 1.0
        dense_vector = tuple(
            0.0 if value == 0 else _float32(value / norm) for value in dense
        )

        sparse_counts: Counter[int] = Counter()
        for token in tokens:
            index, _ = _feature_hash(f"s:{token}", self._sparse.dimension)
            sparse_counts[index] += 1
        indices = tuple(sorted(sparse_counts))
        values = tuple(_float32(1.0 + math.log(sparse_counts[index])) for index in indices)
        return EmbeddedText(
            dense=dense_vector,
            sparse=SparseVector(indices=indices, values=values),
        )


class VectorBatchProducer:
    """Generate an all-or-nothing sealed batch from approved release evidence."""

    def __init__(self, backend: EmbeddingBackend, *, batch_size: int = 32) -> None:
        if batch_size <= 0:
            raise ValueError("embedding batch size must be positive")
        maximum_batch_size = getattr(backend, "maximum_batch_size", None)
        if maximum_batch_size is not None and batch_size > maximum_batch_size:
            raise ValueError(
                "producer batch size exceeds the embedding backend batch-size limit"
            )
        self._backend = backend
        self._batch_size = batch_size

    async def produce(
        self,
        bundle: CorpusReleaseBundle,
        *,
        generated_at: datetime | None = None,
    ) -> IndexVectorBatch:
        timestamp = generated_at or utc_now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("vector batch generation time must be timezone-aware")

        evidence = sorted(bundle.evidence, key=lambda item: item.evidence_id)
        records: list[EvidenceVectorRecord] = []
        for start in range(0, len(evidence), self._batch_size):
            batch = evidence[start : start + self._batch_size]
            for item in batch:
                if (
                    item.verification.approval_status
                    is not EvidenceApprovalStatus.APPROVED
                ):
                    raise VectorProductionError(
                        f"refusing to embed unapproved evidence: {item.evidence_id}"
                    )
            embedded = tuple(
                await self._backend.embed_documents(
                    tuple(item.content_search for item in batch)
                )
            )
            if len(embedded) != len(batch):
                raise VectorProductionError(
                    "embedding backend returned a different number of vectors than inputs"
                )
            for item, vector in zip(batch, embedded, strict=True):
                try:
                    records.append(
                        EvidenceVectorRecord(
                            evidence_id=item.evidence_id,
                            evidence_sha256=item.sha256,
                            dense=tuple(_float32(value) for value in vector.dense),
                            sparse=SparseVector(
                                indices=vector.sparse.indices,
                                values=tuple(
                                    _float32(value) for value in vector.sparse.values
                                ),
                            ),
                        )
                    )
                except (OverflowError, struct.error, ValueError) as error:
                    raise VectorProductionError(
                        f"invalid vectors for evidence {item.evidence_id}: {error}"
                    ) from error

        try:
            content = IndexVectorBatchContent(
                corpus_release_id=bundle.manifest.content.corpus_release_id,
                manifest_sha256=bundle.manifest.manifest_sha256,
                generated_at=timestamp,
                dense=self._backend.dense_definition,
                sparse=self._backend.sparse_definition,
                records=tuple(records),
            )
        except ValueError as error:
            raise VectorProductionError(
                f"embedding batch violates index contract: {error}"
            ) from error
        return IndexVectorBatch.seal(content)
