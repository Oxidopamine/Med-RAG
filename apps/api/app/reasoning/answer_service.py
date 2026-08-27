"""Fail-closed composition of a grounded answer from retrieved guideline evidence.

The whole safety argument of this lane is that the model's output is treated as a
*proposal*, never as a result. Three rules enforce that, in order:

1. The model is called only when there is retrieved evidence to ground against. No
   evidence means abstention decided here, before any generation happens.
2. Every claim must cite evidence IDs, and every cited ID must appear in the set that
   was actually retrieved for this question. A claim citing anything else is dropped -
   not repaired, not re-asked. This check is deterministic and needs no model.
3. If nothing survives (2), the result is an abstention, even when the model reported
   sufficient evidence. A model handed insufficient context is measurably more likely
   to answer confidently than to abstain, so its own sufficiency signal can lower the
   outcome to an abstention but can never raise one to an answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.reasoning.generation_adapters import GenerationBackend, GenerationUnavailableError
from app.reasoning.generation_schemas import AbstentionReason, ModelAnswer
from app.reasoning.verification import (
    ClaimVerification,
    DeterministicClaimVerifier,
    VerifiableEvidence,
    VerificationStatus,
)
from app.schemas.questions import AbstentionDetail, RenderedClaim, VerificationSummary

SYSTEM_PROMPT = """You answer clinical questions strictly from numbered guideline \
passages supplied with each question.

