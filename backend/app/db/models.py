"""Evaluation runs and their per-example results."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

RUN_STATUSES = ("pending", "running", "done", "failed")
CATEGORIES = ("answerable", "unanswerable", "injection")


class Base(DeclarativeBase):
    # Stable constraint names, so migrations can drop or alter them later.
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
        }
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (CheckConstraint(_in("status", RUN_STATUSES), name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    label: Mapped[str] = mapped_column(String(64))
    dataset_version: Mapped[str] = mapped_column(String(32))
    dataset_checksum: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str] = mapped_column(String(32))
    chat_model: Mapped[str] = mapped_column(String(128))
    embed_model: Mapped[str] = mapped_column(String(128))
    top_k: Mapped[int]
    min_score: Mapped[float]
    # Full settings snapshot, so a run can be repeated exactly.
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Directory holding this run's JSON, CSV and Markdown report files.
    artifact_path: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        # Set in Python too: SQLite's CURRENT_TIMESTAMP has only second precision.
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["EvaluationResult"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvaluationResult.id",
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "example_id"),
        CheckConstraint(_in("category", CATEGORIES), name="category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE")
    )
    example_id: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    retrieved: Mapped[list[str]] = mapped_column(JSON)  # filenames, best first
    sources: Mapped[list[str]] = mapped_column(JSON)  # filenames; index 0 is [1]
    cited: Mapped[list[int]] = mapped_column(JSON)  # source numbers in the answer
    scores: Mapped[dict[str, Any]] = mapped_column(JSON)  # per-metric outcome
    latency_ms: Mapped[int | None]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped[EvaluationRun] = relationship(back_populates="results")
