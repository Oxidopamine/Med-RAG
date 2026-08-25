import pytest
from pydantic import ValidationError

from app.schemas.corpus import ActiveCorpusRelease
from app.schemas.domain import utc_now
from app.schemas.questions import (
    EvidenceDetail,
    EvidenceLocator,
    QuestionResult,
    QuestionStatus,
    RenderedClaim,
)


def evidence_detail(evidence_id: str = "EV_001") -> EvidenceDetail:
    return EvidenceDetail(
        evidence_id=evidence_id,
        exact_text="Exact source text.",
        evidence_roles=["PRIMARY_SUPPORT"],
        source_id="SRC_001",
        source_version_id="SV_001",
        source_title="Synthetic Guideline",
        source_version_label="2026",
        publisher_name="Synthetic Publisher",
        source_url="https://fixtures.invalid/guideline",
        source_class="E1",
        jurisdiction="TEST",
        language="en",
        lifecycle_status="EFFECTIVE",
        render_allowed=True,
        locators=[
            EvidenceLocator(
                kind="PDF",
                source_uri="https://fixtures.invalid/guideline.pdf",
                pdf_page=7,
                bbox=(10, 20, 30, 40),
                exact_highlight_available=True,
            )
        ],
    )


def answer_result(**overrides) -> QuestionResult:
    now = utc_now()
    values = {
        "question_id": "Q_001",
        "question": "What does the guideline recommend?",
        "status": QuestionStatus.ANSWER_READY,
        "corpus_release": ActiveCorpusRelease(
            corpus_release_id="CR_001",
            manifest_sha256="a" * 64,
            qdrant_collection="corpus_CR_001",
            activated_at=now,
        ),
        "claims": [
            RenderedClaim(
                claim_id="C_001",
                text="A source-supported claim.",
                evidence_ids=["EV_001"],
                verification_status="VERIFIED",
            )
        ],
        "evidence_details": [evidence_detail()],
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return QuestionResult.model_validate(values)


def test_answer_ready_requires_exact_canonical_evidence_coverage() -> None:
    result = answer_result()

    assert result.evidence_details[0].evidence_id == "EV_001"

    with pytest.raises(ValidationError, match="canonical details for every evidence ID"):
        answer_result(evidence_details=[])


def test_evidence_details_cannot_be_added_to_an_unrelated_claim() -> None:
    with pytest.raises(ValidationError, match="referenced by a rendered claim"):
        answer_result(evidence_details=[evidence_detail("EV_UNRELATED")])


def test_answer_ready_requires_an_active_corpus_release() -> None:
    with pytest.raises(ValidationError, match="active corpus release"):
        answer_result(corpus_release=None)


def test_restricted_evidence_cannot_expose_text_or_highlights() -> None:
    with pytest.raises(ValidationError, match="restricted evidence cannot expose exact text"):
        EvidenceDetail(
            **{
                **evidence_detail().model_dump(),
                "render_allowed": False,
            }
        )
