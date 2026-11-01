import csv as csv_module
import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.deps import get_chat_model
from app.core.config import Settings
from app.db.models import EvaluationRun
from app.main import app
from app.services.evaluation.dataset import load_dataset
from app.services.evaluation.report import Thresholds
from app.services.llm.base import Generation, ModelUnavailable

RUNS = "/api/v1/evaluations"


def start(api: TestClient, **body: object) -> str:
    r = api.post("/api/v1/evaluations/run", json=body)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending"
    return str(r.json()["id"])


def test_run_completes_and_is_saved(api: TestClient, settings: Settings) -> None:
    run_id = start(api, label="baseline")
    # TestClient runs background tasks before returning, so the run is finished.
    r = api.get(f"/api/v1/evaluations/{run_id}")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "done"
    assert (run["dataset_version"], run["label"]) == ("v1", "baseline")
    assert (run["top_k"], run["min_score"]) == (settings.top_k, settings.min_score)
    assert run["config"]["chat_model"] == settings.chat_model
    assert run["config"]["examples"] == 3
    assert run["metrics"]["examples"] == 3
    assert run["metrics"]["errors"] == 0
    assert run["gate"] is None  # no thresholds file for this dataset
    assert [x["example_id"] for x in run["results"]] == ["a1", "u1", "i1"]
    assert run["started_at"] and run["finished_at"]

    out = Path(run["artifact_path"])
    assert out == settings.eval_runs_dir / run_id
    assert {p.name for p in out.iterdir()} == {
        "report.json",
        "results.csv",
        "report.md",
    }
    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert saved["metrics"] == run["metrics"]
    assert len(saved["results"]) == 3


def test_overrides_are_recorded(api: TestClient) -> None:
    run_id = start(api, label="degraded", top_k=1, min_score=0.0)
    run = api.get(f"/api/v1/evaluations/{run_id}").json()
    assert (run["label"], run["top_k"], run["min_score"]) == ("degraded", 1, 0.0)
    assert all(len(x["retrieved"]) <= 1 for x in run["results"])


def test_list_is_newest_first_without_results(api: TestClient) -> None:
    first, second = start(api, label="one"), start(api, label="two")
    runs = api.get("/api/v1/evaluations").json()["runs"]
    assert [r["id"] for r in runs] == [second, first]
    assert "results" not in runs[0]
    assert len(api.get("/api/v1/evaluations?limit=1").json()["runs"]) == 1


def test_only_one_run_at_a_time(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    with sessions() as s:
        s.add(
            EvaluationRun(
                label="x",
                status="running",
                dataset_version="v1",
                dataset_checksum="0" * 16,
                prompt_version="1",
                chat_model="c",
                embed_model="e",
                top_k=4,
                min_score=0.7,
                config={},
            )
        )
        s.commit()
    r = api.post("/api/v1/evaluations/run", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "evaluation_running"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"dataset": "v9"}, 404),
        ({"dataset": "../secrets"}, 422),
        ({"dataset": "v1.jsonl"}, 422),
        ({"top_k": 0}, 422),
        ({"min_score": 2}, 422),
        ({"label": ""}, 422),
        ({"extra": 1}, 422),
    ],
)
def test_bad_requests(api: TestClient, body: dict[str, object], status: int) -> None:
    r = api.post("/api/v1/evaluations/run", json=body)
    assert r.status_code == status


def test_unknown_and_malformed_run_ids(api: TestClient) -> None:
    missing = uuid.uuid4()
    assert api.get(f"/api/v1/evaluations/{missing}").status_code == 404
    assert api.get(f"/api/v1/evaluations/{missing}/report").status_code == 404
    assert api.get("/api/v1/evaluations/not-a-uuid").status_code == 422


