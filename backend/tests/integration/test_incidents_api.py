"""Incident detection, scenarios and reports through the API."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_embedder, get_prometheus, get_sessionmaker
from app.core.config import Settings
from app.core.logs import RECENT_FAILURES, RecentFailures
from app.db.models import EvaluationRun
from app.main import app
from app.services.evaluation.report import Thresholds
from app.services.incidents.signals import Prometheus
from app.services.llm.base import ModelUnavailable

DETECT = "/api/v1/incidents/detect"
SIMULATE = "/api/v1/incidents/simulate"


def prometheus(
    alerts: list[str] | None = None,
    series: dict[str, list[dict[str, Any]]] | None = None,
) -> Prometheus:
    """A Prometheus that answers from memory; series are keyed by a query fragment."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/alerts":
            firing = [
                {"labels": {"alertname": a}, "state": "firing"} for a in alerts or []
            ]
            pending = {"labels": {"alertname": "Pending"}, "state": "pending"}
            return httpx.Response(200, json={"data": {"alerts": [*firing, pending]}})
        query = request.url.params["query"]
        result = next((v for k, v in (series or {}).items() if k in query), [])
        return httpx.Response(200, json={"data": {"result": result}})

    return Prometheus("http://prom.test", transport=httpx.MockTransport(answer))


def down() -> Prometheus:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    return Prometheus("http://prom.test", transport=httpx.MockTransport(refuse))


@pytest.fixture(autouse=True)
def clean_buffer() -> Iterator[None]:
    RECENT_FAILURES.events.clear()
    yield
    RECENT_FAILURES.events.clear()
    app.dependency_overrides.pop(get_prometheus, None)


def use(prom: Prometheus) -> None:
    app.dependency_overrides[get_prometheus] = lambda: prom


def add_run(
    sessions: sessionmaker[Session], settings: Settings, recall: float, top_k: int = 4
) -> EvaluationRun:
    with sessions() as session:
        run = EvaluationRun(
            status="done",
            label="x",
            dataset_version="v1",
            dataset_checksum="abc",
            prompt_version="2",
            chat_model="m",
            embed_model="e",
            top_k=top_k,
            min_score=0.7,
            config={},
            metrics={"recall_at_k": recall},
            finished_at=None,
        )
        session.add(run)
        session.commit()
        return run


def thresholds(settings: Settings) -> None:
    limits = Thresholds(
        dataset_version="v1",
        dataset_checksum="abc",
        basis="test",
        limits={"recall_at_k": 0.9},
    )
    (settings.eval_dir / "v1.thresholds.json").write_text(
        limits.model_dump_json(), encoding="utf-8"
    )


def test_quiet_system_has_no_incidents(api: TestClient) -> None:
    use(prometheus())
    body = api.post(DETECT).json()
    assert body["incidents"] == []
    assert body["not_checked"] == []
    assert body["persisted"] is True


def test_pending_alerts_are_not_breaches(api: TestClient) -> None:
    assert prometheus().firing_alerts() == []


def test_detect_creates_a_live_incident_with_evidence(api: TestClient) -> None:
    use(
        prometheus(
            ["AegisModelFailures"],
            {
                "aegis_model_requests_total": [
                    {"metric": {"outcome": "unreachable"}, "value": [0, "6.2"]},
                    {"metric": {"outcome": "timeout"}, "value": [0, "0.1"]},
                ]
            },
        )
    )
    body = api.post(DETECT).json()
    assert [i["type"] for i in body["incidents"]] == ["model_unavailable"]
    incident = body["incidents"][0]
    assert incident["source"] == "live"
    assert incident["status"] == "open"
    statements = [f["statement"] for f in incident["report"]["facts"]]
    assert "Failed model calls in the last 5 minutes: unreachable 6." in statements
    assert incident["report"]["hypotheses"][0]["confidence"] == "high"


def test_a_real_failed_request_is_correlated_by_request_id(
    api: TestClient, settings: Settings
) -> None:
    class Broken:
        model = "fake-embed"

        def embed(self, texts: list[str]) -> list[list[float]]:
            raise ModelUnavailable("Ollama is not reachable", "unreachable")

    app.dependency_overrides[get_embedder] = Broken
    r = api.post(
        "/api/v1/chat",
        json={"question": "What is a probe?"},
        headers={"X-Request-ID": "drill-1"},
    )
    assert r.status_code == 503
    use(prometheus(["AegisHighErrorRate"]))
    incident = api.post(DETECT).json()["incidents"][0]
    assert incident["type"] == "elevated_api_error_rate"
    log_fact = next(f for f in incident["report"]["facts"] if f["source"] == "log")
    assert "drill-1" in log_fact["statement"]
    assert "drill-1" in log_fact["reference"]
    assert incident["report"]["hypotheses"][0]["confidence"] == "high"


def test_repeat_detection_updates_the_open_incident(api: TestClient) -> None:
    use(prometheus(["AegisHighErrorRate"]))
    first = api.post(DETECT).json()["incidents"][0]
    second = api.post(DETECT).json()["incidents"][0]
    assert second["id"] == first["id"]
    assert second["occurrences"] == 2
    assert len(api.get("/api/v1/incidents").json()["incidents"]) == 1


def test_resolved_incident_is_not_reopened(api: TestClient) -> None:
    use(prometheus(["AegisHighErrorRate"]))
    first = api.post(DETECT).json()["incidents"][0]
    resolved = api.post(f"/api/v1/incidents/{first['id']}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"]
    again = api.post(f"/api/v1/incidents/{first['id']}/resolve")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "incident_resolved"
    assert api.post(DETECT).json()["incidents"][0]["id"] != first["id"]


