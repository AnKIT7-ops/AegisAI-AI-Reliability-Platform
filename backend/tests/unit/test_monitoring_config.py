"""The dashboard, alert rules and runbooks refer only to things that exist."""

import json
import re
from pathlib import Path

import pytest

from app.core import metrics

ROOT = Path(__file__).parents[3]
MONITORING = ROOT / "monitoring"
DASHBOARD = MONITORING / "grafana" / "dashboards" / "aegis-overview.json"
ALERTS = MONITORING / "prometheus" / "alerts.yml"

# Names the app exports, including the series Prometheus derives from them.
EXPORTED = {
    m._name + suffix  # noqa: SLF001
    for m in vars(metrics).values()
    if hasattr(m, "_name") and hasattr(m, "_type")
    for suffix in (
        ("_bucket", "_count", "_sum")
        if m._type == "histogram"  # noqa: SLF001
        else ("_total",)
        if m._type == "counter"  # noqa: SLF001
        else ("",)
    )
}


def referenced(text: str) -> set[str]:
    return set(re.findall(r"\baegis_[a-z0-9_]+", text))


def test_the_app_exports_the_expected_metrics() -> None:
    assert "aegis_http_requests_total" in EXPORTED
    assert "aegis_http_request_duration_seconds_bucket" in EXPORTED
    assert "aegis_evaluation_last_gate_passed" in EXPORTED


def test_dashboard_queries_use_exported_metrics() -> None:
    names = referenced(DASHBOARD.read_text(encoding="utf-8"))
    assert names, "the dashboard queries no metrics"
    assert names <= EXPORTED, names - EXPORTED


def test_alert_rules_use_exported_metrics() -> None:
    names = referenced(ALERTS.read_text(encoding="utf-8"))
    assert names <= EXPORTED, names - EXPORTED


def test_every_panel_has_a_unit_a_description_and_the_provisioned_source() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    provisioned = (
        MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    ).read_text(encoding="utf-8")
    uid = re.search(r"uid:\s*(\S+)", provisioned)
    assert uid
    panels = [p for p in dashboard["panels"] if p["type"] != "row"]
    assert panels
    for p in panels:
        assert p["datasource"]["uid"] == uid.group(1), p["title"]
        assert p["fieldConfig"]["defaults"]["unit"], p["title"]
        assert p["description"], p["title"]
        assert all(t["datasource"]["uid"] == uid.group(1) for t in p["targets"])


@pytest.mark.parametrize(
    "path",
    sorted(set(re.findall(r"runbook: (\S+)", ALERTS.read_text(encoding="utf-8")))),
)
def test_alert_runbooks_exist(path: str) -> None:
    assert (ROOT / path).is_file()


def test_gate_gauge_starts_as_not_gated() -> None:
    # A fresh interpreter: what a newly started backend exports before any run.
    import subprocess
    import sys

    code = (
        "from prometheus_client import REGISTRY; import app.core.metrics; "
        "print(REGISTRY.get_sample_value('aegis_evaluation_last_gate_passed'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    assert out == "nan"
