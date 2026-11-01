"""Evaluation runs, their per-example results, incidents and releases."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

RUN_STATUSES = ("pending", "running", "done", "failed")
CATEGORIES = ("answerable", "unanswerable", "injection")
INCIDENT_STATUSES = ("open", "resolved")
INCIDENT_SOURCES = ("live", "simulated")
# Demo records never mix with live ones: see app/services/releases.py.
DATA_SOURCES = ("live", "demo")
RELEASE_STATUSES = (
    "candidate",
    "canary_passed",
    "rejected",
    "active",
    "superseded",
    "rolled_back",
)
# IS TRUE, never = true: a CHECK passes on NULL, so "= true" would let a release
# with no gate result count as a success.
PROMOTION_RULE = (
    "(status = 'candidate' AND gate_passed IS NULL)"
    " OR (status = 'rejected' AND gate_passed IS FALSE)"
    " OR (status IN ('canary_passed', 'active', 'superseded', 'rolled_back')"
    " AND gate_passed IS TRUE)"
)


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
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        CheckConstraint(_in("source", DATA_SOURCES), name="source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    source: Mapped[str] = mapped_column(
        String(16), default="live", server_default="live"
    )
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


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(_in("status", INCIDENT_STATUSES), name="status"),
        CheckConstraint(_in("source", INCIDENT_SOURCES), name="source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    source: Mapped[str] = mapped_column(String(16))
    # Symptom, facts, hypotheses, uncertainty, runbook, suggested actions.
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurrences: Mapped[int] = mapped_column(default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Release(Base):
    """A simulated release: a frozen copy of one evaluation run and its verdict."""

    __tablename__ = "releases"
    __table_args__ = (
        CheckConstraint(_in("status", RELEASE_STATUSES), name="status"),
        CheckConstraint(_in("source", DATA_SOURCES), name="source"),
        CheckConstraint(PROMOTION_RULE, name="promotion_rule"),
        UniqueConstraint("source", "version"),
        # Per source, so demo history can never collide with a real active release.
        Index(
            "uq_releases_one_active",
            "source",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT")
    )
    # Copied from the run. PostgreSQL triggers refuse any change to these.
    dataset_version: Mapped[str] = mapped_column(String(32))
    dataset_checksum: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str] = mapped_column(String(32))
    chat_model: Mapped[str] = mapped_column(String(128))
    embed_model: Mapped[str] = mapped_column(String(128))
    top_k: Mapped[int]
    min_score: Mapped[float]
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Written once, by the canary evaluation. Null until then.
    gate_passed: Mapped[bool | None]
    gate_failures: Mapped[list[str]] = mapped_column(JSON, default=list)
    baseline_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id", ondelete="RESTRICT")
    )
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )

    events: Mapped[list["ReleaseEvent"]] = relationship(
        back_populates="release", order_by="ReleaseEvent.id"
    )


class ReleaseEvent(Base):
    """Append-only audit history. PostgreSQL triggers refuse UPDATE and DELETE."""

    __tablename__ = "release_events"

    # Integer on SQLite, which only auto-increments that exact type.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("releases.id", ondelete="RESTRICT")
    )
    event: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    # How the action was authorized. One shared password names no person.
    actor: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )

    release: Mapped[Release] = relationship(back_populates="events")
