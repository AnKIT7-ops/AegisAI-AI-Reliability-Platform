"""Simulated releases: create, canary, promote, roll back, read the history."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_sessionmaker
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.models import Release
from app.services.releases import (
    actor_for,
    create_release,
    promote,
    release_to_dict,
    rollback,
    run_canary,
)

router = APIRouter(prefix="/api/v1/releases", tags=["releases"])
Sessions = Annotated[sessionmaker[Session], Depends(get_sessionmaker)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
Source = Literal["live", "demo"]
Status = Literal[
    "candidate", "canary_passed", "rejected", "active", "superseded", "rolled_back"
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRequest(Strict):
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    evaluation_run_id: uuid.UUID


class CanaryRequest(Strict):
    baseline_release_id: uuid.UUID | None = None


class PromoteRequest(Strict):
    reason: Reason | None = None


class RollbackRequest(Strict):
    target_release_id: uuid.UUID
    reason: Reason


@router.get("")
def list_releases(
    sessions: Sessions,
    source: Source | None = None,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Newest first, without the event history."""
    query = select(Release).order_by(Release.created_at.desc()).limit(limit)
    if source is not None:
        query = query.where(Release.source == source)
    if status is not None:
        query = query.where(Release.status == status)
    with sessions() as session:
        return {"releases": [release_to_dict(r) for r in session.scalars(query)]}


@router.post("", status_code=201)
def create(
    req: CreateRequest, sessions: Sessions, settings: SettingsDep
) -> dict[str, Any]:
    """A candidate from a completed evaluation run."""
    with sessions() as session:
        release = create_release(
            session, req.version, req.evaluation_run_id, actor_for(settings)
        )
        return release_to_dict(release, events=True)


@router.get("/{release_id}")
def read(release_id: uuid.UUID, sessions: Sessions) -> dict[str, Any]:
    with sessions() as session:
        release = session.get(Release, release_id)
        if release is None:
            raise ApiError(404, "not_found", "release not found")
        return release_to_dict(release, events=True)


@router.post("/{release_id}/canary")
def canary(
    release_id: uuid.UUID,
    sessions: Sessions,
    settings: SettingsDep,
    req: CanaryRequest | None = None,
) -> dict[str, Any]:
    """Apply the quality gate and record the comparison with a baseline."""
    baseline = req.baseline_release_id if req else None
    with sessions() as session:
        release = run_canary(
            session, release_id, baseline, settings, actor_for(settings)
        )
        return release_to_dict(release, events=True)


@router.post("/{release_id}/promote")
def promote_release(
    release_id: uuid.UUID,
    sessions: Sessions,
    settings: SettingsDep,
    req: PromoteRequest | None = None,
) -> dict[str, Any]:
    """Record the release as active. The gate is checked again first."""
    reason = req.reason if req else None
    with sessions() as session:
        release = promote(session, release_id, reason, settings, actor_for(settings))
        return release_to_dict(release, events=True)


@router.post("/{release_id}/rollback")
def rollback_release(
    release_id: uuid.UUID,
    req: RollbackRequest,
    sessions: Sessions,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Reactivate a named earlier release. A reason is required."""
    with sessions() as session:
        release = rollback(
            session,
            release_id,
            req.target_release_id,
            req.reason,
            actor_for(settings),
        )
        return release_to_dict(release, events=True)
