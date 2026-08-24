from fastapi import Request

from app.reasoning.question_service import QuestionService


def get_question_service(request: Request) -> QuestionService:
    return request.app.state.question_service

