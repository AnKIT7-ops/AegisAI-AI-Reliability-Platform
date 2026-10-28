"""Application settings, read from AEGIS_* environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_")

    # Model provider. "fake" needs no model and is used by tests and CI.
    llm_provider: Literal["ollama", "fake"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3.5:4b"
    embed_model: str = "nomic-embed-text"
    # nomic-embed-text expects task prefixes; set both to "" for models that don't.
    embed_document_prefix: str = "search_document: "
    embed_query_prefix: str = "search_query: "
    connect_timeout_s: float = Field(default=3.0, gt=0)
    generate_timeout_s: float = Field(default=120.0, gt=0)

    # Ingestion and retrieval.
    index_dir: Path = Path("data/index")
    max_upload_bytes: int = Field(default=1_000_000, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=4, ge=1, le=10)
    # Below this cosine score a chunk counts as irrelevant; calibrated for
    # nomic-embed-text on the sample corpus (in-scope >= 0.75, off-topic <= 0.65).
    min_score: float = Field(default=0.70, ge=-1, le=1)

    @model_validator(mode="after")
    def _overlap_below_size(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
