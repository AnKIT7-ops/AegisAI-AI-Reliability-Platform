"""FAISS vector index with a JSON sidecar that holds document records and chunk text."""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from pydantic import BaseModel, Field

log = logging.getLogger("aegis")


class DocumentRecord(BaseModel):
    id: str
    filename: str
    checksum: str
    size_bytes: int
    chunks: int
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Hit(BaseModel):
    chunk_id: int
    document_id: str
    filename: str
    text: str
    score: float


class IndexMismatch(Exception):
    """The saved index was built with a different embedding model or dimension."""


def _normalized(vectors: list[list[float]]) -> Any:
    arr = np.asarray(vectors, dtype="float32")
    faiss.normalize_L2(arr)  # inner product on unit vectors = cosine similarity
    return arr


# ponytail: exact search, whole store in memory, one lock. Fine for thousands of chunks;
# switch to an IVF/HNSW index and a real database when the corpus outgrows that.
class VectorStore:
    def __init__(self, directory: Path, embed_model: str) -> None:
        self.directory = directory
        self.embed_model = embed_model
        self._lock = threading.Lock()
        self._documents: dict[str, DocumentRecord] = {}
        # Row i of the FAISS index -> {"document_id", "text"}.
        self._chunks: list[dict[str, str]] = []
        self._index: Any = None
        self._load()

    def _load(self) -> None:
        meta = self.directory / "store.json"
        if not meta.exists():
            return
        data = json.loads(meta.read_text(encoding="utf-8"))
        if data["embed_model"] != self.embed_model:
            raise IndexMismatch(
                f"index was built with {data['embed_model']!r}, configured model is "
                f"{self.embed_model!r}; delete {self.directory} and re-ingest"
            )
        self._documents = {d["id"]: DocumentRecord(**d) for d in data["documents"]}
        self._chunks = data["chunks"]
        self._index = faiss.read_index(str(self.directory / "index.faiss"))
        if self._index.ntotal > len(self._chunks):
            # _save swaps index.faiss first, so extra rows are the tail of an add that
            # never reached store.json. Drop them; that document simply wasn't saved.
            log.warning(
                "dropping %d unsaved index rows", self._index.ntotal - len(self._chunks)
            )
            self._index.remove_ids(np.arange(len(self._chunks), self._index.ntotal))
        if self._index.ntotal != len(self._chunks):
            # Rows would map to the wrong chunk text.
            raise IndexMismatch(
                f"index has {self._index.ntotal} rows but store.json has "
                f"{len(self._chunks)} chunks; delete {self.directory} and re-ingest"
            )

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Write to temp files and swap, so a crash never leaves a half-written file.
        tmp_index = self.directory / "index.faiss.tmp"
        faiss.write_index(self._index, str(tmp_index))
        os.replace(tmp_index, self.directory / "index.faiss")
        data = {
            "embed_model": self.embed_model,
            "documents": [d.model_dump(mode="json") for d in self._documents.values()],
            "chunks": self._chunks,
        }
        tmp_meta = self.directory / "store.json.tmp"
        tmp_meta.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp_meta, self.directory / "store.json")

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            return self._documents.get(document_id)

    def documents(self) -> list[DocumentRecord]:
        with self._lock:
            return sorted(self._documents.values(), key=lambda d: d.ingested_at)

    def add(
        self, record: DocumentRecord, texts: list[str], vectors: list[list[float]]
    ) -> tuple[DocumentRecord, bool]:
        """Add a document's chunks. Returns (record, created); never re-adds an id."""
        if not texts or len(texts) != len(vectors):
            raise ValueError("need one vector per chunk and at least one chunk")
        arr = _normalized(vectors)
        with self._lock:
            if record.id in self._documents:
                return self._documents[record.id], False
            if self._index is not None and self._index.d != arr.shape[1]:
                raise IndexMismatch(
                    f"vector size {arr.shape[1]} != index size {self._index.d}"
                )
            new_index = self._index is None
            if new_index:
                self._index = faiss.IndexFlatIP(arr.shape[1])
            start = self._index.ntotal
            self._index.add(arr)
            self._chunks.extend({"document_id": record.id, "text": t} for t in texts)
            self._documents[record.id] = record
            try:
                self._save()
            except BaseException:
                # Keep memory equal to disk, so a retry ingests the document again.
                del self._chunks[start:]
                del self._documents[record.id]
                if new_index:
                    self._index = None
                else:
                    self._index.remove_ids(np.arange(start, self._index.ntotal))
                raise
            return record, True

    def search(self, vector: list[float], k: int) -> list[Hit]:
        """Top-k chunks by cosine similarity, best first."""
        with self._lock:
            if self._index is None or self._index.ntotal == 0:
                return []
            if len(vector) != self._index.d:
                raise IndexMismatch(
                    f"query size {len(vector)} != index size {self._index.d}"
                )
            scores, ids = self._index.search(
                _normalized([vector]), min(k, self._index.ntotal)
            )
            hits = []
            for score, i in zip(scores[0], ids[0], strict=True):
                if i < 0 or i >= len(self._chunks):
                    continue
                chunk = self._chunks[i]
                doc = self._documents[chunk["document_id"]]
                hits.append(
                    Hit(
                        chunk_id=int(i),
                        document_id=doc.id,
                        filename=doc.filename,
                        text=chunk["text"],
                        score=float(score),
                    )
                )
            return hits
