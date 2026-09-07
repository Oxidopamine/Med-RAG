"""Contracts for reproducible, release-scoped Qdrant index construction."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.schemas import VerifiedAttestationReference
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

INDEX_CONTRACT_VERSION = "1.0.0"


class EmbeddingModelReference(CanonicalModel):
    """Pinned identity for vector material generated outside the indexer."""

    model_id: str = Field(min_length=1, max_length=300)
    revision: str = Field(min_length=1, max_length=300)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)


class DenseVectorDefinition(CanonicalModel):
    name: str = Field(default="dense", min_length=1, max_length=100)
    dimension: int = Field(gt=0, le=65_536)
    distance: Literal["Cosine", "Dot", "Euclid", "Manhattan"] = "Cosine"
    model: EmbeddingModelReference


class SparseVectorDefinition(CanonicalModel):
    name: str = Field(default="sparse", min_length=1, max_length=100)
    dimension: int = Field(gt=0, le=2**32)
    modifier: Literal["idf"] = "idf"
    model: EmbeddingModelReference


class SparseVector(CanonicalModel):
    indices: tuple[int, ...] = Field(min_length=1)
    values: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sparse_vector(self) -> SparseVector:
        if len(self.indices) != len(self.values):
            raise ValueError("sparse vector indices and values must have equal length")
        if tuple(sorted(self.indices)) != self.indices or len(set(self.indices)) != len(
            self.indices
        ):
            raise ValueError("sparse vector indices must be unique and strictly increasing")
        if self.indices[0] < 0:
            raise ValueError("sparse vector indices cannot be negative")
        if any(not math.isfinite(value) or value == 0 for value in self.values):
            raise ValueError("sparse vector values must be finite and non-zero")
        return self


class EvidenceVectorRecord(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    dense: tuple[float, ...] = Field(min_length=1)
    sparse: SparseVector

    @field_validator("dense")
    @classmethod
    def validate_dense_vector(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(item) for item in value):
            raise ValueError("dense vector values must be finite")
        return value


class IndexVectorBatchContent(CanonicalModel):
    schema_version: Literal[INDEX_CONTRACT_VERSION] = INDEX_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    generated_at: datetime
    dense: DenseVectorDefinition
    sparse: SparseVectorDefinition
    records: tuple[EvidenceVectorRecord, ...] = Field(min_length=1)

    @field_validator("records")
    @classmethod
    def sort_unique_records(
        cls, value: tuple[EvidenceVectorRecord, ...]
    ) -> tuple[EvidenceVectorRecord, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("a vector batch cannot repeat an evidence ID")
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    @model_validator(mode="after")
    def validate_vector_shapes(self) -> IndexVectorBatchContent:
        if self.dense.name == self.sparse.name:
            raise ValueError("dense and sparse vector names must differ")
        for record in self.records:
            if len(record.dense) != self.dense.dimension:
                raise ValueError(
                    f"dense vector dimension mismatch for evidence {record.evidence_id}"
                )
            if record.sparse.indices[-1] >= self.sparse.dimension:
                raise ValueError(
                    f"sparse vector dimension mismatch for evidence {record.evidence_id}"
                )
        return self


class IndexVectorBatch(CanonicalModel):
    content: IndexVectorBatchContent
    batch_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> IndexVectorBatch:
        if self.batch_sha256 != canonical_sha256(self.content):
            raise ValueError("vector batch digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: IndexVectorBatchContent) -> IndexVectorBatch:
        return cls(content=content, batch_sha256=canonical_sha256(content))


class RetrievalSmokeResult(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    point_id: str = Field(min_length=1, max_length=64)
    dense_rank: int = Field(gt=0)
    sparse_rank: int = Field(gt=0)
    result_limit: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_ranks(self) -> RetrievalSmokeResult:
        if self.dense_rank > self.result_limit or self.sparse_rank > self.result_limit:
            raise ValueError("smoke-test ranks must fall within the result limit")
        return self


class IndexValidationReportContent(CanonicalModel):
    schema_version: Literal[INDEX_CONTRACT_VERSION] = INDEX_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    qdrant_version: str = Field(min_length=1, max_length=100)
    collection_status: Literal["green"] = "green"
    collection_config_sha256: str = Field(pattern=SHA256_PATTERN)
    vector_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    point_id_set_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_set_sha256: str = Field(pattern=SHA256_PATTERN)
    payload_set_sha256: str = Field(pattern=SHA256_PATTERN)
    vector_set_sha256: str = Field(pattern=SHA256_PATTERN)
    point_count: int = Field(gt=0)
    dense: DenseVectorDefinition
    sparse: SparseVectorDefinition
    smoke_results: tuple[RetrievalSmokeResult, ...] = Field(min_length=1)
    validated_at: datetime
    outcome: Literal["VALIDATED"] = "VALIDATED"


class IndexValidationReport(CanonicalModel):
    content: IndexValidationReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> IndexValidationReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("index validation report digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: IndexValidationReportContent) -> IndexValidationReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


class IndexAttestationContent(CanonicalModel):
    schema_version: Literal[INDEX_CONTRACT_VERSION] = INDEX_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    validation_report_sha256: str = Field(pattern=SHA256_PATTERN)
    qa_run_id: str = Field(min_length=1, max_length=64)
    # A composite release has one QA run per member document, so `qa_run_id` alone cannot
    # describe what was attested. This lists every member run; it stays empty for a
    # single-document release, which keeps the 1.0.0 contract additive. The version is not
    # bumped on purpose: it is shared with the sealed vector batch and is written into
    # every Qdrant payload as `index_schema_version`, so raising it would invalidate
    # already-sealed vectors and already-attested collections.
    qa_run_ids: tuple[str, ...] = ()
    materialized_count: int = Field(gt=0)
    approved_count: int = Field(gt=0)
    quarantined_count: int = Field(ge=0)
    point_count: int = Field(gt=0)
    attested_at: datetime
    outcome: Literal["VALIDATED"] = "VALIDATED"

    @model_validator(mode="after")
    def verify_accounting(self) -> IndexAttestationContent:
        if self.approved_count + self.quarantined_count != self.materialized_count:
            raise ValueError("index attestation QA counts do not reconcile")
        if self.point_count != self.approved_count:
            raise ValueError("index point count must equal the approved evidence count")
        if self.qa_run_ids:
            if len(set(self.qa_run_ids)) != len(self.qa_run_ids):
                raise ValueError("index attestation repeats a QA run")
            if tuple(sorted(self.qa_run_ids)) != self.qa_run_ids:
                raise ValueError("index attestation QA runs must be ordered")
            if self.qa_run_id != self.qa_run_ids[0]:
                raise ValueError("index attestation QA run is not the first member run")
        return self


class SignedIndexAttestation(CanonicalModel):
    content: IndexAttestationContent
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    validation_report: IndexValidationReport
    attestation: VerifiedAttestationReference

    @model_validator(mode="after")
    def verify_links(self) -> SignedIndexAttestation:
        expected = canonical_sha256(self.content)
        if self.statement_sha256 != expected:
            raise ValueError("index attestation statement digest is inconsistent")
        if self.attestation.statement_sha256 != expected:
            raise ValueError("registered signature covers different index content")
        if self.content.validation_report_sha256 != self.validation_report.report_sha256:
            raise ValueError("index attestation covers a different validation report")
        report = self.validation_report.content
        if (
            report.corpus_release_id != self.content.corpus_release_id
            or report.manifest_sha256 != self.content.manifest_sha256
            or report.qdrant_collection != self.content.qdrant_collection
            or report.point_count != self.content.point_count
        ):
            raise ValueError("index attestation and validation report release facts differ")
        return self
