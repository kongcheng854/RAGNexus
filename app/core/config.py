from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    auth_enabled: bool = True
    langfuse_enabled: bool = False
    langfuse_host: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/rag_center"
    model_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    model_api_key: SecretStr = SecretStr("")
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    vector_store: Literal["pgvector"] = "pgvector"
    document_storage_path: Path = Path("./data/uploads")
    document_max_size_mb: int = Field(default=20, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    table_max_rows_per_chunk: int = Field(default=10, gt=0)
    top_k: int = Field(default=5, gt=0)
    keyword_search_provider: Literal["elasticsearch"] = "elasticsearch"
    elasticsearch_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9200")
    elasticsearch_index: str = Field(default="rag_chunks", pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=255)
    elasticsearch_timeout_seconds: int = Field(default=10, gt=0)
    query_max_length: int = Field(default=2000, gt=0)
    retrieval_mode: Literal["vector", "bm25", "hybrid"] = "vector"
    hybrid_fusion: Literal["rrf"] = "rrf"
    hybrid_rrf_k: int = Field(default=60, gt=0)
    hybrid_vector_top_k: int = Field(default=20, gt=0, le=1000)
    hybrid_bm25_top_k: int = Field(default=20, gt=0, le=1000)
    hybrid_top_n: int = Field(default=20, gt=0, le=1000)
    llm_provider: Literal["openai_compatible"] = "openai_compatible"
    llm_base_url: AnyHttpUrl | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str | None = Field(default=None, min_length=1)
    llm_timeout_seconds: int = Field(default=60, gt=0)
    query_rewrite_enabled: bool = False
    query_rewrite_timeout_ms: int = Field(default=2000, gt=0)
    rerank_enabled: bool = False
    rerank_provider: Literal["llm", "noop"] = "llm"
    rerank_top_n: int = Field(default=5, gt=0)
    rerank_max_candidates: int = Field(default=20, gt=0)
    rerank_chunk_max_chars: int = Field(default=1000, gt=0)
    rerank_temperature: float = Field(default=0.0, ge=0, le=2)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_backup_count: int = Field(default=5, ge=1)
    log_queue_size: int = Field(default=4096, gt=0)
    log_color: bool = True
    log_payloads: bool = False
    log_payload_limit: int = Field(default=2048, ge=128, le=65_536)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self
