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
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

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
    VerifiedModelArtifactPair,
)
from app.schemas.corpus import (
    CorpusReleaseBundle,
    EvidenceApprovalStatus,
    canonical_sha256,
)
from app.schemas.domain import CanonicalModel, utc_now

HASHING_VECTORIZER_REVISION = "1.0.0"
VECTOR_CHECKPOINT_CONTRACT_VERSION = "1.0.0"
_TOKEN_PATTERN = re.compile(r"[\w]+(?:['’\-][\w]+)*", re.UNICODE)


class VectorProductionError(RuntimeError):
    """Raised when an embedding backend returns incomplete or invalid material."""


class RetryableEmbeddingError(RuntimeError):
    """An adapter failure that the bounded production backend may retry."""


@dataclass(frozen=True)
class EmbeddedText:
    dense: tuple[float, ...]
    sparse: SparseVector


class VectorProductionCheckpointContent(CanonicalModel):
    """Digest-sealed resumable state for a release-wide embedding job."""

    schema_version: Literal[VECTOR_CHECKPOINT_CONTRACT_VERSION] = (
        VECTOR_CHECKPOINT_CONTRACT_VERSION
    )
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    generated_at: datetime
    dense: DenseVectorDefinition
    sparse: SparseVectorDefinition
    records: tuple[EvidenceVectorRecord, ...] = ()

    @field_validator("generated_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("checkpoint generation time must be timezone-aware")
        return value

    @field_validator("records")
    @classmethod
    def sort_unique_records(
        cls, value: tuple[EvidenceVectorRecord, ...]
    ) -> tuple[EvidenceVectorRecord, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("checkpoint cannot repeat an evidence ID")
        if ids != sorted(ids):
            raise ValueError("checkpoint evidence records must be sorted")
        return value

    @model_validator(mode="after")
    def validate_shapes(self) -> VectorProductionCheckpointContent:
        for record in self.records:
            if len(record.dense) != self.dense.dimension:
                raise ValueError("checkpoint dense vector dimension mismatch")
            if record.sparse.indices[-1] >= self.sparse.dimension:
                raise ValueError("checkpoint sparse vector dimension mismatch")
        return self


class VectorProductionCheckpoint(CanonicalModel):
    content: VectorProductionCheckpointContent
    checkpoint_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_digest(self) -> VectorProductionCheckpoint:
        if self.checkpoint_sha256 != canonical_sha256(self.content):
            raise ValueError("vector checkpoint digest does not match its content")
        return self

    @classmethod
    def seal(
        cls, content: VectorProductionCheckpointContent
    ) -> VectorProductionCheckpoint:
        return cls(
            content=content,
            checkpoint_sha256=canonical_sha256(content),
        )


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

    async def embed_sparse_queries(self, texts: Sequence[str]) -> Sequence[SparseVector]: ...


class DenseEmbeddingAdapter(Protocol):
    """Artifact-bound dense model runtime supplied by a production integration.

    A dual encoder supplies a verified artifact pair here instead: it pins vector
    identity through the same ``manifest``/``reference`` surface, so both halves stay
    bound to the one candidate identity written into the collection.
    """

    @property
    def artifact(self) -> VerifiedModelArtifact | VerifiedModelArtifactPair: ...

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

    async def embed_sparse_queries(
        self, texts: Sequence[str]
    ) -> Sequence[SparseVector]:
        inputs = tuple(texts)
        if not inputs:
            return ()
        if len(inputs) > self._policy.max_batch_size:
            raise VectorProductionError(
                "sparse query request exceeds the verified backend batch-size limit"
            )
        if any(not isinstance(text, str) or not text.strip() for text in inputs):
            raise VectorProductionError("embedding inputs must be non-empty text")
        sparse = tuple(
            await self._bounded_call(
                "sparse", lambda: self._sparse_adapter.embed_queries(inputs)
            )
        )
        if len(sparse) != len(inputs):
            raise VectorProductionError(
                "sparse adapter returned a different number of vectors than inputs"
            )
        converted: list[SparseVector] = []
        for position, vector in enumerate(sparse):
            if vector.indices[-1] >= self._sparse.dimension:
                raise VectorProductionError(
                    f"sparse adapter returned the wrong dimension at input {position}"
                )
            converted.append(
                SparseVector(
                    indices=vector.indices,
                    values=tuple(_float32(value) for value in vector.values),
                )
            )
        return tuple(converted)

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

    async def embed_sparse_queries(
        self, texts: Sequence[str]
    ) -> Sequence[SparseVector]:
        return tuple(self._embed_one(text).sparse for text in texts)

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
        checkpoint: VectorProductionCheckpoint | None = None,
        progress_callback: Callable[[VectorProductionCheckpoint], None] | None = None,
        checkpoint_interval_batches: int = 1,
    ) -> IndexVectorBatch:
        if checkpoint_interval_batches <= 0:
            raise ValueError("checkpoint interval must be a positive batch count")
        if (
            checkpoint is not None
            and generated_at is not None
            and checkpoint.content.generated_at != generated_at
        ):
            raise ValueError(
                "explicit generation time does not match the vector checkpoint"
            )
        timestamp = (
            checkpoint.content.generated_at
            if checkpoint is not None
            else generated_at or utc_now()
        )
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("vector batch generation time must be timezone-aware")

        evidence = sorted(bundle.evidence, key=lambda item: item.evidence_id)
        records = self._resume_records(bundle, evidence, checkpoint)
        for start in range(len(records), len(evidence), self._batch_size):
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
            checkpoint_interval = self._batch_size * checkpoint_interval_batches
            if progress_callback is not None and (
                len(records) == len(evidence)
                or len(records) % checkpoint_interval == 0
            ):
                progress_callback(
                    VectorProductionCheckpoint.seal(
                        VectorProductionCheckpointContent(
                            corpus_release_id=(
                                bundle.manifest.content.corpus_release_id
                            ),
                            manifest_sha256=bundle.manifest.manifest_sha256,
                            generated_at=timestamp,
                            dense=self._backend.dense_definition,
                            sparse=self._backend.sparse_definition,
                            records=tuple(records),
                        )
                    )
                )

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

    def _resume_records(
        self,
        bundle: CorpusReleaseBundle,
        evidence,
        checkpoint: VectorProductionCheckpoint | None,
    ) -> list[EvidenceVectorRecord]:
        if checkpoint is None:
            return []
        content = checkpoint.content
        if content.corpus_release_id != bundle.manifest.content.corpus_release_id:
            raise VectorProductionError("vector checkpoint corpus release mismatch")
        if content.manifest_sha256 != bundle.manifest.manifest_sha256:
            raise VectorProductionError("vector checkpoint manifest mismatch")
        if content.dense != self._backend.dense_definition:
            raise VectorProductionError("vector checkpoint dense model mismatch")
        if content.sparse != self._backend.sparse_definition:
            raise VectorProductionError("vector checkpoint sparse model mismatch")
        expected_prefix = evidence[: len(content.records)]
        if [item.evidence_id for item in expected_prefix] != [
            item.evidence_id for item in content.records
        ]:
            raise VectorProductionError(
                "vector checkpoint is not a contiguous sorted release prefix"
            )
        for evidence_record, vector_record in zip(
            expected_prefix, content.records, strict=True
        ):
            if evidence_record.sha256 != vector_record.evidence_sha256:
                raise VectorProductionError(
                    "vector checkpoint evidence digest mismatch: "
                    f"{evidence_record.evidence_id}"
                )
        return list(content.records)
