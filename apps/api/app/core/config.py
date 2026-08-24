from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Guideline Evidence QA"
    environment: str = "development"
    api_prefix: str = "/v1"
    api_cors_origins: list[str] = ["http://localhost:3000"]
    api_cors_origin_regex: str | None = r"^https?://(localhost|127\.0\.0\.1):[0-9]+$"
    database_url: str = "postgresql+asyncpg://medrag:medrag@localhost:5432/medrag"
    qdrant_url: str = "http://localhost:6333"
    safety_policy_version: str = "0.1.0"
    approved_corpus_available: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
