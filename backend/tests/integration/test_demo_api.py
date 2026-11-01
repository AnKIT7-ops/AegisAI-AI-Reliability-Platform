"""The demo: real exported fixtures, kept apart from live data."""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.metrics import EVALUATION_LAST_GATE
from app.db.models import EvaluationResult, EvaluationRun, Incident
from app.services import demo as demo_service
from app.services.evaluation.report import gate_for_run
from tests.integration.test_incidents_api import add_run, prometheus, use

ROOT = Path(__file__).parents[3]
DEMO_DIR = ROOT / "data" / "demo"
EVAL_DIR = ROOT / "data" / "evaluation"
SAMPLE_DOCS = ROOT / "data" / "sample_docs"
DEMO = "/api/v1/demo"
LOAD = "/api/v1/demo/load"
RELEASES = "/api/v1/releases"
FULL = {"evaluation_runs": 3, "releases": 3, "release_events": 9, "incidents": 1}
NONE = dict.fromkeys(FULL, 0)


@pytest.fixture
def demo(api: TestClient, settings: Settings) -> TestClient:
    settings.eval_dir = EVAL_DIR
    settings.demo_dir = DEMO_DIR
    return api


def by_version(client: TestClient) -> dict[str, dict[str, Any]]:
    rows = client.get(f"{RELEASES}?source=demo").json()["releases"]
    return {r["version"]: client.get(f"{RELEASES}/{r['id']}").json() for r in rows}


def run_fixtures() -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (DEMO_DIR / "runs").glob("*.json")
    }


def test_status_before_loading(demo: TestClient) -> None:
    body = demo.get(DEMO).json()
    assert body["loaded"] is False
    assert len(body["documents"]) == 15
    assert body["recorded_answers"] == []
    assert body["run_ids"] == {}
    assert body["incident_id"] is None


def test_load_creates_the_demo(demo: TestClient) -> None:
    r = demo.post(LOAD)
    assert r.status_code == 200
    assert r.json() == {"created": FULL, "already_present": NONE}


def test_second_load_creates_nothing(demo: TestClient) -> None:
    demo.post(LOAD)
    assert demo.post(LOAD).json() == {"created": NONE, "already_present": FULL}


def test_status_after_loading(demo: TestClient) -> None:
    demo.post(LOAD)
    body = demo.get(DEMO).json()
    assert body["loaded"] is True
    assert set(body["run_ids"]) == {"known-good", "current", "degraded"}
    assert body["incident_id"]
    recorded = body["recorded_answers"]
    assert [a["example_id"] for a in recorded] == [
        "prom-storage-01",
        "services-01",
        "lifecycle-03",
        "inject-01",
        "none-01",
    ]
    first = recorded[0]
    assert first["question"] and first["answer"] and first["sources"]
    assert first["latency_ms"] > 0
    assert first["run_id"] == body["run_ids"]["current"]


def test_runs_are_marked_demo_with_their_real_numbers(demo: TestClient) -> None:
    demo.post(LOAD)
    runs = demo.get("/api/v1/evaluations").json()["runs"]
    assert {r["source"] for r in runs} == {"demo"}
    assert {r["label"] for r in runs} == {
        "baseline",
        "drill-baseline",
        "drill-degraded",
    }
    expected = run_fixtures()["degraded"]["metrics"]
    degraded = [r for r in runs if r["label"] == "drill-degraded"]
    assert degraded[0]["metrics"] == expected


def test_run_detail_serves_the_stored_results(demo: TestClient) -> None:
    run_id = demo.post(LOAD) and demo.get(DEMO).json()["run_ids"]["current"]
    body = demo.get(f"/api/v1/evaluations/{run_id}").json()
    assert len(body["results"]) == 69
    assert body["gate"]["passed"] is True
    assert demo.get(f"/api/v1/evaluations/{run_id}/report").status_code == 200


def test_release_history(demo: TestClient) -> None:
    demo.post(LOAD)
    releases = by_version(demo)
    assert {v: r["status"] for v, r in releases.items()} == {
        "demo-1": "superseded",
        "demo-2": "active",
        "demo-3": "rejected",
    }
    assert all(r["simulated"] is True for r in releases.values())
    assert [e["event"] for e in releases["demo-1"]["events"]] == [
        "created",
        "canary_passed",
        "promoted",
        "superseded",
    ]
    assert [e["event"] for e in releases["demo-3"]["events"]] == [
        "created",
        "canary_failed",
    ]
    events = [e for r in releases.values() for e in r["events"]]
    assert {e["actor"] for e in events} == {"demo-seed"}


