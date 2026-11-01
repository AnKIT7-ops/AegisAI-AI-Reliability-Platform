"""The operational summary behind the Overview and Telemetry pages."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_prometheus, get_sessionmaker
from app.core.config import Settings, get_settings
from app.services.incidents.signals import Prometheus, telemetry

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.get("")
def summary(
    sessions: Annotated[sessionmaker[Session], Depends(get_sessionmaker)],
    settings: Annotated[Settings, Depends(get_settings)],
    prometheus: Annotated[Prometheus, Depends(get_prometheus)],
) -> dict[str, Any]:
    """Null means no traffic. Anything unreadable is named in not_checked.

    Ollama is not probed: a failed probe would count as a model failure.
    """
    return telemetry(prometheus, sessions, settings, datetime.now(UTC))