def test_reports(api: TestClient) -> None:
    base = start(api, label="baseline")
    run_id = start(api, label="degraded", top_k=1, min_score=0.0)

    md = api.get(f"/api/v1/evaluations/{run_id}/report")
    assert md.headers["content-type"].startswith("text/markdown")
    assert f"# Evaluation run {run_id}" in md.text
    assert "| recall_at_k |" in md.text

    compared = api.get(f"/api/v1/evaluations/{run_id}/report?baseline={base}")
    assert f"Compared with run {base} (baseline)" in compared.text
    assert "| Metric | This run | Baseline | Change |" in compared.text

    csv = api.get(f"/api/v1/evaluations/{run_id}/report?format=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("example_id,category,question")
    assert len(list(csv_module.reader(io.StringIO(csv.text)))) == 4  # header + 3

    assert api.get(f"/api/v1/evaluations/{run_id}/report?format=pdf").status_code == 422
    missing = api.get(f"/api/v1/evaluations/{run_id}/report?baseline={uuid.uuid4()}")
    assert missing.status_code == 404


def test_gate_uses_the_thresholds_file(api: TestClient, settings: Settings) -> None:
    checksum = load_dataset(settings.eval_dir / "v1.jsonl").checksum
    (settings.eval_dir / "v1.thresholds.json").write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "dataset_checksum": checksum,
                "basis": "test",
                "limits": {"failure_rate": 0, "recall_at_k": 2.0},
            }
        ),
        encoding="utf-8",
    )
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["gate"]["passed"] is False
    assert run["gate"]["failures"] == ["recall_at_k: 1 < 2"]
    report = api.get(f"/api/v1/evaluations/{run['id']}/report").text
    assert "Quality gate: **FAIL**" in report


def test_model_errors_are_measured_not_fatal(api: TestClient) -> None:
    class Down:
        model = "down"

        def generate(self, system: str, prompt: str) -> Generation:
            raise ModelUnavailable("Ollama timed out")

    app.dependency_overrides[get_chat_model] = Down
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "done"
    assert run["metrics"]["errors"] >= 1
    assert any(x["error"] == "Ollama timed out" for x in run["results"])


def test_unexpected_errors_fail_the_run_without_details(api: TestClient) -> None:
    class Broken:
        model = "broken"

        def generate(self, system: str, prompt: str) -> Generation:
            raise RuntimeError("secret detail C:/internal/path")

    app.dependency_overrides[get_chat_model] = Broken
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "failed"
    assert run["error"] == "the run failed; see the server log"
    assert run["results"] == []
    # A failed run no longer blocks the next one.
    app.dependency_overrides.pop(get_chat_model)
    assert api.post("/api/v1/evaluations/run", json={}).status_code == 202


def test_no_database_configured_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    deps._sessionmaker.cache_clear()
    r = client.get("/api/v1/evaluations")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "database_unavailable"


def test_invalid_thresholds_file_fails_the_gate_not_the_run(
    api: TestClient, settings: Settings
) -> None:
    (settings.eval_dir / "v1.thresholds.json").write_text("{not json", encoding="utf-8")
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "done"
    assert run["artifact_path"] is not None  # reports are written, with a failed gate
    assert run["gate"] == {
        "passed": False,
        "failures": ["the thresholds file is invalid"],
    }
    assert api.get(f"/api/v1/evaluations/{run['id']}/report").status_code == 200


def test_unwritable_artifact_dir_keeps_the_run_done(
    api: TestClient, settings: Settings
) -> None:
    settings.eval_runs_dir.write_text("a file, not a directory", encoding="utf-8")
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "done"
    assert run["metrics"]["examples"] == 3
    assert run["artifact_path"] is None


def test_gate_fails_through_the_api_when_the_dataset_changed(
    api: TestClient, settings: Settings
) -> None:
    (settings.eval_dir / "v1.thresholds.json").write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "dataset_checksum": "stale",
                "basis": "test",
                "limits": {"failure_rate": 1},
            }
        ),
        encoding="utf-8",
    )
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "done"
    assert run["gate"]["passed"] is False
    assert "re-measure the baseline" in run["gate"]["failures"][0]


def test_execute_run_never_raises_when_the_failure_cannot_be_recorded() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.services.evaluation.jobs import execute_run

    session = MagicMock()
    session.get.return_value = SimpleNamespace(label="x", dataset_version="v1")
    session.commit.side_effect = RuntimeError("database gone")
    sessions = MagicMock()
    sessions.return_value.__enter__.return_value = session
    # The other arguments are never reached: the first commit already fails.
    job: Any = execute_run
    job(
        uuid.uuid4(),
        SimpleNamespace(examples=[]),
        None,
        None,
        None,
        None,
        None,
        sessions,
    )
    assert session.commit.call_count >= 2  # the run, then the failed-status attempt