def test_rejection_comes_from_the_gate_not_the_fixture(demo: TestClient) -> None:
    demo.post(LOAD)
    rejected = by_version(demo)["demo-3"]
    assert rejected["gate_passed"] is False
    gate = gate_for_run(
        EVAL_DIR, "v1", rejected["metrics"], rejected["dataset_checksum"]
    )
    assert gate is not None
    assert rejected["gate_failures"] == gate.failures
    assert any("recall_at_k" in failure for failure in gate.failures)


def test_comparisons_are_chained(demo: TestClient) -> None:
    demo.post(LOAD)
    releases = by_version(demo)
    assert releases["demo-1"]["comparison"]["status"] == "no_baseline"
    assert releases["demo-2"]["baseline_release_id"] == releases["demo-1"]["id"]
    assert releases["demo-2"]["comparison"]["status"] == "comparable"
    assert releases["demo-3"]["baseline_release_id"] == releases["demo-2"]["id"]


def test_demo_incident_is_simulated_and_open(demo: TestClient) -> None:
    demo.post(LOAD)
    incident_id = demo.get(DEMO).json()["incident_id"]
    incident = demo.get(f"/api/v1/incidents/{incident_id}").json()
    assert (incident["type"], incident["status"], incident["source"]) == (
        "model_timeout",
        "open",
        "simulated",
    )
    assert incident["report"]["hypotheses"]


def test_user_actions_survive_a_second_load(demo: TestClient) -> None:
    demo.post(LOAD)
    releases = by_version(demo)
    r = demo.post(
        f"{RELEASES}/{releases['demo-2']['id']}/rollback",
        json={"target_release_id": releases["demo-1"]["id"], "reason": "trying it"},
    )
    assert r.status_code == 200
    incident_id = demo.get(DEMO).json()["incident_id"]
    demo.post(f"/api/v1/incidents/{incident_id}/resolve")

    assert demo.post(LOAD).json()["created"] == NONE

    after = by_version(demo)
    assert after["demo-1"]["status"] == "active"
    assert after["demo-2"]["status"] == "rolled_back"
    assert len(after["demo-2"]["events"]) == 4
    incident = demo.get(f"/api/v1/incidents/{incident_id}").json()
    assert incident["status"] == "resolved"


