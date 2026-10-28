import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, EvaluationRun
from app.services.evaluation.jobs import mark_interrupted
from app.services.evaluation.report import (
    GateResult,
    Thresholds,
    check_gate,
    load_thresholds,
    markdown_report,
    results_csv,
)

LIMITS = Thresholds(
    dataset_version="v1",
    dataset_checksum="abc",
    basis="test",
    limits={"recall_at_k": 0.9, "failure_rate": 0, "latency_ms_p95": 5000},
)
RUN = {
    "id": "r1",
    "label": "baseline",
    "status": "done",
    "dataset_version": "v1",
    "dataset_checksum": "abc",
    "prompt_version": "1",
    "chat_model": "c",
    "embed_model": "e",
    "top_k": 4,
    "min_score": 0.7,
    "created_at": "t0",
    "finished_at": "t1",
    "error": None,
    "metrics": {"recall_at_k": 0.95, "latency_ms_p95": 3000},
}


def result(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "example_id": "a",
        "category": "answerable",
        "question": "q",
        "answer": "ok [1]",
        "retrieved": ["a.md"],
        "sources": ["a.md"],
        "cited": [1],
        "scores": {"refused": False, "correct": True, "citation_correct": True},
        "latency_ms": 10,
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "error": None,
    }
    return base | kw


def test_gate_passes_within_limits() -> None:
    metrics = {"recall_at_k": 0.95, "failure_rate": 0, "latency_ms_p95": 4000}
    assert check_gate(metrics, LIMITS, "abc") == GateResult(passed=True, failures=[])


def test_gate_reports_each_failure_in_the_right_direction() -> None:
    metrics = {"recall_at_k": 0.5, "failure_rate": 0.1, "latency_ms_p95": None}
    gate = check_gate(metrics, LIMITS, "abc")
    assert not gate.passed
    assert gate.failures == [
        "recall_at_k: 0.5 < 0.9",
        "failure_rate: 0.1 > 0",
        "latency_ms_p95: not measured",
    ]


def test_load_thresholds(tmp_path: Path) -> None:
    assert load_thresholds(tmp_path, "v1") is None
    (tmp_path / "v1.thresholds.json").write_text(LIMITS.model_dump_json())
    assert load_thresholds(tmp_path, "v1") == LIMITS


@pytest.mark.parametrize("payload", ["=HYPERLINK(1)", "+1", "-1", "@SUM(A1)"])
def test_csv_neutralizes_formulas(payload: str) -> None:
    row = results_csv([result(answer=payload)]).splitlines()[1]
    assert f"'{payload}" in row


def test_csv_flattens_scores_and_lists() -> None:
    lines = results_csv([result()]).splitlines()
    header, row = lines[0].split(","), lines[1]
    assert header[:3] == ["example_id", "category", "question"]
    assert "True" in row and '"[""a.md""]"' in row


def test_markdown_lists_failures_and_escapes_cells() -> None:
    results = [
        result(),
        result(example_id="b", scores={"refused": True, "correct": False}),
        result(example_id="u", category="unanswerable", scores={"refused": False}),
        result(
            example_id="i",
            category="injection",
            answer="BANANA | x\ny",
            scores={"resisted": False},
        ),
        result(example_id="e", answer=None, error="Ollama timed out", scores={}),
    ]
    gate = check_gate(RUN["metrics"], LIMITS, "abc")  # type: ignore[arg-type]
    md = markdown_report(RUN, results, gate)
    assert "Quality gate: **FAIL**" in md
    assert "failure_rate: not measured" in md
    assert "## Examples that did not pass (4)" in md
    assert "| b | answerable | refused |" in md
    assert "| u | unanswerable | answered instead of refusing |" in md
    assert "BANANA \\| x y" in md
    assert "| e | answerable | error: Ollama timed out |" in md
    assert "| a |" not in md


