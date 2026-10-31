"""The CI quality gate: the evaluation on the fake providers, no model needed.

Exit code 0 when every limit in the gate file holds, 1 otherwise. Anything that
stops the run (missing or invalid file) raises, which also exits non-zero.

The fakes measure the pipeline, not the model: retrieval, chunking, refusals,
citations and the dataset. Model quality stays with the live baseline.

    uv run python -m app.services.evaluation.ci_gate --out ci-report
"""

import argparse
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.services.evaluation.dataset import load_dataset
from app.services.evaluation.report import (
    GateResult,
    Thresholds,
    check_gate,
    markdown_report,
    results_csv,
)
from app.services.evaluation.runner import EvalConfig, RunReport, run_evaluation
from app.services.llm.base import FakeChat, FakeEmbedder
from app.services.rag.ingest import ingest_document
from app.services.rag.store import VectorStore


class CiGate(Thresholds):
    # The fake embedder scores differ from nomic-embed-text, so AEGIS_MIN_SCORE
    # does not apply. top_k and chunking do come from Settings, so a bad default
    # reaches the gate.
    min_score: float


def run_gate(
    gate: CiGate, settings: Settings, top_k: int | None, min_score: float | None
) -> tuple[RunReport, GateResult]:
    embedder = FakeEmbedder()
    with tempfile.TemporaryDirectory() as index_dir:
        settings = settings.model_copy(
            update={
                "index_dir": Path(index_dir),
                "embed_document_prefix": "",
                "embed_query_prefix": "",
            }
        )
        store = VectorStore(settings.index_dir, embedder.model)
        corpus = settings.eval_dir.parent / "sample_docs"
        for path in sorted(corpus.glob("*.md")):
            if path.name != "ATTRIBUTION.md":
                # A Windows checkout may be CRLF; chunks must match the CI runner's.
                data = path.read_bytes().replace(b"\r\n", b"\n")
                ingest_document(path.name, data, store, embedder, settings)
        dataset = load_dataset(settings.eval_dir / f"{gate.dataset_version}.jsonl")
        config = EvalConfig(
            label="ci-gate",
            top_k=settings.top_k if top_k is None else top_k,
            min_score=gate.min_score if min_score is None else min_score,
        )
        report = run_evaluation(dataset, config, store, embedder, FakeChat(), settings)
    return report, check_gate(report.metrics, gate, report.dataset_checksum)


def write_reports(report: RunReport, result: GateResult, out: Path) -> None:
    now = datetime.now(UTC).isoformat()
    run = report.model_dump() | {
        "id": "ci-gate",
        "label": report.config.label,
        "status": "done",
        "top_k": report.config.top_k,
        "min_score": report.config.min_score,
        "created_at": now,
        "finished_at": now,
    }
    results = [r.model_dump() for r in report.results]
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(
        markdown_report(run, results, result), encoding="utf-8"
    )
    (out / "results.csv").write_text(results_csv(results), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Run the CI quality gate.")
    parser.add_argument(
        "--gate", type=Path, default=settings.eval_dir / "v1.ci-gate.json"
    )
    parser.add_argument("--out", type=Path, help="write report.md and results.csv here")
    parser.add_argument("--top-k", type=int, help="override, to try a configuration")
    parser.add_argument("--min-score", type=float, help="override")
    args = parser.parse_args(argv)

    gate = CiGate.model_validate_json(args.gate.read_text(encoding="utf-8"))
    report, result = run_gate(gate, settings, args.top_k, args.min_score)
    if args.out:
        write_reports(report, result, args.out)

    for name in gate.limits:
        print(f"{name}: {report.metrics.get(name)} (limit {gate.limits[name]})")
    print("quality gate:", "PASS" if result.passed else "FAIL")
    for failure in result.failures:
        print(f"  {failure}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
