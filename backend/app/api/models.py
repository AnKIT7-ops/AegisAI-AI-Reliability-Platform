"""Which models are configured and whether the provider has them."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_ollama
from app.core.config import Settings, get_settings
from app.services.llm.base import ModelUnavailable
from app.services.llm.ollama import OllamaClient

router = APIRouter(prefix="/api/v1/models", tags=["models"])


class ModelStatus(BaseModel):
    name: str
    installed: bool


class ModelsResponse(BaseModel):
    provider: str
    reachable: bool
    chat_model: ModelStatus
    embed_model: ModelStatus


def _installed(name: str, names: list[str]) -> bool:
    # Ollama lists untagged pulls as "<name>:latest".
    return name in names or ":" not in name and f"{name}:latest" in names


@router.get("")
def models(
    settings: Annotated[Settings, Depends(get_settings)],
    ollama: Annotated[OllamaClient, Depends(get_ollama)],
) -> ModelsResponse:
    """Always 200: an unreachable provider is reported, not raised."""
    if settings.llm_provider == "fake":
        reachable, names = True, [settings.chat_model, settings.embed_model]
    else:
        try:
            reachable, names = True, ollama.installed_models()
        except ModelUnavailable:
            reachable, names = False, []
    return ModelsResponse(
        provider=settings.llm_provider,
        reachable=reachable,
        chat_model=ModelStatus(
            name=settings.chat_model, installed=_installed(settings.chat_model, names)
        ),
        embed_model=ModelStatus(
            name=settings.embed_model, installed=_installed(settings.embed_model, names)
        ),
    )
