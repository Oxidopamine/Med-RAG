import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def test_question_endpoint_reaches_fail_closed_abstention() -> None:
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "What does the synthetic guideline say?"},
        )

        assert response.status_code == 202
        question_id = response.json()["question_id"]

        for _ in range(30):
            result_response = await client.get(f"/v1/questions/{question_id}")
            assert result_response.status_code == 200
            result = result_response.json()
            if result["status"] == "ABSTAINED":
                break
            await asyncio.sleep(0.05)

        assert result["status"] == "ABSTAINED"
        assert result["claims"] == []
        assert result["corpus_release"] is None
        assert result["abstention"]["reason_code"] == "NO_APPROVED_CORPUS"


async def test_question_endpoint_rejects_unknown_contract_fields() -> None:
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/questions",
            json={
                "question": "What does the synthetic guideline say?",
                "untrusted_override": True,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


async def test_context_endpoint_rejects_contradictory_present_and_absent_facts() -> None:
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        accepted = (
            await client.post(
                "/v1/questions",
                json={"question": "What does the synthetic guideline say?"},
            )
        ).json()

        response = await client.patch(
            f"/v1/questions/{accepted['question_id']}/context",
            json={
                "context": {
                    "conditions": ["ATRIAL_FIBRILLATION"],
                    "known_absent_conditions": ["ATRIAL_FIBRILLATION"],
                }
            },
        )

    assert response.status_code == 422
    assert "both present and absent" in response.text
