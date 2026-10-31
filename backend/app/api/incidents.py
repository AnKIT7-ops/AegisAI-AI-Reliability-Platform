"""Incidents: detect them, replay a scenario, read reports, resolve.

Nothing here changes the system: reports suggest actions as text for a person.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_prometheus, get_sessionmaker
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.logs import RECENT_FAILURES
from app.db.models import Incident
from app.services.incidents.diagnosis import (
    Report,
    Signals,
    diagnose,
    markdown_report,
)
from app.services.incidents.signals import Prometheus, collect
from app.services.incidents.store import incident_to_dict, record, unsaved

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
log = logging.getLogger("aegis")
Sessions = Annotated[sessionmaker[Session], Depends(get_sessionmaker)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A file name inside the incident directory, never a path.
    scenario: str = Field(pattern=r"^[a-z_]{1,64}$")


def _get(session: Session, incident_id: uuid.UUID) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ApiError(404, "not_found", "incident not found")
    return incident


@router.post("/detect")
def detect(
    sessions: Sessions,
    settings: SettingsDep,
    prometheus: Annotated[Prometheus, Depends(get_prometheus)],
) -> dict[str, Any]:
    """One detection pass. Reports work without a database, but are not saved."""
    now = datetime.now(UTC)
    signals, not_checked = collect(
        prometheus, sessions, settings, RECENT_FAILURES.snapshot(), now
    )
    reports = diagnose(signals, settings.prometheus_public_url)
    incidents = [unsaved(r, now, "live") for r in reports]
    persisted = False
    if signals.database_ok:
        try:
            with sessions() as session:
                saved = record(session, reports, now, "live")
                incidents = [incident_to_dict(i) for i in saved]
            persisted = True
        except OperationalError:  # the database went away after the check
            log.error("incidents could not be saved")
    return {
        "observed_at": now.isoformat(),
        "persisted": persisted,
        "not_checked": not_checked,  # empty results mean nothing for these
        "incidents": incidents,
    }


@router.post("/simulate", status_code=201)
def simulate(
    req: SimulateRequest, sessions: Sessions, settings: SettingsDep
) -> dict[str, Any]:
    """Diagnose a fixture from data/incidents. Stored with source "simulated"."""
    path = settings.incident_dir / f"{req.scenario}.json"
    if not path.is_file():
        raise ApiError(404, "not_found", f"scenario {req.scenario} not found")
    signals = Signals.model_validate_json(path.read_text(encoding="utf-8"))
    reports = diagnose(signals, settings.prometheus_public_url)
    with sessions() as session:
        incidents = record(session, reports, datetime.now(UTC), "simulated")
        return {"incidents": [incident_to_dict(i) for i in incidents]}


@router.get("")
def list_incidents(
    sessions: Sessions,
    status: Literal["open", "resolved"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Most recently seen first."""
    query = select(Incident).order_by(Incident.last_seen.desc()).limit(limit)
    if status:
        query = query.where(Incident.status == status)
    with sessions() as session:
        return {"incidents": [incident_to_dict(i) for i in session.scalars(query)]}


@router.get("/{incident_id}")
def get_incident(incident_id: uuid.UUID, sessions: Sessions) -> dict[str, Any]:
    with sessions() as session:
        return incident_to_dict(_get(session, incident_id))


@router.get("/{incident_id}/report")
def get_report(incident_id: uuid.UUID, sessions: Sessions) -> PlainTextResponse:
    """Markdown: observed facts, hypotheses, uncertainty, suggested actions."""
    with sessions() as session:
        incident = _get(session, incident_id)
        report = Report.model_validate(incident.report)
        return PlainTextResponse(
            markdown_report(report, incident.source), media_type="text/markdown"
        )


@router.post("/{incident_id}/resolve")
def resolve(incident_id: uuid.UUID, sessions: Sessions) -> dict[str, Any]:
    """A person closes an incident; if it recurs, detection opens a new one."""
    with sessions() as session:
        incident = _get(session, incident_id)
        if incident.status == "resolved":
            raise ApiError(409, "incident_resolved", "incident is already resolved")
        incident.status, incident.resolved_at = "resolved", datetime.now(UTC)
        session.commit()
        return incident_to_dict(incident)
