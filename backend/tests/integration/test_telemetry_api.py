"""The telemetry summary the Overview and Telemetry pages read."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_sessionmaker
from app.core.config import Settings
from app.main import app
from app.services.incidents.diagnosis import (
    CHAT_P95,
    ERROR_RATIO,
    MODEL_FAILURES,
    MODEL_P95,
    REQUEST_RATE,
    RETRIEVAL_P95,
)
from tests.integration.test_incidents_api import down, prometheus, use

TELEMETRY = "/api/v1/telemetry"


def value(number: float) -> list[dict[str, Any]]:
    return [{"metric": {}, "value": [0, str(number)]}]


def healthy() -> Any:
    return prometheus(
        alerts=["AegisChatLatencyHigh"],
        series={
            ERROR_RATIO: value(0.02),
            REQUEST_RATE: value(1.5),
            CHAT_P95: value(2.4),
            MODEL_P95: value(2.1),
            RETRIEVAL_P95: value(0.05),
            MODEL_FAILURES: [{"metric": {"outcome": "timeout"}, "value": [0, "3"]}],
        },
    )


def test_summary(api: TestClient, settings: Settings) -> None:
    use(healthy())
    r = api.get(TELEMETRY)
    assert r.status_code == 200
    body = r.json()
    assert body["observed_at"]
    assert body["database"] == "ok"
    assert body["not_checked"] == []
    assert body["firing_alerts"] == ["AegisChatLatencyHigh"]
    assert body["request_rate_per_s"] == 1.5
    assert body["error_ratio"] == 0.02
    assert body["chat_p95_s"] == 2.4
    assert body["model_p95_s"] == 2.1
    assert body["retrieval_p95_s"] == 0.05
    assert body["model_failures"] == {"timeout": 3}
    assert body["prometheus_url"] == settings.prometheus_public_url
    assert body["grafana_url"] == settings.grafana_public_url


def test_no_traffic_is_null_not_zero(api: TestClient) -> None:
    use(prometheus())
    body = api.get(TELEMETRY).json()
    assert body["not_checked"] == []
    assert body["firing_alerts"] == []
    assert body["model_failures"] == {}
    for name in ("request_rate_per_s", "error_ratio", "chat_p95_s"):
        assert body[name] is None


def test_prometheus_down_reports_not_checked(api: TestClient) -> None:
    use(down())
    r = api.get(TELEMETRY)
    assert r.status_code == 200
    body = r.json()
    assert body["not_checked"] == ["alerts", "metrics"]
    assert body["firing_alerts"] is None
    assert body["model_failures"] is None
    assert body["error_ratio"] is None
    assert body["database"] == "ok"


@pytest.fixture
def no_database(tmp_path: Path) -> sessionmaker[Session]:
    # A directory is not a database file, so connecting raises OperationalError.
    return sessionmaker(create_engine(f"sqlite:///{tmp_path}"))


def test_database_down_is_reported(
    api: TestClient, no_database: sessionmaker[Session]
) -> None:
    use(healthy())
    app.dependency_overrides[get_sessionmaker] = lambda: no_database
    r = api.get(TELEMETRY)
    assert r.status_code == 200
    assert r.json()["database"] == "unavailable"
    assert r.json()["error_ratio"] == 0.02
