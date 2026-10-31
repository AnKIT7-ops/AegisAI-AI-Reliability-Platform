"""Persist incident reports; a repeat of an open incident updates it."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Incident
from app.services.incidents.diagnosis import Report


def record(
    session: Session, reports: list[Report], now: datetime, source: str
) -> list[Incident]:
    incidents = []
    for report in reports:
        # ponytail: check-then-insert, so two simultaneous detections can open a
        # duplicate; harmless with one user. A partial unique index would stop it.
        incident = session.scalars(
            select(Incident)
            .where(
                Incident.type == report.type,
                Incident.source == source,
                Incident.status == "open",
            )
            .limit(1)
        ).first()
        if incident is None:
            incident = Incident(
                type=report.type,
                severity=report.severity,
                source=source,
                report={},
                first_seen=now,
                last_seen=now,
            )
            session.add(incident)
        else:
            incident.occurrences += 1
            incident.last_seen = now
        incident.report = report.model_dump(mode="json")
        incidents.append(incident)
    session.commit()
    return incidents


def incident_to_dict(incident: Incident) -> dict[str, Any]:
    return {
        "id": str(incident.id),
        "type": incident.type,
        "severity": incident.severity,
        "status": incident.status,
        "source": incident.source,
        "occurrences": incident.occurrences,
        "first_seen": incident.first_seen.isoformat(),
        "last_seen": incident.last_seen.isoformat(),
        "resolved_at": (
            incident.resolved_at.isoformat() if incident.resolved_at else None
        ),
        "report": incident.report,
    }


def unsaved(report: Report, now: datetime, source: str) -> dict[str, Any]:
    """The same shape when the database is down and nothing could be stored."""
    return {
        "id": None,
        "type": report.type,
        "severity": report.severity,
        "status": "open",
        "source": source,
        "occurrences": 1,
        "first_seen": now.isoformat(),
        "last_seen": now.isoformat(),
        "resolved_at": None,
        "report": report.model_dump(mode="json"),
    }
