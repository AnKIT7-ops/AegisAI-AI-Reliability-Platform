"""Lazily built shared services. Tests swap them via app.dependency_overrides."""

import threading
from functools import lru_cache

from app.core.config import get_settings
from app.services.llm.base import ChatModel, Embedder, FakeChat, FakeEmbedder
from app.services.llm.ollama import OllamaChat, OllamaClient, OllamaEmbedder
from app.services.rag.store import VectorStore


@lru_cache
def get_ollama() -> OllamaClient:
    s = get_settings()
    return OllamaClient(s.ollama_url, s.connect_timeout_s, s.generate_timeout_s)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.llm_provider == "fake":
        return FakeEmbedder()
    return OllamaEmbedder(get_ollama(), s.embed_model)


@lru_cache
def get_chat_model() -> ChatModel:
    s = get_settings()
    if s.llm_provider == "fake":
        return FakeChat()
    return OllamaChat(get_ollama(), s.chat_model)


_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    # Built once under a lock: two stores on one directory would overwrite each other.
    global _store
    with _store_lock:
        if _store is None:
            _store = VectorStore(get_settings().index_dir, get_embedder().model)
        return _store
