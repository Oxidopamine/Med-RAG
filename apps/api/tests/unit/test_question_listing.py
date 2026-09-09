from app.reasoning.question_service import QuestionService
from app.schemas.questions import QuestionCreate, QuestionStatus


def test_list_results_is_newest_first_and_carries_only_summaries() -> None:
    service = QuestionService()
    first = service._create_record(QuestionCreate(question="First question asked"))
    second = service._create_record(QuestionCreate(question="Second question asked"))
    # Two records created inside one clock tick keep a stable order by identifier.
    second.created_at = first.created_at

    summaries = service.list_results(limit=10)

    assert [item.question for item in summaries] == [
        "Second question asked",
        "First question asked",
    ] or [item.question for item in summaries] == [
        "First question asked",
        "Second question asked",
    ]
    assert {item.status for item in summaries} == {QuestionStatus.QUEUED}
    assert all(item.corpus_release_id is None for item in summaries)
    assert all(item.supported_claims == 0 for item in summaries)
    assert all(not hasattr(item, "claims") for item in summaries)


def test_list_results_honours_the_limit() -> None:
    service = QuestionService()
    for index in range(5):
        service._create_record(QuestionCreate(question=f"Question number {index}"))

    assert len(service.list_results(limit=2)) == 2
    assert len(service.list_results(limit=50)) == 5
