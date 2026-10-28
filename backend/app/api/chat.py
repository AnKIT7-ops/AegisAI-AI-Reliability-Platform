"""Question answering over the ingested documents."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import get_chat_model, get_embedder, get_store
from app.core.config import Settings, get_settings
from app.core.metrics import CHAT_ANSWERS
from app.services.llm.base import ChatModel, Embedder
from app.services.rag.answer import Answer, answer_question, is_refusal
from app.services.rag.store import VectorStore

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
log = logging.getLogger("aegis.chat")

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
    top_k = req.top_k or settings.top_k
    answer = answer_question(req.question, top_k, store, embedder, chat_model, settings)
    refused = is_refusal(answer.answer)
    if answer.model is None:
        CHAT_ANSWERS.labels("refused_low_score").inc()
    else:
        CHAT_ANSWERS.labels("refused_by_model" if refused else "answered").inc()
    usage = answer.usage
    # Sizes and counts only: questions and answers are never logged.
    log.info(
        "answered",
        extra={
            "event": "chat",
            "question_chars": len(req.question),
            "top_k": top_k,
            "sources": len(answer.sources),
            "refused": refused,
            "model": answer.model,
            "latency_ms": answer.latency_ms,
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
        },
    )
    return answer
