import pytest
from pydantic import ValidationError

from app.schemas.corpus import ActiveCorpusRelease
from app.schemas.domain import utc_now
from app.schemas.questions import (
    AbstentionDetail,
    EvidenceDetail,
    EvidenceLocator,
    QuestionResult,
    QuestionStatus,
    RenderedClaim,
    RetrievalCandidate,
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


def test_answer_ready_may_expose_uncited_ranked_candidates() -> None:
    result = answer_result(
        evidence_details=[evidence_detail(), evidence_detail("EV_004")],
        retrieval_candidates=[RetrievalCandidate(evidence_id="EV_004", retrieval_rank=4)],
    )

    assert [candidate.evidence_id for candidate in result.retrieval_candidates] == ["EV_004"]
    # The rank is the position the passage held in retrieval, not its position in this
    # list, so a reader can tell how far below the cited support it sat.
    assert result.retrieval_candidates[0].retrieval_rank == 4


def test_a_ranked_candidate_needs_canonical_evidence_details() -> None:
    with pytest.raises(ValidationError, match="canonical details for every evidence ID"):
        answer_result(
            retrieval_candidates=[RetrievalCandidate(evidence_id="EV_004", retrieval_rank=4)]
        )


def test_a_cited_evidence_id_cannot_be_replayed_as_an_uncited_candidate() -> None:
    with pytest.raises(ValidationError, match="cannot also be an uncited retrieval candidate"):
        answer_result(
            retrieval_candidates=[RetrievalCandidate(evidence_id="EV_001", retrieval_rank=2)]
        )


def test_ranked_candidates_must_be_listed_in_ascending_unique_rank_order() -> None:
    with pytest.raises(ValidationError, match="ascending, unique rank order"):
        answer_result(
            evidence_details=[
                evidence_detail(),
                evidence_detail("EV_004"),
                evidence_detail("EV_005"),
            ],
            retrieval_candidates=[
                RetrievalCandidate(evidence_id="EV_005", retrieval_rank=5),
                RetrievalCandidate(evidence_id="EV_004", retrieval_rank=4),
            ],
        )


def test_an_abstained_result_cannot_expose_ranked_candidates() -> None:
    # Abstention means nothing retrieved earned display, so the candidate door stays shut.
    with pytest.raises(ValidationError, match="only an answer-ready result"):
        answer_result(
            status=QuestionStatus.ABSTAINED,
            claims=[],
            evidence_details=[],
            abstention=AbstentionDetail(
                reason_code="NO_CLAIM_SURVIVED_GROUNDING",
                message="No proposed claim was supported.",
            ),
            retrieval_candidates=[RetrievalCandidate(evidence_id="EV_004", retrieval_rank=4)],
        )


def test_table_cell_locator_carries_its_address() -> None:
    locator = EvidenceLocator(
        kind="TABLE_CELL",
        source_uri="https://fixtures.invalid/annex.xlsx",
        table_id="Annex2Dosing",
        row_index=3,
        column_index=2,
    )

    assert locator.table_id == "Annex2Dosing"
    assert locator.row_index == 3
    assert locator.column_index == 2


@pytest.mark.parametrize(
    "cell",
    [
        {"table_id": "Annex2Dosing"},
        {"table_id": "Annex2Dosing", "row_index": 3},
        {"row_index": 3, "column_index": 2},
        {"column_index": 2},
    ],
)
def test_partial_cell_address_is_rejected(cell: dict[str, object]) -> None:
    # A half-supplied address would render as a cell reference with a fabricated row or
    # column. Reporting the address as absent is the safer failure.
    with pytest.raises(ValidationError, match="table-cell address requires"):
        EvidenceLocator(
            kind="TABLE_CELL",
            source_uri="https://fixtures.invalid/annex.xlsx",
            **cell,
        )


def test_locator_without_a_cell_address_is_unaffected() -> None:
    locator = EvidenceLocator(
        kind="PDF",
        source_uri="https://fixtures.invalid/guideline.pdf",
        pdf_page=7,
    )

    assert locator.table_id is None
    assert locator.row_index is None
    assert locator.column_index is None
