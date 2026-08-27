from functools import lru_cache
from pathlib import Path

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
    qdrant_api_key: str | None = None
    qdrant_timeout_seconds: float = 60.0
    qdrant_expected_version: str = "1.15.4"
    safety_policy_version: str = "0.1.0"
    ingestion_api_key: str | None = None
    artifact_store_path: Path = Path("data/local/artifacts")
    ingestion_max_pdf_bytes: int = 50 * 1024 * 1024
    ingestion_request_timeout_seconds: float = 30.0
    ingestion_allow_private_networks: bool = False
    steward_artifact_store_path: Path = Path("data/local/steward-artifacts")
    steward_max_artifact_bytes: int = 100 * 1024 * 1024
    steward_request_timeout_seconds: float = 60.0
    steward_allow_private_networks: bool = False
    steward_fhir_max_entries: int = 10_000
    steward_fhir_max_total_uncompressed_bytes: int = 512 * 1024 * 1024
    steward_fhir_max_member_bytes: int = 32 * 1024 * 1024
    steward_fhir_max_compression_ratio: float = 100.0
    steward_fhir_max_json_depth: int = 100
    corpus_steward_signing_key_path: Path | None = None
    corpus_steward_signing_key_id: str | None = None
    corpus_steward_signer_identity: str | None = None
    # Serving retrieval. Absent SERVING_CANDIDATE_PATH the API runs with no retrieval
    # engine and abstains on every question; it never falls back to an unpinned model.
    serving_candidate_path: Path | None = None
    serving_dense_model_root: Path | None = None
    serving_dense_model_manifest: Path | None = None
    serving_dense_artifact_sha256: str | None = None
    serving_sparse_model_root: Path | None = None
    serving_sparse_model_manifest: Path | None = None
    serving_sparse_artifact_sha256: str | None = None
    serving_reranker_model_root: Path | None = None
    serving_reranker_model_manifest: Path | None = None
    serving_reranker_artifact_sha256: str | None = None
    serving_embedding_device: str | None = None
    serving_reranker_device: str | None = None
    serving_embedding_timeout_seconds: float = 30.0
    serving_embedding_max_attempts: int = 2
    serving_embedding_retry_delay_seconds: float = 0.2
    serving_embedding_max_retry_delay_seconds: float = 2.0
    serving_generation_parameters_path: Path | None = None

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

    @field_validator("ingestion_api_key")
    @classmethod
    def validate_ingestion_api_key(cls, value: str | None) -> str | None:
        if value is not None and len(value) < 24:
            raise ValueError("INGESTION_API_KEY must contain at least 24 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
