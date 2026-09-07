"""Contracts for composing many QA'd documents into one servable release.

## Why this exists

Serving holds one release. `ServingPipeline` carries one release's evidence map and one
collection, `corpus_releases.qdrant_collection` is unique per release, and fusion is
weighted RRF over a single candidate pool - so N releases would mean N searches fused
across N independently ranked lists, which is a different ranking function and a new
benchmark contract rather than a wider filter. A multi-document corpus therefore has to
arrive as one release. See docs/narrative-corpus-composition.md, D1.

## What it is *not*

It is not a new release schema. `CorpusReleaseManifestContent` already carries
`inventory_snapshots` as a tuple validated against repeating a trust root, and `evidence`
as a tuple - it was multi-source from the start. So assembly merges bundles and hands the
result to the same `CorpusReleaseService.register_candidate` every single-document release
goes through, and the same registry gate runs over the union.

The new signed object is the *decision*: which QA runs were composed, at which digests, and
what manifest resulted. Without that, "which documents are in this release" would be
answerable only by reading a manifest and trusting it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.schemas import (
    STEWARD_CONTRACT_VERSION,
    VerifiedAttestationReference,
)
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

RELEASE_ASSEMBLER_NAME = "corpus-release-assembler"
RELEASE_ASSEMBLER_VERSION = "1.0.0"


class ReleaseAssemblyMember(CanonicalModel):
    """One decided QA run contributing to a composite release.

    A member has no release of its own - that is the point of `DECIDED`. What identifies it
    is the decision batch it sealed and the evidence manifest that batch was taken over.
    """

    qa_run_id: str = Field(min_length=1, max_length=64)
    corpus_release_candidate_id: str = Field(min_length=1, max_length=64)
    materialization_run_id: str = Field(min_length=1, max_length=64)
    decision_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    # Approved, not materialized. The composite's evidence is the union of what its
    # members *approved*; a quarantined record was decided and deliberately not
    # promoted, so counting it here would make the totals disagree with the release.
    approved_count: int = Field(ge=1)


class ReleaseAssemblyContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    assembler_name: Literal[RELEASE_ASSEMBLER_NAME] = RELEASE_ASSEMBLER_NAME
    assembler_version: str = Field(default=RELEASE_ASSEMBLER_VERSION, min_length=1)
    members: tuple[ReleaseAssemblyMember, ...] = Field(min_length=1)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    evidence_count: int = Field(ge=1)
    assembled_at: datetime

    @field_validator("members")
    @classmethod
    def sort_unique_members(
        cls, value: tuple[ReleaseAssemblyMember, ...]
    ) -> tuple[ReleaseAssemblyMember, ...]:
        qa_runs = [item.qa_run_id for item in value]
        if len(qa_runs) != len(set(qa_runs)):
            raise ValueError("a release cannot compose the same QA run twice")
        return tuple(sorted(value, key=lambda item: item.qa_run_id))

    @model_validator(mode="after")
    def verify_totals(self) -> ReleaseAssemblyContent:
        # The composite's evidence count is the sum of its members', which holds only
        # because assembly refuses colliding evidence IDs. Checking it here means a
        # collision that slipped past that check cannot be signed as if it had not.
        if self.evidence_count != sum(item.approved_count for item in self.members):
            raise ValueError("composite evidence count must equal the sum of its members")
        return self


class SignedReleaseAssembly(CanonicalModel):
    content: ReleaseAssemblyContent
    assembly_sha256: str = Field(pattern=SHA256_PATTERN)
    attestation: VerifiedAttestationReference

    @model_validator(mode="after")
    def verify_digest_and_attestation(self) -> SignedReleaseAssembly:
        expected = canonical_sha256(self.content)
        if self.assembly_sha256 != expected:
            raise ValueError("release assembly digest is inconsistent")
        if self.attestation.statement_sha256 != expected:
            raise ValueError("release assembly signature covers different content")
        return self