def test_prometheus_down_is_reported_not_read_as_healthy(api: TestClient) -> None:
    use(down())
    body = api.post(DETECT).json()
    assert body["incidents"] == []
    assert body["not_checked"] == ["alerts", "metrics"]


def test_malformed_prometheus_reply_is_not_checked(api: TestClient) -> None:
    bad = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": {}}))
    use(Prometheus("http://prom.test", transport=bad))
    assert api.post(DETECT).json()["not_checked"] == ["alerts", "metrics"]


def test_nan_percentile_is_no_data() -> None:
    nan = [{"metric": {}, "value": [0, "NaN"]}]
    assert prometheus(series={"quantile": nan}).value("histogram_quantile") is None


def test_database_down_still_reports_but_saves_nothing(
    api: TestClient, tmp_path: Path
) -> None:
    missing = create_engine(f"sqlite:///{tmp_path / 'no' / 'such' / 'x.db'}")
    app.dependency_overrides[get_sessionmaker] = lambda: sessionmaker(missing)
    use(prometheus())
    r = api.post(DETECT)
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is False
    assert body["not_checked"] == ["evaluation_runs"]
    assert [i["type"] for i in body["incidents"]] == ["unhealthy_dependency"]
    assert body["incidents"][0]["id"] is None


def test_failed_run_is_a_regression_compared_with_the_passing_one(
    api: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    thresholds(settings)
    now = datetime.now(UTC)
    good = add_run(sessions, settings, 0.98)
    bad = add_run(sessions, settings, 0.5, top_k=1)
    with sessions() as session:
        session.merge(good).finished_at = now - timedelta(hours=1)
        session.merge(bad).finished_at = now
        session.commit()
    use(prometheus())
    incident = api.post(DETECT).json()["incidents"][0]
    assert incident["type"] == "retrieval_quality_regression"
    facts = incident["report"]["facts"]
    assert "recall_at_k: 0.5 < 0.9" in facts[0]["statement"]
    assert "top_k 4 -> 1" in facts[-1]["statement"]
    assert facts[-1]["reference"].endswith(f"report?baseline={good.id}")


def test_passing_latest_run_is_no_regression(
    api: TestClient, sessions: sessionmaker[Session], settings: Settings
) -> None:
    thresholds(settings)
    add_run(sessions, settings, 0.98)
    use(prometheus())
    assert api.post(DETECT).json()["incidents"] == []


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("model_timeout", ["model_timeout"]),
        ("unhealthy_dependency", ["unhealthy_dependency"]),
    ],
)
def test_simulate_replays_a_scenario(
    api: TestClient, scenario: str, expected: list[str]
) -> None:
    r = api.post(SIMULATE, json={"scenario": scenario})
    assert r.status_code == 201
    incidents = r.json()["incidents"]
    assert [i["type"] for i in incidents] == expected
    assert {i["source"] for i in incidents} == {"simulated"}


def test_simulated_and_live_incidents_stay_separate(api: TestClient) -> None:
    sim = api.post(SIMULATE, json={"scenario": "unexplained_error_rate"}).json()
    use(prometheus(["AegisHighErrorRate"]))
    live = api.post(DETECT).json()
    assert sim["incidents"][0]["id"] != live["incidents"][0]["id"]


@pytest.mark.parametrize("scenario", ["../secrets", "a/b", "UPPER", "", "a.json"])
def test_scenario_is_a_name_not_a_path(api: TestClient, scenario: str) -> None:
    r = api.post(SIMULATE, json={"scenario": scenario})
    assert r.status_code == 422


def test_unknown_scenario_is_404(api: TestClient) -> None:
    r = api.post(SIMULATE, json={"scenario": "nope"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_report_and_filters(api: TestClient) -> None:
    created = api.post(SIMULATE, json={"scenario": "model_unavailable"}).json()
    incident_id = created["incidents"][0]["id"]
    assert api.get(f"/api/v1/incidents/{incident_id}").json()["id"] == incident_id
    report = api.get(f"/api/v1/incidents/{incident_id}/report")
    assert report.headers["content-type"].startswith("text/markdown")
    assert "- Source: simulated" in report.text
    assert "## Hypotheses (inferred, not confirmed)" in report.text
    assert len(api.get("/api/v1/incidents?status=open").json()["incidents"]) == 1
    assert api.get("/api/v1/incidents?status=resolved").json()["incidents"] == []


def test_unknown_incident_is_404(api: TestClient) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    assert api.get(f"/api/v1/incidents/{missing}").status_code == 404
    assert api.post(f"/api/v1/incidents/{missing}/resolve").status_code == 404


def test_snapshot_waits_for_a_write_in_progress() -> None:
    failures = RecentFailures()
    done = threading.Event()

    def read() -> None:
        failures.snapshot()
        done.set()

    failures.acquire()  # what logging holds around emit()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    assert not done.wait(0.1)
    failures.release()
    assert done.wait(2)


def test_client_errors_are_not_buffered_as_failures(api: TestClient) -> None:
    api.get("/api/v1/nope")
    assert not RECENT_FAILURES.events


def test_buffered_failures_hold_no_free_text(api: TestClient) -> None:
    class Secretive:
        model = "fake-embed"

        def embed(self, texts: list[str]) -> list[list[float]]:
            raise ModelUnavailable("password=hunter2", "http_error")

    app.dependency_overrides[get_embedder] = Secretive
    assert api.post("/api/v1/chat", json={"question": "hi"}).status_code == 503
    assert RECENT_FAILURES.events
    assert "hunter2" not in str(list(RECENT_FAILURES.events))
