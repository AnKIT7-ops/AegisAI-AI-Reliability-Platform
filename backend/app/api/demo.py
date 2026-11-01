"""Demo data: see what is loaded, and load it. Nothing here removes data."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_sessionmaker
from app.core.config import Settings, get_settings
from app.services.demo import demo_status, load_demo

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])
Sessions = Annotated[sessionmaker[Session], Depends(get_sessionmaker)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("")
def status(sessions: Sessions, settings: SettingsDep) -> dict[str, Any]:
    """Whether the demo is loaded, its documents and its recorded answers."""
    with sessions() as session:
        return demo_status(session, settings)


@router.post("/load")
def load(sessions: Sessions, settings: SettingsDep) -> dict[str, dict[str, int]]:
    """Insert whatever is absent. Safe to repeat; never overwrites."""
    with sessions() as session:
        return load_demo(session, settings)
