"""Sealed retrieval-candidate configuration used by indexing and benchmarks."""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.index_schemas import EmbeddingModelReference
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

CANDIDATE_CONTRACT_VERSION = "1.0.0"


class CandidateLaneKind(str, Enum):
    DENSE = "DENSE"
    BM25 = "BM25"
    LEARNED_LEXICAL = "LEARNED_LEXICAL"
    EXACT_TERMINOLOGY = "EXACT_TERMINOLOGY"
    SAFETY_QUERY = "SAFETY_QUERY"
    BIOMEDICAL_SPECIALIST = "BIOMEDICAL_SPECIALIST"


class CandidateLane(CanonicalModel):
    lane_id: str = Field(min_length=1, max_length=100)
    kind: CandidateLaneKind
    vector_name: str = Field(min_length=1, max_length=100)
    model: EmbeddingModelReference
    adapter_id: str = Field(min_length=1, max_length=300)
    adapter_revision: str = Field(min_length=1, max_length=300)
    weight: float = Field(default=1.0, ge=0)
    required: bool = True

    @field_validator("weight")
    @classmethod
    def finite_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("candidate lane weights must be finite")
        return value


class CandidateReranker(CanonicalModel):
    model: EmbeddingModelReference
    adapter_id: str = Field(min_length=1, max_length=300)
    adapter_revision: str = Field(min_length=1, max_length=300)
    instruction_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_pool: int = Field(gt=0, le=10_000)
    output_depth: int = Field(gt=0, le=1_000)
    safety_role_preservation: bool = True

    @model_validator(mode="after")
    def validate_depth(self) -> CandidateReranker:
        if self.output_depth > self.candidate_pool:
            raise ValueError("reranker output depth cannot exceed its candidate pool")
        if not self.safety_role_preservation:
            raise ValueError("clinical candidates must preserve required safety roles")
        return self


class RetrievalCandidateContent(CanonicalModel):
    schema_version: Literal[CANDIDATE_CONTRACT_VERSION] = CANDIDATE_CONTRACT_VERSION
    candidate_id: str = Field(min_length=1, max_length=100)
    lanes: tuple[CandidateLane, ...] = Field(min_length=2)
    rrf_k: int = Field(default=60, gt=0, le=100_000)
    candidate_limit: int = Field(default=50, gt=0, le=10_000)
    output_depth: int = Field(default=10, gt=0, le=1_000)
    reranker: CandidateReranker | None = None
    safety_query_revision: str | None = Field(default=None, max_length=300)
    terminology_revision: str | None = Field(default=None, max_length=300)

    @field_validator("lanes")
    @classmethod
    def sort_unique_lanes(cls, value: tuple[CandidateLane, ...]) -> tuple[CandidateLane, ...]:
        lane_ids = [item.lane_id for item in value]
        vector_names = [item.vector_name for item in value]
        if len(lane_ids) != len(set(lane_ids)):
            raise ValueError("candidate lane IDs must be unique")
        if len(vector_names) != len(set(vector_names)):
            raise ValueError("candidate vector names must be unique")
        return tuple(sorted(value, key=lambda item: item.lane_id))

    @model_validator(mode="after")
    def validate_candidate(self) -> RetrievalCandidateContent:
        dense = [item for item in self.lanes if item.kind is CandidateLaneKind.DENSE]
        sparse = [item for item in self.lanes if item.kind is CandidateLaneKind.BM25]
        if len(self.lanes) != 2 or len(dense) != 1 or len(sparse) != 1:
            raise ValueError("the initial candidate contract requires one dense and one BM25 lane")
        if not any(item.weight > 0 for item in self.lanes):
            raise ValueError("at least one candidate lane must have a positive fusion weight")
        if self.candidate_limit < self.output_depth:
            raise ValueError("candidate limit cannot be smaller than output depth")
        if self.reranker is not None and (
            self.reranker.candidate_pool != self.candidate_limit
            or self.reranker.output_depth != self.output_depth
        ):
            raise ValueError("reranker pool/depth must match the candidate pipeline")
        return self

    def lane(self, kind: CandidateLaneKind) -> CandidateLane:
        matches = [item for item in self.lanes if item.kind is kind]
        if len(matches) != 1:
            raise ValueError(f"candidate does not have exactly one {kind.value} lane")
        return matches[0]


class RetrievalCandidateManifest(CanonicalModel):
    content: RetrievalCandidateContent
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> RetrievalCandidateManifest:
        if self.candidate_sha256 != canonical_sha256(self.content):
            raise ValueError("retrieval candidate digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: RetrievalCandidateContent) -> RetrievalCandidateManifest:
        return cls(content=content, candidate_sha256=canonical_sha256(content))
