"""Question answering over the ingested documents."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import get_chat_model, get_embedder, get_store
from app.core.config import Settings, get_settings
from app.services.llm.base import ChatModel, Embedder
from app.services.rag.answer import Answer, answer_question
from app.services.rag.store import VectorStore

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question
    top_k: int | None = Field(default=None, ge=1, le=10)


@router.post("")
def chat(
    req: ChatRequest,
    store: Annotated[VectorStore, Depends(get_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    chat_model: Annotated[ChatModel, Depends(get_chat_model)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Answer:
    """Answer from the documents with numbered sources, or say it isn't there."""
    return answer_question(
        req.question, req.top_k or settings.top_k, store, embedder, chat_model, settings
    )
