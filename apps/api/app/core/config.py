from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Sentinel RAG"
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

    # Research serving path. Off by default, and off means the API keeps its fail-closed
    # behaviour of abstaining with RETRIEVAL_PIPELINE_NOT_CONFIGURED. Enabling it serves
    # a *validated* release without activating it - activation is a signed gate backed by
    # a sealed holdout, and nothing here forges one. Every answer produced this way is
    # labelled RESEARCH_UNACTIVATED in the payload.
    #
    # Serving costs what the roadmap says it costs: the embedding model is loaded
    # in-process at startup, roughly 5.4 GiB resident for Qwen3-0.6B on CPU/fp32.
    serving_enabled: bool = False
    serving_release_bundle_path: Path | None = None
    serving_vectors_path: Path | None = None
    # Overrides the collection named in the release manifest. Required in practice
    # because a release indexed under several vector profiles has one collection per
    # profile (`<collection>--vp-<profile>`), and the manifest names only the first.
    serving_qdrant_collection: str | None = None
    serving_embedding_backend: str = "verified-local"
    serving_dense_model_root: Path | None = None
    serving_dense_model_manifest: Path | None = None
    serving_dense_artifact_sha256: str | None = None
    serving_sparse_model_root: Path | None = None
    serving_sparse_model_manifest: Path | None = None
    serving_sparse_artifact_sha256: str | None = None
    serving_top_k: int = 10
    serving_candidate_limit: int = 100
    serving_rrf_k: int = 60
    # "claude" is the intended production lane; "gemini" is the development comparator
    # that exists while the anthropic-* Vertex quota is ungranted.
    serving_generation_provider: str = "claude"

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