Rules:
- Use only the supplied passages. Do not use any other knowledge, even if you are \
confident it is correct.
- Every claim must cite the evidence_id of each passage that supports it. Cite only \
evidence_ids that appear in the supplied passages.
- If the passages do not answer the question, set sufficient_evidence to false and \
explain what is missing. Answering from general knowledge is a failure; saying the \
passages do not cover it is a correct outcome.
- When passages disagree, do not resolve the disagreement. Report it as a conflict, \
classify what kind it is, and cite every passage involved.
- Do not restate a passage as a claim unless it bears on the question asked."""

_ABSTENTION_MESSAGES = {
    AbstentionReason.NO_EVIDENCE_RETRIEVED: (
        "No approved guideline evidence matched this question under the active release."
    ),
    AbstentionReason.NO_ACTIVE_RELEASE: (
        "No corpus release is currently active, so no evidence can be cited."
    ),
    AbstentionReason.MODEL_DECLARED_INSUFFICIENT: (
        "The retrieved guideline passages do not answer this question."
    ),
    AbstentionReason.NO_CLAIM_SURVIVED_GROUNDING: (
        "No proposed claim was fully supported by the retrieved guideline passages."
    ),
    AbstentionReason.NO_CLAIM_SURVIVED_VERIFICATION: (
        "No proposed claim could be verified against the passages it cited."
    ),
    AbstentionReason.GENERATION_UNAVAILABLE: (
        "The answer service could not produce a verifiable answer for this question."
    ),
}


@dataclass(frozen=True)
class RetrievedPassage:
    """One retrieved evidence record as the generation lane sees it.

    The provenance fields carry what the deterministic validators need to decide
    whether this record may stand behind a rendered claim. They default to the state a
    record must already be in to have been retrieved from an active release at all;
    a caller that knows otherwise is expected to say so, and ``render_allowed=False``
    is the case that turns a content check into ``UNRESOLVED`` rather than a pass.
    """

    evidence_id: str
    text: str
    evidence_roles: tuple[str, ...] = ()
    source_title: str | None = None
    source_version_label: str | None = None
    render_allowed: bool = True
    approval_status: str = "APPROVED"
    lifecycle_status: str = "ACTIVE"
    locator_count: int = 1

    def as_verifiable(self) -> VerifiableEvidence:
        return VerifiableEvidence(
            evidence_id=self.evidence_id,
            exact_text=self.text if self.render_allowed else None,
            render_allowed=self.render_allowed,
            approval_status=self.approval_status,
            lifecycle_status=self.lifecycle_status,
            locator_count=self.locator_count,
        )


@dataclass(frozen=True)
class WithheldClaim:
    """A claim that was proposed and not rendered, with the reason it was not.

    Withheld claims never reach the reader. They are kept because "the model proposed
    six claims and two were withheld for a unit mismatch" is the signal that tells an
    operator whether the lane is working; a bare count cannot distinguish a careful
    model from a broken validator.
    """

    text: str
    evidence_ids: tuple[str, ...]
    status: str
    reason: str


@dataclass(frozen=True)
class ComposedAnswer:
    claims: tuple[RenderedClaim, ...]
    conflicts: tuple[dict[str, str], ...]
    verification: VerificationSummary
    abstention: AbstentionDetail | None
    withheld: tuple[WithheldClaim, ...] = ()

    @property
    def answered(self) -> bool:
        return self.abstention is None


def render_evidence_block(passages: tuple[RetrievedPassage, ...]) -> str:
    """Render retrieved passages as the only content the model may draw on."""

    lines: list[str] = []
    for index, passage in enumerate(passages, start=1):
        header = f"[{index}] evidence_id: {passage.evidence_id}"
        if passage.source_title:
            header += f" | source: {passage.source_title}"
        if passage.source_version_label:
            header += f" ({passage.source_version_label})"
        if passage.evidence_roles:
            header += f" | roles: {', '.join(passage.evidence_roles)}"
        lines.append(header)
        lines.append(passage.text.strip())
        lines.append("")
    return "\n".join(lines).strip()


def _abstain(
    reason: AbstentionReason,
    *,
    message: str | None = None,
    closest_evidence_ids: tuple[str, ...] = (),
    rendered: int = 0,
    withheld: int = 0,
    withheld_claims: tuple[WithheldClaim, ...] = (),
) -> ComposedAnswer:
    return ComposedAnswer(
        claims=(),
        conflicts=(),
        verification=VerificationSummary(
            rendered_claims=rendered, supported_claims=0, withheld_claims=withheld
        ),
        abstention=AbstentionDetail(
            reason_code=reason.value,
            message=message or _ABSTENTION_MESSAGES[reason],
            closest_evidence_ids=list(closest_evidence_ids),
        ),
        withheld=withheld_claims,
    )


class GroundedAnswerComposer:
    """Turn retrieved evidence into claims that are grounded by construction."""

    def __init__(
        self,
        backend: GenerationBackend,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        verifier: DeterministicClaimVerifier | None = None,
    ) -> None:
        self._backend = backend
        self._system_prompt = system_prompt
        self._verifier = verifier or DeterministicClaimVerifier()

    async def compose(
        self,
        question: str,
        passages: tuple[RetrievedPassage, ...],
        *,
        release_active: bool = True,
    ) -> ComposedAnswer:
        if not release_active:
            return _abstain(AbstentionReason.NO_ACTIVE_RELEASE)
        if not passages:
            # Decided without a model call: there is nothing to ground against.
            return _abstain(AbstentionReason.NO_EVIDENCE_RETRIEVED)

        user_content = (
            f"Question:\n{question.strip()}\n\n"
            f"Guideline passages:\n{render_evidence_block(passages)}"
        )
        try:
            answer = await self._backend.generate(
                system_prompt=self._system_prompt, user_content=user_content
            )
        except GenerationUnavailableError as error:
            unavailable = _ABSTENTION_MESSAGES[AbstentionReason.GENERATION_UNAVAILABLE]
            return _abstain(
                AbstentionReason.GENERATION_UNAVAILABLE,
                message=f"{unavailable} ({error})",
                closest_evidence_ids=tuple(item.evidence_id for item in passages[:5]),
            )
        return self._ground(answer, passages)

    def _ground(
        self, answer: ModelAnswer, passages: tuple[RetrievedPassage, ...]
    ) -> ComposedAnswer:
        retrieved_ids = {item.evidence_id for item in passages}
        closest = tuple(item.evidence_id for item in passages[:5])
        verifiable: dict[str, VerifiableEvidence] = {
            item.evidence_id: item.as_verifiable() for item in passages
        }

        if not answer.sufficient_evidence:
            return _abstain(
                AbstentionReason.MODEL_DECLARED_INSUFFICIENT,
                message=answer.insufficiency_note
                or _ABSTENTION_MESSAGES[AbstentionReason.MODEL_DECLARED_INSUFFICIENT],
                closest_evidence_ids=closest,
            )

        supported: list[RenderedClaim] = []
        withheld_claims: list[WithheldClaim] = []
        ungrounded = 0
        for index, claim in enumerate(answer.claims, start=1):
            cited = list(dict.fromkeys(claim.evidence_ids))
            if not cited or not set(cited).issubset(retrieved_ids):
                # A claim citing evidence that was never retrieved is discarded whole.
                # Partial repair would keep the sentence while dropping the support it
                # was written to rest on, which is the failure this check exists for.
                ungrounded += 1
                withheld_claims.append(
                    WithheldClaim(
                        text=claim.text,
                        evidence_ids=tuple(cited),
                        status=VerificationStatus.UNSUPPORTED.value,
                        reason="cites evidence that was not retrieved for this question",
                    )
                )
                continue

            # Citation grounding proves the claim points at retrieved evidence. It says
            # nothing about whether the evidence contains what the claim asserts, which
            # is what the deterministic validators decide next.
            verification: ClaimVerification = self._verifier.verify(
                claim.text, cited, verifiable
            )
            if verification.status is not VerificationStatus.SUPPORTED:
                withheld_claims.append(
                    WithheldClaim(
                        text=claim.text,
                        evidence_ids=tuple(cited),
                        status=verification.status.value,
                        reason=verification.summary(),
                    )
                )
                continue

            supported.append(
                RenderedClaim(
                    claim_id=f"CL_{index:03d}",
                    text=claim.text,
                    evidence_ids=cited,
                    verification_status=verification.status.value,
                )
            )

        rendered = len(answer.claims)
        withheld = len(withheld_claims)
        if not supported:
            # Which abstention this is depends on why nothing survived. A claim withheld
            # beyond the ungrounded ones reached the validators and failed there, so the
            # defect is content rather than citation. Proposing no claims at all, or
            # only unciteable ones, stays a grounding outcome.
            reason = (
                AbstentionReason.NO_CLAIM_SURVIVED_VERIFICATION
                if withheld > ungrounded
                else AbstentionReason.NO_CLAIM_SURVIVED_GROUNDING
            )
            return _abstain(
                reason,
                closest_evidence_ids=closest,
                rendered=rendered,
                withheld=withheld,
                withheld_claims=tuple(withheld_claims),
            )

        conflicts = tuple(
            {
                "conflict_type": conflict.conflict_type.value,
                "summary": conflict.summary,
                "evidence_ids": ", ".join(
                    item for item in conflict.evidence_ids if item in retrieved_ids
                ),
            }
            for conflict in answer.conflicts
            if set(conflict.evidence_ids) & retrieved_ids
        )
        return ComposedAnswer(
            claims=tuple(supported),
            conflicts=conflicts,
            verification=VerificationSummary(
                rendered_claims=rendered,
                supported_claims=len(supported),
                withheld_claims=withheld,
            ),
            abstention=None,
            withheld=tuple(withheld_claims),
        )
