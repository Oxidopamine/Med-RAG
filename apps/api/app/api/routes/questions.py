from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_question_service
from app.reasoning.question_service import QuestionNotFoundError, QuestionService
from app.schemas.questions import (
    QuestionAccepted,
    QuestionContextPatch,
    QuestionCreate,
    QuestionResult,
)

router = APIRouter(prefix="/questions", tags=["questions"])
QuestionServiceDependency = Annotated[QuestionService, Depends(get_question_service)]


@router.post("", response_model=QuestionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def submit_question(
    payload: QuestionCreate,
    service: QuestionServiceDependency,
) -> QuestionAccepted:
    return await service.submit(payload)


@router.get("/{question_id}", response_model=QuestionResult)
async def get_question(
    question_id: str,
    service: QuestionServiceDependency,
) -> QuestionResult:
    try:
        return service.result(question_id)
    except QuestionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Question not found") from error


@router.patch("/{question_id}/context", response_model=QuestionAccepted)
async def replace_question_context(
    question_id: str,
    payload: QuestionContextPatch,
    service: QuestionServiceDependency,
) -> QuestionAccepted:
    try:
        return await service.replace_context(question_id, payload.context)
    except QuestionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Question not found") from error


@router.get("/{question_id}/events")
async def question_events(
    question_id: str,
    service: QuestionServiceDependency,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        service.result(question_id)
    except QuestionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Question not found") from error

    async def stream():
        async for event in service.events(question_id, after=after):
            # A keep-alive is sent as an SSE comment, which the EventSource parser is
            # required to ignore: it carries no status and must never be mistaken for one.
            # What it does is put bytes on a connection that would otherwise be silent for
            # the whole of a slow phase, so a dropped connection actually fails a write and
            # surfaces to the client as an error instead of hanging open forever.
            if event is None:
                yield ": keep-alive\n\n"
                continue
            yield (
                f"id: {event.sequence}\n"
                "event: progress\n"
                f"data: {event.model_dump_json()}\n\n"
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

