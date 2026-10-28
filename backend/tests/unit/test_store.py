import json
from pathlib import Path

import pytest

from app.services.llm.base import FakeEmbedder
from app.services.rag.store import DocumentRecord, IndexMismatch, VectorStore

emb = FakeEmbedder()
DOCS = {
    "probes": [
        "liveness probe restarts a container",
        "readiness probe gates pod traffic",
    ],
    "promql": [
        "promql rate function computes per second rate",
        "histogram quantile in promql",
    ],
}


def record(doc_id: str, chunks: int) -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, filename=f"{doc_id}.md", checksum=doc_id, size_bytes=1, chunks=chunks
    )


def fill(store: VectorStore) -> None:
    for doc_id, texts in DOCS.items():
        store.add(record(doc_id, len(texts)), texts, emb.embed(texts))


def test_search_ranks_the_relevant_chunk_first(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, emb.model)
    fill(store)
    hits = store.search(emb.embed(["what does a readiness probe do"])[0], 3)
    assert len(hits) == 3
    assert hits[0].text == "readiness probe gates pod traffic"
    assert hits[0].filename == "probes.md"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_top_k_is_capped_by_index_size(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, emb.model)
    fill(store)
    assert len(store.search(emb.embed(["probe"])[0], 10)) == 4


def test_empty_store_returns_nothing(tmp_path: Path) -> None:
    assert VectorStore(tmp_path, emb.model).search(emb.embed(["x"])[0], 4) == []


def test_persists_and_reloads(tmp_path: Path) -> None:
    fill(VectorStore(tmp_path, emb.model))
    reloaded = VectorStore(tmp_path, emb.model)
    assert [d.id for d in reloaded.documents()] == ["probes", "promql"]
    assert reloaded.search(emb.embed(["promql rate"])[0], 1)[0].filename == "promql.md"


def test_duplicate_id_is_not_added_twice(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, emb.model)
    fill(store)
    texts = DOCS["probes"]
    _, created = store.add(record("probes", 2), texts, emb.embed(texts))
    assert created is False
    assert len(store.search(emb.embed(["probe"])[0], 10)) == 4


def test_different_embed_model_refuses_to_load(tmp_path: Path) -> None:
    fill(VectorStore(tmp_path, emb.model))
    with pytest.raises(IndexMismatch, match="re-ingest"):
        VectorStore(tmp_path, "another-model")


def test_vector_size_mismatch_is_rejected(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, emb.model)
    fill(store)
    with pytest.raises(IndexMismatch):
        store.add(record("other", 1), ["x"], FakeEmbedder(dim=8).embed(["x"]))
    with pytest.raises(IndexMismatch):
        store.search([0.1] * 8, 1)


def test_failed_save_leaves_memory_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = VectorStore(tmp_path, emb.model)
    fill(store)

    def locked(*_: object) -> None:
        raise PermissionError("file in use")

    monkeypatch.setattr(store, "_save", locked)
    texts = ["new chunk about rollouts"]
    with pytest.raises(PermissionError):
        store.add(record("new", 1), texts, emb.embed(texts))
    assert store.get("new") is None
    assert len(store.search(emb.embed(["probe"])[0], 10)) == 4

    monkeypatch.undo()
    assert store.add(record("new", 1), texts, emb.embed(texts))[1] is True
    assert VectorStore(tmp_path, emb.model).get("new") is not None


def test_failed_first_save_resets_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = VectorStore(tmp_path, emb.model)
    monkeypatch.setattr(store, "_save", lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        store.add(record("a", 1), ["x"], emb.embed(["x"]))
    assert store.search(emb.embed(["x"])[0], 1) == []


def test_unsaved_tail_rows_are_dropped_on_load(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, emb.model)
    fill(store)
    saved_meta = (tmp_path / "store.json").read_text(encoding="utf-8")
    texts = ["half saved document"]
    store.add(record("crash", 1), texts, emb.embed(texts))
    # crash between the swaps: new index.faiss on disk, old store.json
    (tmp_path / "store.json").write_text(saved_meta, encoding="utf-8")

    reloaded = VectorStore(tmp_path, emb.model)
    assert reloaded.get("crash") is None
    assert len(reloaded.search(emb.embed(["probe"])[0], 10)) == 4
    assert reloaded.add(record("crash", 1), texts, emb.embed(texts))[1] is True


def test_missing_rows_refuse_to_load(tmp_path: Path) -> None:
    fill(VectorStore(tmp_path, emb.model))
    meta = tmp_path / "store.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["chunks"].append({"document_id": "probes", "text": "no vector for me"})
    meta.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IndexMismatch, match="rows"):
        VectorStore(tmp_path, emb.model)
