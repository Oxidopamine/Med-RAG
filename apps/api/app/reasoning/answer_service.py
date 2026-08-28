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

from app.reasoning.ablation import PRODUCTION, AblationProfile
from app.reasoning.generation_adapters import GenerationBackend, GenerationUnavailableError
from app.reasoning.generation_schemas import AbstentionReason, ModelAnswer
from app.schemas.questions import (
    AbstentionDetail,
    GuidelineConflict,
    RenderedClaim,
    RetrievalCandidate,
    VerificationSummary,
)

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
    AbstentionReason.GENERATION_UNAVAILABLE: (
        "The answer service could not produce a verifiable answer for this question."
    ),
}


@dataclass(frozen=True)
class RetrievedPassage:
    """One retrieved evidence record as the generation lane sees it."""

    evidence_id: str
    text: str
    evidence_roles: tuple[str, ...] = ()
    source_title: str | None = None
    source_version_label: str | None = None


@dataclass(frozen=True)
class ComposedAnswer:
    claims: tuple[RenderedClaim, ...]
    conflicts: tuple[GuidelineConflict, ...]
    verification: VerificationSummary
    abstention: AbstentionDetail | None
    # Retrieved passages no surviving claim cited, in retrieval order. Carried so a
    # reader can see what the ranking offered below the cited support; never carried on
    # an abstention, where nothing retrieved has earned display.
    candidates: tuple[RetrievalCandidate, ...] = ()

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
    )


class GroundedAnswerComposer:
    """Turn retrieved evidence into claims that are grounded by construction."""

    def __init__(
        self,
        backend: GenerationBackend,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        ablation: AblationProfile = PRODUCTION,
    ) -> None:
        self._backend = backend
        self._system_prompt = system_prompt
        # PRODUCTION by default: the grounding rules below are the ones D7 states, and a
        # caller has to ask explicitly to have them relaxed.
        self._ablation = ablation

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

        if not answer.sufficient_evidence and self._ablation.enforce_model_sufficiency:
            return _abstain(
                AbstentionReason.MODEL_DECLARED_INSUFFICIENT,
                message=answer.insufficiency_note
                or _ABSTENTION_MESSAGES[AbstentionReason.MODEL_DECLARED_INSUFFICIENT],
                closest_evidence_ids=closest,
            )

        supported: list[RenderedClaim] = []
        withheld = 0
        for index, claim in enumerate(answer.claims, start=1):
            cited = list(dict.fromkeys(claim.evidence_ids))
            ungrounded = not cited or not set(cited).issubset(retrieved_ids)
            if ungrounded and self._ablation.discard_ungrounded_claims:
                # A claim citing evidence that was never retrieved is discarded whole.
                # Partial repair would keep the sentence while dropping the support it
                # was written to rest on, which is the failure this check exists for.
                withheld += 1
                continue
            supported.append(
                RenderedClaim(
                    claim_id=f"CL_{index:03d}",
                    text=claim.text,
                    evidence_ids=cited,
                    # An ablated run renders the claim but must not label it SUPPORTED:
                    # nothing checked it. The status is what a reader classifying the run
                    # sees, so it states which of the two actually happened.
                    verification_status="UNVERIFIED" if ungrounded else "SUPPORTED",
                )
            )

        rendered = len(answer.claims)
        if not supported:
            return _abstain(
                AbstentionReason.NO_CLAIM_SURVIVED_GROUNDING,
                closest_evidence_ids=closest,
                rendered=rendered,
                withheld=withheld,
            )

        conflicts = tuple(
            GuidelineConflict(
                conflict_type=conflict.conflict_type.value,
                summary=conflict.summary,
                # Only passages this run actually retrieved. A conflict that points at a
                # record the reader cannot open is a claim about evidence they cannot check.
                #
                # Deduplicated for the same reason the claim path above is: the model can
                # repeat an identifier, the contract forbids it, and a validation error
                # here would discard a fully grounded answer as a pipeline failure.
                evidence_ids=list(
                    dict.fromkeys(
                        item for item in conflict.evidence_ids if item in retrieved_ids
                    )
                ),
            )
            for conflict in answer.conflicts
            if set(conflict.evidence_ids) & retrieved_ids
        )
        cited = {evidence_id for claim in supported for evidence_id in claim.evidence_ids}
        candidates = tuple(
            RetrievalCandidate(evidence_id=passage.evidence_id, retrieval_rank=rank)
            for rank, passage in enumerate(passages, start=1)
            if passage.evidence_id not in cited
        )
        return ComposedAnswer(
            claims=tuple(supported),
            conflicts=conflicts,
            verification=VerificationSummary(
                rendered_claims=rendered,
                # Only claims whose citations were actually checked. On the production
                # path every rendered claim is grounded, so this is `len(supported)`; on an
                # ablated run the list also holds UNVERIFIED claims, and counting those as
                # supported would report unchecked claims as verified in the one field a
                # reader classifying the run relies on.
                supported_claims=sum(
                    1 for claim in supported if claim.verification_status == "SUPPORTED"
                ),
                withheld_claims=withheld,
            ),
            abstention=None,
            candidates=candidates,
        )
