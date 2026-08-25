import asyncio

from app.reasoning.question_service import QuestionService
from app.schemas.corpus import ActiveCorpusRelease
from app.schemas.domain import utc_now
from app.schemas.questions import QuestionCreate, QuestionStatus


async def test_no_corpus_question_path_abstains_without_claims() -> None:
    service = QuestionService(approved_corpus_available=False)
    accepted = await service.submit(
        QuestionCreate(
            question=(
                "For a 74-year-old with AF and eGFR 28, what do guidelines say "
                "about anticoagulation?"
            )
        )
    )

    for _ in range(30):
        result = service.result(accepted.question_id)
        if result.status is QuestionStatus.ABSTAINED:
            break
        await asyncio.sleep(0.05)

    assert result.status is QuestionStatus.ABSTAINED
    assert result.claims == []
    assert result.abstention is not None
    assert result.abstention.reason_code == "NO_APPROVED_CORPUS"
    assert result.interpreted_context is not None
    assert result.interpreted_context.age == 74
    await service.close()


async def test_progress_events_contain_states_not_claim_content() -> None:
    service = QuestionService(approved_corpus_available=False)
    accepted = await service.submit(QuestionCreate(question="A synthetic guideline question"))

    events = [event async for event in service.events(accepted.question_id)]

    assert events[0].status is QuestionStatus.QUEUED
    assert events[-1].status is QuestionStatus.ABSTAINED
    assert all(not hasattr(event, "claim") for event in events)
    await service.close()


async def test_active_release_pointer_changes_only_the_fail_closed_reason() -> None:
    async def active_release() -> ActiveCorpusRelease:
        return ActiveCorpusRelease(
            corpus_release_id="CR_TEST",
            manifest_sha256="a" * 64,
            qdrant_collection="corpus_CR_TEST",
            activated_at=utc_now(),
        )

    service = QuestionService(active_release_provider=active_release)
    accepted = await service.submit(QuestionCreate(question="A synthetic guideline question"))

    for _ in range(30):
        result = service.result(accepted.question_id)
        if result.status is QuestionStatus.ABSTAINED:
            break
        await asyncio.sleep(0.05)

    assert result.claims == []
    assert result.abstention is not None
    assert result.abstention.reason_code == "RETRIEVAL_PIPELINE_NOT_CONFIGURED"
    assert result.corpus_release is not None
    assert result.corpus_release.corpus_release_id == "CR_TEST"
    await service.close()


async def test_release_registry_failure_is_treated_as_no_approved_corpus() -> None:
    async def unavailable_registry() -> None:
        raise RuntimeError("database unavailable")

    service = QuestionService(active_release_provider=unavailable_registry)
    accepted = await service.submit(QuestionCreate(question="A synthetic guideline question"))

    for _ in range(30):
        result = service.result(accepted.question_id)
        if result.status is QuestionStatus.ABSTAINED:
            break
        await asyncio.sleep(0.05)

    assert result.claims == []
    assert result.abstention is not None
    assert result.abstention.reason_code == "NO_APPROVED_CORPUS"
    await service.close()
