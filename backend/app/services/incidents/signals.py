"""Collect a snapshot of live signals for diagnosis.

Thresholds live in the Prometheus alert rules: an alert firing is the breach, so
limits are defined once. What could not be read is returned by name, so an empty
result is never mistaken for a healthy system.
"""

import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import EvaluationRun
from app.services.evaluation.report import gate_for_run
from app.services.incidents.diagnosis import (
    CHAT_P95,
    ERROR_RATIO,
    MODEL_FAILURES,
    MODEL_P95,
    RETRIEVAL_P95,
    LogEvent,
    RunSignal,
    Signals,
)

# Finding the last passing run looks this far back.
RUN_HISTORY = 20


class Prometheus:
    def __init__(
        self, base_url: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=2.0, transport=transport)

    def _data(self, path: str, **params: str) -> Any:
        response = self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()["data"]

    def firing_alerts(self) -> list[str]:
        alerts = self._data("/api/v1/alerts")["alerts"]
        return sorted(
            {a["labels"]["alertname"] for a in alerts if a["state"] == "firing"}
        )

    def by_label(self, promql: str, label: str) -> dict[str, float]:
        result = self._data("/api/v1/query", query=promql)["result"]
        values = {s["metric"].get(label, ""): float(s["value"][1]) for s in result}
        return {k: v for k, v in values.items() if math.isfinite(v)}

    def value(self, promql: str) -> float | None:
        # Empty or NaN means no traffic in the window, not zero.
        return next(iter(self.by_label(promql, "").values()), None)


def _metrics(prometheus: Prometheus) -> dict[str, Any]:
    failures = prometheus.by_label(MODEL_FAILURES, "outcome")
    return {
        "alerts": prometheus.firing_alerts(),
        # increase() extrapolates, so under half a call means none happened.
        "model_failures": {k: v for k, v in failures.items() if v >= 0.5},
        "error_ratio": prometheus.value(ERROR_RATIO),
        "chat_p95_s": prometheus.value(CHAT_P95),
        "model_p95_s": prometheus.value(MODEL_P95),
        "retrieval_p95_s": prometheus.value(RETRIEVAL_P95),
    }


def _run_signal(run: EvaluationRun, failures: list[str]) -> RunSignal:
    return RunSignal(
        id=str(run.id),
        gate_failures=failures,
        chat_model=run.chat_model,
        embed_model=run.embed_model,
        prompt_version=run.prompt_version,
        dataset_checksum=run.dataset_checksum,
        top_k=run.top_k,
        min_score=run.min_score,
    )


def _runs(session: Session, settings: Settings) -> dict[str, RunSignal]:
    runs = session.scalars(
        select(EvaluationRun)
        # Demo runs include a deliberate failure, which is not an incident.
        .where(EvaluationRun.status == "done", EvaluationRun.source == "live")
        .order_by(EvaluationRun.finished_at.desc())
        .limit(RUN_HISTORY)
    ).all()
    gates = [
        gate_for_run(
            settings.eval_dir, r.dataset_version, r.metrics or {}, r.dataset_checksum
        )
        for r in runs
    ]
    if not gates or gates[0] is None or gates[0].passed:
        return {}
    found = {"failed_run": _run_signal(runs[0], gates[0].failures)}
    for run, gate in zip(runs, gates, strict=True):
        if gate is not None and gate.passed:
            found["passing_run"] = _run_signal(run, [])
            break
    return found


def collect(
    prometheus: Prometheus,
    sessions: sessionmaker[Session],
    settings: Settings,
    events: Iterable[dict[str, Any]],
    now: datetime,
) -> tuple[Signals, list[str]]:
    """The snapshot, and the names of the signals that could not be read."""
    fields: dict[str, Any] = {}
    not_checked = []
    try:
        fields |= _metrics(prometheus)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        not_checked += ["alerts", "metrics"]
    try:
        with sessions() as session:
            session.execute(text("SELECT 1"))
            fields |= _runs(session, settings)
    except OperationalError:
        fields["database_ok"] = False
        not_checked.append("evaluation_runs")
    signals = Signals(
        observed_at=now,
        fault_delay_ms=settings.fault_delay_ms,
        fault_error_rate=settings.fault_error_rate,
        log_events=[LogEvent(**e) for e in events],
        **fields,
    )
    return signals, not_checked
