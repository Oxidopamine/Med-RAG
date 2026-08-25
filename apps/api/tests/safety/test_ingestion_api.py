from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


async def test_ingestion_api_fails_closed_without_configured_key(monkeypatch) -> None:
    monkeypatch.delenv("INGESTION_API_KEY", raising=False)
    get_settings.cache_clear()
    app = create_app()

    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/ingestion/quarantine")

    assert response.status_code == 503
    get_settings.cache_clear()


async def test_ingestion_api_rejects_missing_or_incorrect_credentials(monkeypatch) -> None:
    monkeypatch.setenv("INGESTION_API_KEY", "synthetic-test-key-at-least-24-chars")
    get_settings.cache_clear()
    app = create_app()

    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/v1/ingestion/quarantine")
        incorrect = await client.get(
            "/v1/ingestion/quarantine",
            headers={"X-Ingestion-Key": "wrong-key"},
        )

    assert missing.status_code == 401
    assert incorrect.status_code == 401
    get_settings.cache_clear()
