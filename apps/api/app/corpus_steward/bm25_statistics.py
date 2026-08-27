"""Release-derived BM25 token-length statistics."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from app.corpus_steward.embedding_adapters import (
    BM25AdapterParameters,
    unicode_medical_tokens,
)
from app.schemas.corpus import SHA256_PATTERN, CorpusReleaseBundle, canonical_sha256
from app.schemas.domain import CanonicalModel

BM25_STATISTICS_CONTRACT_VERSION = "1.0.0"


class BM25ReleaseStatisticsContent(CanonicalModel):
    schema_version: Literal[BM25_STATISTICS_CONTRACT_VERSION] = (
        BM25_STATISTICS_CONTRACT_VERSION
    )
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    tokenizer: Literal["unicode-medical-v1"] = "unicode-medical-v1"
    tokenization_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    document_prefix: str = Field(max_length=200)
    stopwords_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    document_count: int = Field(gt=0)
    total_token_count: int = Field(gt=0)
    minimum_document_length: int = Field(gt=0)
    median_document_length: int = Field(gt=0)
    p95_document_length: int = Field(gt=0)
    maximum_document_length: int = Field(gt=0)
    average_document_length: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_distribution(self) -> BM25ReleaseStatisticsContent:
        if not (
            self.minimum_document_length
            <= self.median_document_length
            <= self.p95_document_length
            <= self.maximum_document_length
        ):
            raise ValueError("BM25 document-length distribution is inconsistent")
        expected_average = self.total_token_count / self.document_count
        if not math.isclose(self.average_document_length, expected_average):
            raise ValueError("BM25 average document length is inconsistent")
        return self


class BM25ReleaseStatistics(CanonicalModel):
    content: BM25ReleaseStatisticsContent
    statistics_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> BM25ReleaseStatistics:
        if self.statistics_sha256 != canonical_sha256(self.content):
            raise ValueError("BM25 release-statistics digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: BM25ReleaseStatisticsContent) -> BM25ReleaseStatistics:
        return cls(content=content, statistics_sha256=canonical_sha256(content))


def derive_bm25_release_statistics(
    bundle: CorpusReleaseBundle,
    adapter_parameters: BM25AdapterParameters,
) -> BM25ReleaseStatistics:
    if blockers := bundle.activation_blockers():
        raise ValueError(
            "BM25 statistics require an activatable canonical release: "
            + ", ".join(blockers)
        )
    if adapter_parameters.stopwords_path is not None:
        raise ValueError(
            "release statistics require explicit stopword artifact loading when configured"
        )
    tokenization = {
        "tokenizer": adapter_parameters.tokenizer,
        "document_prefix": adapter_parameters.document_prefix,
        "stopwords_sha256": None,
    }
    lengths = []
    prefix = adapter_parameters.document_prefix
    for evidence in bundle.evidence:
        text = f"{prefix} {evidence.content_search}" if prefix else evidence.content_search
        length = len(unicode_medical_tokens(text))
        if length == 0:
            raise ValueError(f"evidence contains no BM25 terms: {evidence.evidence_id}")
        lengths.append(length)
    ordered = sorted(lengths)
    count = len(ordered)
    total = sum(ordered)
    median_index = math.ceil(0.5 * count) - 1
    p95_index = math.ceil(0.95 * count) - 1
    return BM25ReleaseStatistics.seal(
        BM25ReleaseStatisticsContent(
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            tokenization_configuration_sha256=canonical_sha256(tokenization),
            document_prefix=prefix,
            document_count=count,
            total_token_count=total,
            minimum_document_length=ordered[0],
            median_document_length=ordered[median_index],
            p95_document_length=ordered[p95_index],
            maximum_document_length=ordered[-1],
            average_document_length=total / count,
        )
    )
