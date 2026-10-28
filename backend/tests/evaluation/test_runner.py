"""The full v1 dataset over the sample corpus, with the deterministic fakes."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.evaluation.dataset import Dataset, load_dataset
from app.services.evaluation.runner import EvalConfig, RunReport, run_evaluation
from app.services.llm.base import FakeChat, FakeEmbedder, Generation, ModelUnavailable
from app.services.rag.answer import PROMPT_VERSION
from app.services.rag.ingest import ingest_document
from app.services.rag.store import VectorStore

ROOT = Path(__file__).parents[3]
EMB = FakeEmbedder()
# The fake embedder scores differ from nomic-embed-text, so it has its own cut-off.
BASELINE = EvalConfig(label="baseline", top_k=4, min_score=0.3)
DEGRADED = EvalConfig(label="degraded", top_k=1, min_score=0.0)
TIMING = {"latency_ms_p50", "latency_ms_p95"}


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        llm_provider="fake",
        index_dir=tmp_path_factory.mktemp("index"),
        embed_document_prefix="",
        embed_query_prefix="",
    )


@pytest.fixture(scope="module")
def store(settings: Settings) -> VectorStore:
    s = VectorStore(settings.index_dir, EMB.model)
    for f in sorted((ROOT / "data" / "sample_docs").glob("*.md")):
        if f.name != "ATTRIBUTION.md":
            ingest_document(f.name, f.read_bytes(), s, EMB, settings)
    return s


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(ROOT / "data" / "evaluation" / "v1.jsonl")


@pytest.fixture(scope="module")
def baseline(dataset: Dataset, store: VectorStore, settings: Settings) -> RunReport:
    return run_evaluation(dataset, BASELINE, store, EMB, FakeChat(), settings)


def untimed(metrics: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in metrics.items() if k not in TIMING}


def test_report_records_the_configuration(
    baseline: RunReport, dataset: Dataset
) -> None:
    assert baseline.config == BASELINE
    assert baseline.dataset_version == "v1"
    assert baseline.dataset_checksum == dataset.checksum
    assert baseline.prompt_version == PROMPT_VERSION
    assert (baseline.chat_model, baseline.embed_model) == ("fake-chat", "fake-embed")
    assert [r.example_id for r in baseline.results] == [e.id for e in dataset.examples]
    assert baseline.metrics["examples"] == len(dataset.examples)
    assert baseline.metrics["errors"] == 0


def test_runs_are_repeatable(
    baseline: RunReport, dataset: Dataset, store: VectorStore, settings: Settings
) -> None:
    again = run_evaluation(dataset, BASELINE, store, EMB, FakeChat(), settings)
    assert untimed(again.metrics) == untimed(baseline.metrics)


def test_degraded_retrieval_is_detected(
    baseline: RunReport, dataset: Dataset, store: VectorStore, settings: Settings
) -> None:
    degraded = run_evaluation(dataset, DEGRADED, store, EMB, FakeChat(), settings)
    b, d = baseline.metrics, degraded.metrics
    assert d["recall_at_k"] < b["recall_at_k"] - 0.1
    assert d["mrr"] < b["mrr"]
    # No score cut-off: off-topic questions reach the model instead of being refused.
    assert d["correct_refusal_rate"] < b["correct_refusal_rate"]


def test_provider_failures_are_counted_not_raised(
    dataset: Dataset, store: VectorStore, settings: Settings
) -> None:
    class Down:
        model = "down"

        def generate(self, system: str, prompt: str) -> Generation:
            raise ModelUnavailable("Ollama timed out")

    report = run_evaluation(dataset, BASELINE, store, EMB, Down(), settings)
    failed = [r for r in report.results if r.error]
    assert failed and all(r.error == "Ollama timed out" for r in failed)
    assert report.metrics["errors"] == len(failed)
    # Questions refused before any model call still succeed.
    assert report.metrics["failure_rate"] < 1


def test_results_carry_citations_and_tokens(baseline: RunReport) -> None:
    answered = [r for r in baseline.results if r.answer and r.answer.startswith("[1]")]
    assert answered
    assert all(r.cited == [1] for r in answered)
    assert all(r.sources and r.sources[0] == r.retrieved[0] for r in answered)
    assert all(r.prompt_tokens and r.completion_tokens for r in answered)
    assert all(len(r.retrieved) == BASELINE.top_k for r in baseline.results)