def test_markdown_comparison_shows_change() -> None:
    other = RUN | {"id": "r0", "metrics": {"recall_at_k": 0.9, "latency_ms_p95": 2500}}
    md = markdown_report(RUN, [], None, other)
    assert "| recall_at_k | 0.95 | 0.9 | 0.05 |" in md
    assert "| latency_ms_p95 | 3000 | 2500 | 500 |" in md
    assert "None." in md


def test_mark_interrupted_fails_only_active_runs(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    fields = json.loads(
        '{"label": "x", "dataset_version": "v1", "dataset_checksum": "0", '
        '"prompt_version": "1", "chat_model": "c", "embed_model": "e", '
        '"top_k": 4, "min_score": 0.7, "config": {}}'
    )
    with Session(engine) as s:
        s.add_all(
            [
                EvaluationRun(status="running", **fields),
                EvaluationRun(status="pending", **fields),
                EvaluationRun(status="done", **fields),
            ]
        )
        s.commit()
        assert mark_interrupted(s) == 2
        statuses = sorted(r.status for r in s.query(EvaluationRun))
        assert statuses == ["done", "failed", "failed"]
    engine.dispose()


def test_gate_fails_when_the_dataset_changed() -> None:
    metrics = {"recall_at_k": 1.0, "failure_rate": 0, "latency_ms_p95": 1}
    gate = check_gate(metrics, LIMITS, "other")
    assert not gate.passed
    assert "dataset checksum abc, this run used other" in gate.failures[0]


def test_malformed_thresholds_raise_value_error(tmp_path: Path) -> None:
    (tmp_path / "v1.thresholds.json").write_text('{"dataset_version": "v1"}')
    with pytest.raises(ValueError):
        load_thresholds(tmp_path, "v1")


def test_csv_keeps_the_error_text() -> None:
    row = results_csv(
        [result(answer=None, error="Ollama timed out", scores={"error": True})]
    ).splitlines()[1]
    assert row.endswith(",Ollama timed out")


def test_markdown_cells_collapse_all_line_breaks() -> None:
    answer = "a\r\nb\rc | d"
    bad = result(example_id="b", answer=answer, scores={"refused": True})
    md = markdown_report(RUN, [bad], None)
    assert "| a b c \\| d |" in md


def test_comparison_warns_when_datasets_differ() -> None:
    other = RUN | {"id": "r0", "dataset_checksum": "def"}
    md = markdown_report(RUN, [], None, other)
    assert "dataset v1 (checksum def)" in md
    assert "not comparable" in md
    assert "not comparable" not in markdown_report(RUN, [], None, RUN | {"id": "r0"})


def test_markdown_error_cell_is_escaped() -> None:
    bad = result(example_id="e", answer=None, error="bad | cell\nnext", scores={})
    md = markdown_report(RUN, [bad], None)
    assert r"| error: bad \| cell next |" in md


def test_unmeasured_metrics_are_dropped_from_the_gauge() -> None:
    from prometheus_client import REGISTRY

    from app.services.evaluation.jobs import _publish_metrics

    _publish_metrics({"recall_at_k": 0.9})
    assert (
        REGISTRY.get_sample_value(
            "aegis_evaluation_last_metric", {"metric": "recall_at_k"}
        )
        == 0.9
    )
    _publish_metrics({"recall_at_k": None})
    assert (
        REGISTRY.get_sample_value(
            "aegis_evaluation_last_metric", {"metric": "recall_at_k"}
        )
        is None
    )
    _publish_metrics({"never_published": None})  # nothing to drop: no error


def test_unreadable_thresholds_fail_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.evaluation import report

    def unreadable(eval_dir: Path, version: str) -> Thresholds | None:
        raise PermissionError("denied")

    monkeypatch.setattr(report, "load_thresholds", unreadable)
    gate = report.gate_for_run(tmp_path, "v1", {}, "abc")
    assert gate == GateResult(passed=False, failures=["the thresholds file is invalid"])


def test_no_thresholds_file_means_no_gate(tmp_path: Path) -> None:
    from app.services.evaluation.report import gate_for_run

    assert gate_for_run(tmp_path, "v1", {}, "abc") is None
