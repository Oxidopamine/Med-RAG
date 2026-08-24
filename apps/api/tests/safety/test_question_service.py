import asyncio

from app.reasoning.question_service import QuestionService
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

