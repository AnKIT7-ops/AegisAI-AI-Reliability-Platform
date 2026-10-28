"""Validate an uploaded text document, chunk it, embed it and add it to the store."""

import hashlib
import re

from app.core.config import Settings
from app.core.errors import ApiError
from app.services.llm.base import Embedder
from app.services.rag.chunking import chunk_text
from app.services.rag.store import DocumentRecord, VectorStore

ALLOWED_SUFFIXES = (".md", ".txt")


def ingest_document(
    filename: str,
    data: bytes,
    store: VectorStore,
    embedder: Embedder,
    settings: Settings,
) -> tuple[DocumentRecord, bool]:
    """Returns (record, created). Identical bytes return the existing record."""
    # Never trust a client-supplied path: keep the base name only.
    name = re.split(r"[\\/]", filename)[-1].strip()
    if not name or len(name) > 255 or not name.lower().endswith(ALLOWED_SUFFIXES):
        raise ApiError(415, "unsupported_type", "only .md and .txt files are accepted")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(400, "invalid_encoding", "file must be UTF-8 text") from None
    chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise ApiError(400, "empty_document", "file has no text")

    checksum = hashlib.sha256(data).hexdigest()
    existing = store.get(checksum[:16])
    if existing:
        return existing, False
    vectors = embedder.embed([settings.embed_document_prefix + c for c in chunks])
    record = DocumentRecord(
        id=checksum[:16],
        filename=name,
        checksum=checksum,
        size_bytes=len(data),
        chunks=len(chunks),
    )
    return store.add(record, chunks, vectors)
