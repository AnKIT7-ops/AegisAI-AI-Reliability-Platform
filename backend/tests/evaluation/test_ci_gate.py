"""The CI quality gate blocks what it should, through its real entry point."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.evaluation.ci_gate import main
from app.services.evaluation.dataset import load_dataset

EVAL_DIR = Path(__file__).parents[3] / "data" / "evaluation"
GATE = EVAL_DIR / "v1.ci-gate.json"


def gate_file(tmp_path: Path, **changes: object) -> Path:
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(json.loads(GATE.read_text("utf-8")) | changes), "utf-8")
    return path


def test_committed_gate_passes_and_writes_reports(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path)]) == 0
    assert "Quality gate: **PASS**" in (tmp_path / "report.md").read_text("utf-8")
    assert (tmp_path / "results.csv").read_text("utf-8").startswith("example_id,")


def test_gate_fails_when_a_required_metric_falls_below_its_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--top-k", "1", "--min-score", "0"]) == 1
    out = capsys.readouterr().out
    assert "quality gate: FAIL" in out
    assert "recall_at_k: 0.6667 < 0.9" in out


def test_a_bad_default_setting_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AEGIS_TOP_K", "1")
    assert main([]) == 1


@pytest.mark.parametrize(
    "metric, limit",
    [("recall_at_k", 1.0), ("false_refusal_rate", -1.0)],
)
def test_limits_apply_in_their_own_direction(
    tmp_path: Path, metric: str, limit: float
) -> None:
    limits = json.loads(GATE.read_text("utf-8"))["limits"] | {metric: limit}
    assert main(["--gate", str(gate_file(tmp_path, limits=limits))]) == 1


def test_gate_fails_when_a_limit_is_not_measured(tmp_path: Path) -> None:
    limits = {"no_such_metric": 0.5}
    assert main(["--gate", str(gate_file(tmp_path, limits=limits))]) == 1


def test_gate_fails_on_an_edited_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--gate", str(gate_file(tmp_path, dataset_checksum="0" * 16))]) == 1
    assert "re-measure the baseline" in capsys.readouterr().out


def test_invalid_gate_file_stops_the_run(tmp_path: Path) -> None:
    path = tmp_path / "gate.json"
    path.write_text('{"dataset_version": "v1"}', "utf-8")
    with pytest.raises(ValidationError):
        main(["--gate", str(path)])


def test_gate_is_pinned_to_the_committed_dataset() -> None:
    gate = json.loads(GATE.read_text("utf-8"))
    dataset = load_dataset(Settings().eval_dir / f"{gate['dataset_version']}.jsonl")
    assert gate["dataset_checksum"] == dataset.checksum