def test_a_real_active_release_coexists(
    demo: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    metrics = run_fixtures()["current"]["metrics"]
    fixture = run_fixtures()["current"]
    with sessions() as session:
        run = EvaluationRun(
            status="done",
            **{
                k: fixture[k]
                for k in (
                    "label",
                    "dataset_version",
                    "dataset_checksum",
                    "prompt_version",
                    "chat_model",
                    "embed_model",
                    "top_k",
                    "min_score",
                    "config",
                )
            },
            metrics=metrics,
        )
        session.add(run)
        session.commit()
        run_id = str(run.id)
    live = demo.post(
        RELEASES, json={"version": "demo-2", "evaluation_run_id": run_id}
    ).json()["id"]
    demo.post(f"{RELEASES}/{live}/canary", json={})
    assert demo.post(f"{RELEASES}/{live}/promote", json={}).status_code == 200

    assert demo.post(LOAD).json()["created"] == FULL

    active = demo.get(f"{RELEASES}?status=active").json()["releases"]
    assert {(r["source"], r["version"]) for r in active} == {
        ("live", "demo-2"),
        ("demo", "demo-2"),
    }


def test_loading_leaves_live_rows_untouched(
    demo: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    run = add_run(sessions, settings, recall=0.97)
    with sessions() as session:
        before = dict(vars(session.get(EvaluationRun, run.id)))
    demo.post(LOAD)
    with sessions() as session:
        after = dict(vars(session.get(EvaluationRun, run.id)))
    before.pop("_sa_instance_state")
    after.pop("_sa_instance_state")
    assert after == before


def test_demo_runs_raise_no_incident(demo: TestClient) -> None:
    demo.post(LOAD)
    use(prometheus())
    assert demo.post("/api/v1/incidents/detect").json()["incidents"] == []


def test_a_demo_candidate_inherits_the_source(demo: TestClient) -> None:
    demo.post(LOAD)
    run_id = demo.get(DEMO).json()["run_ids"]["current"]
    r = demo.post(RELEASES, json={"version": "try-1", "evaluation_run_id": run_id})
    assert r.json()["source"] == "demo"


def test_loading_writes_no_documents(demo: TestClient) -> None:
    before = demo.get("/api/v1/documents").json()
    demo.post(LOAD)
    assert demo.get("/api/v1/documents").json() == before


def test_loading_publishes_no_metrics(demo: TestClient) -> None:
    EVALUATION_LAST_GATE.set(1)
    demo.post(LOAD)
    assert EVALUATION_LAST_GATE._value.get() == 1  # noqa: SLF001


def assert_stale_and_empty(client: TestClient, sessions: sessionmaker[Session]) -> None:
    r = client.post(LOAD)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "demo_stale")
    with sessions() as session:
        assert session.scalars(select(EvaluationRun)).first() is None
        assert session.scalars(select(Incident)).first() is None


def changed_thresholds(tmp_path: Path, **changes: Any) -> Path:
    data = json.loads((EVAL_DIR / "v1.thresholds.json").read_text(encoding="utf-8"))
    data.update(changes)
    out = tmp_path / "changed"
    out.mkdir()
    (out / "v1.thresholds.json").write_text(json.dumps(data), encoding="utf-8")
    return out


def test_stale_thresholds_refuse_the_load(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    settings.eval_dir = changed_thresholds(tmp_path, limits={"recall_at_k": 0.999})
    assert_stale_and_empty(demo, sessions)


def test_a_rejected_fixture_that_now_passes_is_stale(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    settings.eval_dir = changed_thresholds(tmp_path, limits={"recall_at_k": 0.1})
    assert_stale_and_empty(demo, sessions)


def test_changed_dataset_refuses_the_load(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    settings.eval_dir = changed_thresholds(tmp_path, dataset_checksum="changed")
    assert_stale_and_empty(demo, sessions)


def test_missing_thresholds_refuse_the_load(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    settings.eval_dir = tmp_path
    assert_stale_and_empty(demo, sessions)


def test_missing_fixtures_refuse_the_load(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    settings.demo_dir = tmp_path / "nowhere"
    assert_stale_and_empty(demo, sessions)
    assert demo.get(DEMO).json()["loaded"] is False


def test_malformed_fixture_refuses_the_load(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    copy = tmp_path / "demo"
    shutil.copytree(DEMO_DIR, copy)
    (copy / "runs" / "current.json").write_text('{"label": "x"}', encoding="utf-8")
    settings.demo_dir = copy
    assert_stale_and_empty(demo, sessions)


def test_missing_scenario_refuses_the_load(
    demo: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    settings.incident_dir = tmp_path
    assert_stale_and_empty(demo, sessions)


def test_losing_a_race_reports_what_is_present(
    demo: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lost(*_: Any) -> None:
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(demo_service, "_insert_all", lost)
    assert demo.post(LOAD).json() == {"created": NONE, "already_present": FULL}


def test_committed_fixtures_are_current() -> None:
    thresholds = json.loads(
        (EVAL_DIR / "v1.thresholds.json").read_text(encoding="utf-8")
    )
    runs = run_fixtures()
    assert set(runs) == {"known-good", "current", "degraded"}
    for run in runs.values():
        assert run["status"] == "done"
        assert run["dataset_checksum"] == thresholds["dataset_checksum"]
        assert len(run["results"]) == run["metrics"]["examples"]
    verdicts = {
        name: gate_for_run(
            EVAL_DIR, run["dataset_version"], run["metrics"], run["dataset_checksum"]
        )
        for name, run in runs.items()
    }
    assert {k: v.passed for k, v in verdicts.items() if v} == {
        "known-good": True,
        "current": True,
        "degraded": False,
    }


def test_committed_documents_match_the_samples() -> None:
    fixture = json.loads((DEMO_DIR / "documents.json").read_text(encoding="utf-8"))
    documents = {d["filename"]: d for d in fixture}
    samples = [p for p in SAMPLE_DOCS.glob("*.md") if p.name != "ATTRIBUTION.md"]
    assert set(documents) == {p.name for p in samples}
    for path in samples:
        # Normalised: Windows working copies are CRLF, CI is LF.
        content = path.read_bytes().replace(b"\r\n", b"\n")
        assert documents[path.name]["size_bytes"] == len(content)
        assert documents[path.name]["checksum"] == hashlib.sha256(content).hexdigest()


def test_recorded_answers_exist_in_their_run() -> None:
    recorded = json.loads(
        (DEMO_DIR / "recorded_answers.json").read_text(encoding="utf-8")
    )
    results = run_fixtures()[recorded["run"]]["results"]
    ids = {r["example_id"]: r for r in results}
    assert set(recorded["examples"]) <= set(ids)
    assert all(ids[e]["answer"] for e in recorded["examples"])


def test_fixtures_hold_no_local_paths() -> None:
    for path in DEMO_DIR.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for fragment in ("artifact_path", "C:\\\\", "/app/data", "/home/"):
            assert fragment not in text, f"{path.name}: {fragment}"


def test_demo_results_are_stored_with_the_run(
    demo: TestClient, sessions: sessionmaker[Session]
) -> None:
    demo.post(LOAD)
    with sessions() as session:
        assert len(session.scalars(select(EvaluationResult)).all()) == 207
