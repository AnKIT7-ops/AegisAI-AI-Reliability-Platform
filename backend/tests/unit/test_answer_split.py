import time
from pathlib import Path

from app.core.config import Settings
from app.services.llm.base import FakeChat, FakeEmbedder, Generation
from app.services.rag.answer import answer_from_hits, answer_question, retrieve
from app.services.rag.store import DocumentRecord, Hit, VectorStore

emb = FakeEmbedder()


class Counts:
    model = "c"

    def generate(self, system: str, prompt: str) -> Generation:
        return Generation("ok [1]", 11, 4)


class NoCounts:
    model = "n"

    def generate(self, system: str, prompt: str) -> Generation:
        return Generation("ok [1]")


def hit(score: float) -> Hit:
    return Hit(chunk_id=0, document_id="d", filename="f.md", text="t", score=score)


def test_usage_is_reported_when_a_model_answers() -> None:
    a = answer_from_hits("q", [hit(0.9)], Counts(), 0.2, time.perf_counter())
    assert a.usage is not None
    assert (a.usage.prompt_tokens, a.usage.completion_tokens) == (11, 4)


def test_unreported_counts_stay_none() -> None:
    a = answer_from_hits("q", [hit(0.9)], NoCounts(), 0.2, time.perf_counter())
    assert a.usage is not None and a.usage.prompt_tokens is None


def test_no_usage_when_no_model_is_called() -> None:
    a = answer_from_hits("q", [hit(0.1)], Counts(), 0.2, time.perf_counter())
    assert a.usage is None and a.model is None and a.sources == []


def test_retrieve_keeps_low_scores_and_respects_top_k(tmp_path: Path) -> None:
    s = VectorStore(tmp_path, emb.model)
    texts = ["alpha beta", "gamma delta", "epsilon zeta"]
    rec = DocumentRecord(id="d", filename="d.md", checksum="d", size_bytes=1, chunks=3)
    s.add(rec, texts, emb.embed(texts))
    cfg = Settings(llm_provider="fake", min_score=0.99, embed_query_prefix="")
    hits = retrieve("alpha beta", 2, s, emb, cfg)
    assert len(hits) == 2 and hits[0].score >= hits[1].score
    # The cut-off applies only in answer_question, not retrieve.
    a = answer_question("zzz qqq", 3, s, emb, FakeChat(), cfg)
    assert a.model is None
