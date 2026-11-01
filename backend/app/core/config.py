"""Application settings, read from AEGIS_* environment variables."""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.auth import check_hash


def _is_loopback(bind: str) -> bool:
    """Anything unrecognised counts as exposed, so the check fails closed."""
    host = bind.strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    # Inputs stay out of startup errors: one of them is the password hash.
    model_config = SettingsConfigDict(env_prefix="AEGIS_", hide_input_in_errors=True)

    # Model provider. "fake" needs no model and is used by tests and CI.
    llm_provider: Literal["ollama", "fake"] = "ollama"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3.5:4b"
    embed_model: str = "nomic-embed-text"
    # nomic-embed-text expects task prefixes; set both to "" for models that don't.
    embed_document_prefix: str = "search_document: "
    embed_query_prefix: str = "search_query: "
    connect_timeout_s: float = Field(default=3.0, gt=0)
    generate_timeout_s: float = Field(default=120.0, gt=0)
    # Fault injection for monitoring drills (docs/runbooks). Off at 0: a delay
    # before every Ollama call, and the share of calls that fail as "injected".
    fault_delay_ms: int = Field(default=0, ge=0, le=60_000)
    fault_error_rate: float = Field(default=0.0, ge=0, le=1)

    # Ingestion and retrieval.
    index_dir: Path = Path("data/index")
    max_upload_bytes: int = Field(default=1_000_000, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=4, ge=1, le=10)
    # Below this cosine score a chunk counts as irrelevant; calibrated for
    # nomic-embed-text on the sample corpus (in-scope >= 0.75, off-topic <= 0.65).
    min_score: float = Field(default=0.70, ge=-1, le=1)

    # Evaluation. The dataset directory defaults to the repo's data/evaluation;
    # run artifacts (JSON, CSV, Markdown) are written under eval_runs_dir/<run id>/.
    eval_dir: Path = Path(__file__).resolve().parents[3] / "data" / "evaluation"
    eval_runs_dir: Path = Path("data/runs")

    # Incidents. prometheus_url is what the backend queries; the public URL goes
    # into report links, so it must open in a browser. Scenarios are fixtures.
    prometheus_url: str = "http://127.0.0.1:9090"
    prometheus_public_url: str = "http://127.0.0.1:9090"
    grafana_public_url: str = "http://127.0.0.1:3000"
    incident_dir: Path = Path(__file__).resolve().parents[3] / "data" / "incidents"
    demo_dir: Path = Path(__file__).resolve().parents[3] / "data" / "demo"

    # Request guards. Host stops DNS rebinding; Origin stops other sites' pages
    # from sending state-changing requests. Lists are JSON in the environment.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "backend"]
    allowed_origins: list[str] = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Authentication. An empty hash turns it off, for localhost use. The session
    # hours are an absolute limit. frontend_bind is the address nginx publishes
    # on, passed in by Compose so the check below can see it.
    auth_password_hash: str = Field(default="", repr=False)
    auth_cookie_secure: bool = False
    auth_session_hours: int = Field(default=8, gt=0)
    frontend_bind: str = "127.0.0.1"

    @model_validator(mode="after")
    def _overlap_below_size(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self

    @model_validator(mode="after")
    def _exposure_needs_authentication(self) -> "Settings":
        if self.auth_password_hash:
            check_hash(self.auth_password_hash)
        exposed = not _is_loopback(self.frontend_bind)
        if exposed and not (self.auth_password_hash and self.auth_cookie_secure):
            raise ValueError(
                "a non-loopback FRONTEND_BIND needs AEGIS_AUTH_PASSWORD_HASH "
                "and AEGIS_AUTH_COOKIE_SECURE=true"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
