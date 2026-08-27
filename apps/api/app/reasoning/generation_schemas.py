"""Sealed contracts for the grounded-answer generation lane.

The generation model is the only component in this system that is not deterministic
and not locally verifiable, so everything about how it is invoked is pinned the same
way an embedding adapter is: provider, model, effort, and token ceiling are sealed
parameters carried in a content-addressed artifact rather than runtime configuration.

The answer contract is deliberately narrow. The model is never asked to decide whether
it has enough evidence, only to state which retrieved passages support each claim; the
sufficiency decision is made in application code against the retrieved set, because a
model given insufficient context is measurably *more* confident, not less.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.domain import CanonicalModel

GENERATION_CONTRACT_VERSION = "1.0.0"


class GenerationProvider(str, Enum):
    """Where the Claude model is served from.

    Both are Anthropic-operated model families reached through the Anthropic SDK's
    platform clients, so switching between them - or to a different Claude model on
    either - is a change to a sealed parameter file, not to calling code.
    """

    AWS_BEDROCK = "AWS_BEDROCK"
    GCP_VERTEX = "GCP_VERTEX"


class GenerationEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ConflictType(str, Enum):
    """The conflict taxonomy the answer layer is allowed to report.

    Naming the kind of disagreement is worth roughly twenty accuracy points over
    presenting conflicting passages unlabelled, and for guideline evidence the correct
    output is almost never a resolution - it is both clauses, typed, with provenance.
    """

    NO_CONFLICT = "NO_CONFLICT"
    COMPLEMENTARY = "COMPLEMENTARY"
    CONFLICTING_RECOMMENDATIONS = "CONFLICTING_RECOMMENDATIONS"
    OUTDATED_INFORMATION = "OUTDATED_INFORMATION"
    CONTRADICTORY_SOURCE = "CONTRADICTORY_SOURCE"


class AbstentionReason(str, Enum):
    NO_EVIDENCE_RETRIEVED = "NO_EVIDENCE_RETRIEVED"
    NO_ACTIVE_RELEASE = "NO_ACTIVE_RELEASE"
    MODEL_DECLARED_INSUFFICIENT = "MODEL_DECLARED_INSUFFICIENT"
    NO_CLAIM_SURVIVED_GROUNDING = "NO_CLAIM_SURVIVED_GROUNDING"
    GENERATION_UNAVAILABLE = "GENERATION_UNAVAILABLE"


class GenerationAdapterParameters(CanonicalModel):
    """Sealed invocation settings for one pinned generation candidate."""

    schema_version: Literal[GENERATION_CONTRACT_VERSION] = GENERATION_CONTRACT_VERSION
    provider: GenerationProvider
    # Bedrock model IDs carry an ``anthropic.`` prefix; Vertex uses the bare ID. The
    # adapter applies the prefix, so this field stays the same string across providers.
    model_id: str = Field(min_length=1, max_length=200)
    max_tokens: int = Field(gt=0, le=128_000)
    effort: GenerationEffort = GenerationEffort.HIGH
    # Adaptive thinking is the only supported on-mode for current models; a fixed
    # thinking-token budget is rejected outright by them.
    adaptive_thinking: Literal[True] = True
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    # Neither Bedrock nor Vertex supports top-level automatic prompt caching, so the
    # adapter places an explicit breakpoint after the frozen instruction block instead.
    cache_system_prompt: bool = True
    aws_region: str | None = Field(default=None, min_length=1, max_length=64)
    gcp_project_id: str | None = Field(default=None, min_length=1, max_length=200)
    gcp_region: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_provider_binding(self) -> GenerationAdapterParameters:
        if self.provider is GenerationProvider.AWS_BEDROCK:
            if self.aws_region is None:
                raise ValueError("a Bedrock generation adapter requires an AWS region")
            if self.gcp_project_id or self.gcp_region:
                raise ValueError("a Bedrock generation adapter cannot pin GCP settings")
        else:
            if not self.gcp_project_id or not self.gcp_region:
                raise ValueError(
                    "a Vertex generation adapter requires a GCP project and region"
                )
            if self.aws_region is not None:
                raise ValueError("a Vertex generation adapter cannot pin an AWS region")
        return self

    def qualified_model_id(self) -> str:
        if self.provider is GenerationProvider.AWS_BEDROCK:
            return f"anthropic.{self.model_id}"
        return self.model_id


class ModelClaim(BaseModel):
    """One claim exactly as the model returned it, before grounding is checked."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class ModelConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_type: ConflictType
    summary: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=2, max_length=20)


class ModelAnswer(BaseModel):
    """The complete structured response contract handed to the model.

    ``sufficient_evidence`` is advisory: a false value abstains, but a true value does
    not by itself produce an answer - every claim still has to survive the evidence-ID
    check against the retrieved set.
    """

    model_config = ConfigDict(extra="forbid")

    sufficient_evidence: bool
    claims: list[ModelClaim] = Field(default_factory=list, max_length=50)
    conflicts: list[ModelConflict] = Field(default_factory=list, max_length=20)
    insufficiency_note: str | None = Field(default=None, max_length=2_000)
