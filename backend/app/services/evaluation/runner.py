"""Run a dataset through retrieval and answering, and score the results."""

import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.services.evaluation.dataset import Dataset
from app.services.evaluation.metrics import (
    Outcome,
    aggregate,
    cited_numbers,
    score_example,
)
from app.services.llm.base import ChatModel, Embedder, ModelUnavailable
from app.services.rag.answer import PROMPT_VERSION, answer_from_hits, retrieve
from app.services.rag.store import VectorStore


class EvalConfig(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    top_k: int = Field(ge=1, le=10)
    min_score: float = Field(ge=-1, le=1)


class ExampleResult(BaseModel):
    example_id: str
    category: str
    question: str
    answer: str | None
    retrieved: list[str]
    sources: list[str]  # filenames; index 0 is [1]
    cited: list[int]
    scores: dict[str, Any]
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None


class RunReport(BaseModel):
    config: EvalConfig
    dataset_version: str
    dataset_checksum: str
    prompt_version: str
    chat_model: str
    embed_model: str
    results: list[ExampleResult]
    metrics: dict[str, Any]


def _outcome(
    question: str,
    config: EvalConfig,
    store: VectorStore,
    embedder: Embedder,
    chat: ChatModel,
    settings: Settings,
) -> Outcome:
    start = time.perf_counter()
    try:
        hits = retrieve(question, config.top_k, store, embedder, settings)
        answer = answer_from_hits(question, hits, chat, config.min_score, start)
    except ModelUnavailable as exc:
        # One failed call is a measured failure, not the end of the run.
        return Outcome(error=str(exc))
    usage = answer.usage
    return Outcome(
        answer=answer.answer,
        retrieved=[h.filename for h in hits],
        sources=[s.filename for s in answer.sources],
        latency_ms=answer.latency_ms,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )


def run_evaluation(
    dataset: Dataset,
    config: EvalConfig,
    store: VectorStore,
    embedder: Embedder,
    chat: ChatModel,
    settings: Settings,
) -> RunReport:
    outcomes = [
        _outcome(e.question, config, store, embedder, chat, settings)
        for e in dataset.examples
    ]
    scores = [
        score_example(e, o) for e, o in zip(dataset.examples, outcomes, strict=True)
    ]
    results = [
        ExampleResult(
            example_id=e.id,
            category=e.category,
            question=e.question,
            answer=o.answer,
            retrieved=o.retrieved,
            sources=o.sources,
            cited=cited_numbers(o.answer) if o.answer else [],
            scores=s,
            latency_ms=o.latency_ms,
            prompt_tokens=o.prompt_tokens,
            completion_tokens=o.completion_tokens,
            error=o.error,
        )
        for e, o, s in zip(dataset.examples, outcomes, scores, strict=True)
    ]
    return RunReport(
        config=config,
        dataset_version=dataset.version,
        dataset_checksum=dataset.checksum,
        prompt_version=PROMPT_VERSION,
        chat_model=chat.model,
        embed_model=embedder.model,
        results=results,
        metrics=aggregate(dataset.examples, outcomes, scores),
    )