def stored_run(sessions: sessionmaker[Session], **kw: Any) -> str:
    fields: dict[str, Any] = dict(
        status="done",
        label="r",
        dataset_version="v1",
        dataset_checksum="abc",
        prompt_version="2",
        chat_model="m",
        embed_model="e",
        top_k=4,
        min_score=0.7,
        config={},
        metrics={"recall_at_k": 0.9, "latency_ms_p95": 1000, "examples": 3},
    )
    with sessions() as session:
        run = EvaluationRun(**(fields | kw))
        session.add(run)
        session.commit()
        return str(run.id)


def write_limits(settings: Settings) -> None:
    (settings.eval_dir / "v1.thresholds.json").write_text(
        Thresholds(
            dataset_version="v1",
            dataset_checksum="abc",
            basis="test",
            limits={"recall_at_k": 0.8},
        ).model_dump_json(),
        encoding="utf-8",
    )


def test_list_carries_the_gate(
    api: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    write_limits(settings)
    stored_run(sessions)
    assert api.get(RUNS).json()["runs"][0]["gate"] == {"passed": True, "failures": []}


def test_list_gate_is_null_without_thresholds(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    stored_run(sessions)
    assert api.get(RUNS).json()["runs"][0]["gate"] is None


def test_list_gate_is_null_for_an_unfinished_run(
    api: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    write_limits(settings)
    stored_run(sessions, status="failed", metrics=None)
    assert api.get(RUNS).json()["runs"][0]["gate"] is None


def test_detail_carries_the_limits(
    api: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    write_limits(settings)
    run_id = stored_run(sessions)
    assert api.get(f"{RUNS}/{run_id}").json()["limits"] == {"recall_at_k": 0.8}


def test_detail_limits_are_empty_without_thresholds(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    run_id = stored_run(sessions)
    assert api.get(f"{RUNS}/{run_id}").json()["limits"] == {}


def test_detail_limits_are_empty_when_the_file_is_invalid(
    api: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    (settings.eval_dir / "v1.thresholds.json").write_text("{", encoding="utf-8")
    run_id = stored_run(sessions)
    body = api.get(f"{RUNS}/{run_id}").json()
    assert body["limits"] == {}
    assert body["gate"]["passed"] is False


def test_comparison_says_which_way_metrics_moved(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    base = stored_run(sessions, label="before")
    run = stored_run(
        sessions,
        label="after",
        metrics={"recall_at_k": 0.95, "latency_ms_p95": 1200, "examples": 3},
    )
    body = api.get(f"{RUNS}/{run}/comparison?baseline={base}").json()
    assert body["status"] == "comparable"
    assert body["run"]["label"] == "after"
    assert body["baseline"]["label"] == "before"
    rows = {m["name"]: m for m in body["metrics"]}
    assert set(rows) == {"recall_at_k", "latency_ms_p95"}
    assert rows["recall_at_k"]["direction"] == "improved"
    assert rows["latency_ms_p95"]["direction"] == "regressed"
    assert rows["recall_at_k"]["delta"] == 0.05


def test_comparison_across_datasets_lists_no_deltas(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    base = stored_run(sessions, dataset_checksum="other")
    run = stored_run(sessions)
    body = api.get(f"{RUNS}/{run}/comparison?baseline={base}").json()
    assert (body["status"], body["metrics"]) == ("dataset_changed", [])


def test_comparison_tolerates_a_run_without_metrics(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    base = stored_run(sessions, status="failed", metrics=None)
    run = stored_run(sessions)
    r = api.get(f"{RUNS}/{run}/comparison?baseline={base}")
    assert (r.status_code, r.json()["metrics"]) == (200, [])


def test_comparison_needs_both_runs(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    run = stored_run(sessions)
    assert (
        api.get(f"{RUNS}/{run}/comparison?baseline={uuid.uuid4()}").status_code == 404
    )
    assert api.get(f"{RUNS}/{run}/comparison").status_code == 422
