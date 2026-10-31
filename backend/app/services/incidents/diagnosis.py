"""Turn a snapshot of signals into incident reports.

Pure and deterministic: no I/O and no model. `facts` holds what was measured;
`hypotheses` holds inferences from those facts. A case no rule explains has no
hypotheses and says the cause is unknown, rather than guessing. Suggested actions
are fixed text for a person to read; nothing in this package can run them.
"""

from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

IncidentType = Literal[
    "model_timeout",
    "model_unavailable",
    "increased_latency",
    "elevated_api_error_rate",
    "retrieval_quality_regression",
    "unhealthy_dependency",
]
Confidence = Literal["high", "medium", "low"]
# Failures older than this are not evidence; it matches the alert windows.
WINDOW = timedelta(minutes=5)
# PromQL, shared with signals.py so a report links to the query that was measured.
ERROR_RATIO = (
    'sum(rate(aegis_http_requests_total{status=~"5..",route!~"/health.*|/metrics"}'
    '[5m])) / sum(rate(aegis_http_requests_total{route!~"/health.*|/metrics"}[5m]))'
)
MODEL_FAILURES = (
    'sum by (outcome) (increase(aegis_model_requests_total{outcome!="ok"}[5m]))'
)
CHAT_P95 = (
    "histogram_quantile(0.95, sum by (le) (rate("
    'aegis_http_request_duration_seconds_bucket{route="/api/v1/chat"}[5m])))'
)
MODEL_P95 = (
    "histogram_quantile(0.95, sum by (le) (rate("
    'aegis_model_request_duration_seconds_bucket{operation="chat"}[5m])))'
)
RETRIEVAL_P95 = (
    "histogram_quantile(0.95, sum by (le) "
    "(rate(aegis_retrieval_duration_seconds_bucket[5m])))"
)

RUNBOOKS: dict[IncidentType, str] = {
    "model_timeout": "docs/runbooks/model-timeout.md",
    "model_unavailable": "docs/runbooks/model-unavailable.md",
    "increased_latency": "docs/runbooks/latency-spike.md",
    "elevated_api_error_rate": "docs/runbooks/error-rate-spike.md",
    "retrieval_quality_regression": "docs/runbooks/evaluation-gate-failed.md",
    "unhealthy_dependency": "docs/runbooks/unhealthy-dependency.md",
}
SEVERITIES: dict[IncidentType, Literal["warning", "critical"]] = {
    "model_timeout": "warning",
    "model_unavailable": "critical",
    "increased_latency": "warning",
    "elevated_api_error_rate": "critical",
    "retrieval_quality_regression": "warning",
    "unhealthy_dependency": "critical",
}
ACTIONS: dict[IncidentType, list[str]] = {
    "model_timeout": [
        "Check whether the model is loaded and the GPU is free (ollama ps).",
        "Warm the model with one request before raising AEGIS_GENERATE_TIMEOUT_S.",
    ],
    "model_unavailable": [
        "Check Ollama: curl.exe http://127.0.0.1:11434/api/version",
        "Compare GET /api/v1/models with ollama list.",
    ],
    "increased_latency": [
        "Compare model and retrieval p95 on the AegisAI overview dashboard.",
        "Check that AEGIS_FAULT_DELAY_MS is 0 outside a drill.",
    ],
    "elevated_api_error_rate": [
        "Read the failing requests in the logs by request ID.",
        "Check that AEGIS_FAULT_ERROR_RATE is 0 outside a drill.",
    ],
    "retrieval_quality_regression": [
        "Read the comparison report against the last passing run.",
        "Restore the last passing configuration, then run the evaluation again.",
    ],
    "unhealthy_dependency": [
        "Check the database: docker compose ps postgres",
        "Read docker compose logs postgres --tail 50 before restarting anything.",
    ],
}
UNKNOWN = "Cause unknown: no collected signal explains this."


class LogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: datetime
    request_id: str | None = None
    event: str  # model_error, error, request
    status: int | None = None
    reason: str | None = None  # a ModelUnavailable reason


class RunSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    gate_failures: list[str] = []
    chat_model: str
    embed_model: str
    prompt_version: str
    dataset_checksum: str
    top_k: int
    min_score: float


class Signals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    alerts: list[str] = []  # firing Prometheus alerts
    model_failures: dict[str, float] = {}  # outcome -> failed calls in the window
    error_ratio: float | None = None
    chat_p95_s: float | None = None
    model_p95_s: float | None = None
    retrieval_p95_s: float | None = None
    database_ok: bool = True
    fault_delay_ms: int = 0
    fault_error_rate: float = 0.0
    failed_run: RunSignal | None = None  # the latest run, if it failed its gate
    passing_run: RunSignal | None = None  # the latest run that passed
    log_events: list[LogEvent] = []


class Fact(BaseModel):
    statement: str
    source: Literal["alert", "metric", "log", "evaluation", "health", "config"]
    reference: str | None = None  # a link, an API path or a log search to read


class Hypothesis(BaseModel):
    cause: str
    confidence: Confidence
    basis: str


class Report(BaseModel):
    type: IncidentType
    severity: Literal["warning", "critical"]
    symptom: str
    facts: list[Fact]
    hypotheses: list[Hypothesis]
    uncertainty: str
    runbook: str
    suggested_actions: list[str]


def _report(
    kind: IncidentType,
    symptom: str,
    facts: list[Fact],
    hypotheses: list[Hypothesis],
    uncertainty: str,
) -> Report:
    return Report(
        type=kind,
        severity=SEVERITIES[kind],
        symptom=symptom,
        facts=facts,
        hypotheses=hypotheses,
        uncertainty=uncertainty if hypotheses else f"{UNKNOWN} {uncertainty}".strip(),
        runbook=RUNBOOKS[kind],
        suggested_actions=ACTIONS[kind],
    )


def _query_link(prometheus_url: str, promql: str) -> str:
    return f"{prometheus_url}/query?g0.expr={quote(promql, safe='')}"


def _alert_fact(name: str, prometheus_url: str) -> Fact:
    return Fact(
        statement=f"Alert {name} is firing.",
        source="alert",
        reference=f"{prometheus_url}/alerts",
    )


def _recent(s: Signals) -> list[LogEvent]:
    return [e for e in s.log_events if s.observed_at - e.ts <= WINDOW]


def _log_search(request_id: str) -> str:
    return (
        "docker compose logs --no-log-prefix backend | "
        f'Select-String \'"request_id": "{request_id}"\''
    )


def _request_fact(events: list[LogEvent], what: str) -> Fact | None:
    """Failures of one kind, correlated by request ID. IDs are in order of arrival."""
    ids = list(dict.fromkeys(e.request_id for e in events if e.request_id))
    if not ids:
        return None
    shown = ", ".join(ids[:5])
    return Fact(
        statement=f"{len(ids)} request(s) {what} in the last 5 minutes: {shown}.",
        source="log",
        reference=_log_search(ids[0]),
    )


def _model_failure_fact(outcomes: dict[str, float], prometheus_url: str) -> Fact:
    parts = ", ".join(f"{k} {round(v)}" for k, v in sorted(outcomes.items()))
    return Fact(
        statement=f"Failed model calls in the last 5 minutes: {parts}.",
        source="metric",
        reference=_query_link(prometheus_url, MODEL_FAILURES),
    )


def _model_timeout(s: Signals, prometheus_url: str) -> Report:
    count = s.model_failures["timeout"]
    facts = [
        _alert_fact("AegisModelFailures", prometheus_url),
        _model_failure_fact({"timeout": count}, prometheus_url),
    ]
    events = [e for e in _recent(s) if e.reason == "timeout"]
    if fact := _request_fact(events, "failed on a model timeout"):
        facts.append(fact)
    return _report(
        "model_timeout",
        "Calls to the model are timing out.",
        facts,
        [
            Hypothesis(
                cause="The model is loading or the GPU is busy.",
                confidence="medium",
                basis="A cold load has taken 38 to 79 s here.",
            ),
            Hypothesis(
                cause="Nothing is answering at the configured Ollama URL.",
                confidence="medium",
                basis="A connection timeout counts as a timeout too; these calls "
                "fail after about 6 s, generation after 120 s.",
            ),
            Hypothesis(
                cause="AEGIS_GENERATE_TIMEOUT_S is too low for this model.",
                confidence="low",
                basis="Only worth checking if the timeouts persist on a warm model.",
            ),
        ],
        "The metrics do not separate a connection timeout from a generation "
        "timeout; the failed requests' durations in the logs do.",
    )


UNAVAILABLE_CAUSES = {
    "unreachable": "Ollama is not running or not listening on the configured URL.",
    "model_missing": "The configured model is not pulled.",
    "http_error": "Ollama answered with an error.",
    "bad_response": "Ollama answered with an unexpected body.",
    "injected": "A fault injection drill is failing the calls.",
}


def _model_unavailable(s: Signals, prometheus_url: str) -> Report:
    outcomes = {k: v for k, v in s.model_failures.items() if k != "timeout"}
    facts = [_alert_fact("AegisModelFailures", prometheus_url)]
    if outcomes:
        facts.append(_model_failure_fact(outcomes, prometheus_url))
    if s.fault_error_rate:
        facts.append(
            Fact(
                statement=f"AEGIS_FAULT_ERROR_RATE is {s.fault_error_rate}.",
                source="config",
            )
        )
    events = [e for e in _recent(s) if e.reason and e.reason != "timeout"]
    if fact := _request_fact(events, "failed on a model error"):
        facts.append(fact)
    # One outcome names its cause; several at once may share one nobody measured.
    known = [k for k in outcomes if k in UNAVAILABLE_CAUSES]
    confidence: Confidence = "high" if len(outcomes) == 1 else "medium"
    return _report(
        "model_unavailable",
        "The model provider is failing calls.",
        facts,
        [
            Hypothesis(
                cause=UNAVAILABLE_CAUSES[k],
                confidence=confidence,
                basis=f"Model calls failed with outcome {k}.",
            )
            for k in sorted(known)
        ],
        "A failed call shows the outcome, not why Ollama reached that state.",
    )


def _latency(s: Signals, prometheus_url: str) -> Report:
    facts = [_alert_fact("AegisChatLatencyHigh", prometheus_url)]
    for label, value, promql in (
        ("Chat p95", s.chat_p95_s, CHAT_P95),
        ("Model call p95", s.model_p95_s, MODEL_P95),
        ("Retrieval p95", s.retrieval_p95_s, RETRIEVAL_P95),
    ):
        if value is not None:
            facts.append(
                Fact(
                    statement=f"{label} is {value:.2f} s over 5 minutes.",
                    source="metric",
                    reference=_query_link(prometheus_url, promql),
                )
            )
    hypotheses = []
    chat, model, retrieval = s.chat_p95_s, s.model_p95_s, s.retrieval_p95_s
    if s.fault_delay_ms:
        facts.append(
            Fact(
                statement=f"AEGIS_FAULT_DELAY_MS is {s.fault_delay_ms}.",
                source="config",
            )
        )
        hypotheses.append(
            Hypothesis(
                cause="A fault injection drill is delaying model calls.",
                confidence="high",
                basis="The delay setting is on and adds to every model call.",
            )
        )
    elif chat and model and model >= 0.7 * chat:
        hypotheses.append(
            Hypothesis(
                cause="The model is slow: cold load or a busy GPU.",
                confidence="medium",
                basis="Model calls account for most of the chat p95.",
            )
        )
    elif chat and retrieval and retrieval >= 0.5 * chat:
        hypotheses.append(
            Hypothesis(
                cause="Retrieval is slow: query embedding or vector search.",
                confidence="medium",
                basis="Retrieval accounts for at least half of the chat p95.",
            )
        )
    return _report(
        "increased_latency",
        "Chat responses are slower than the alert limit.",
        facts,
        hypotheses,
        "Percentiles of separate histograms do not add up exactly.",
    )


def _error_rate(s: Signals, prometheus_url: str) -> Report:
    facts = [_alert_fact("AegisHighErrorRate", prometheus_url)]
    if s.error_ratio is not None:
        facts.append(
            Fact(
                statement=f"{s.error_ratio:.0%} of API requests returned 5xx "
                "over 5 minutes.",
                source="metric",
                reference=_query_link(prometheus_url, ERROR_RATIO),
            )
        )
    recent = _recent(s)
    failed = [e for e in recent if e.event == "request"]
    crashes = [e for e in recent if e.event == "error"]
    model_ids = {e.request_id for e in recent if e.event == "model_error"}
    if fact := _request_fact(failed, "returned 5xx"):
        facts.append(fact)

    hypotheses = []
    if s.model_failures or model_ids:
        matched = {e.request_id for e in failed} & model_ids
        hypotheses.append(
            Hypothesis(
                cause="Model failures are causing the 5xx responses.",
                # Same request ID on both lines is correlation, not a timing guess.
                confidence="high" if matched else "medium",
                basis=f"{len(matched)} failed request(s) logged a model error."
                if matched
                else "Model calls failed in the same window.",
            )
        )
    if crashes:
        if fact := _request_fact(crashes, "raised an unhandled exception"):
            facts.append(fact)
        hypotheses.append(
            Hypothesis(
                cause="An unexpected exception in the backend.",
                confidence="high",
                basis="The stack trace is logged under the request ID.",
            )
        )
    if not s.database_ok:
        hypotheses.append(
            Hypothesis(
                cause="The database is down.",
                confidence="medium",
                basis="The readiness check failed in the same window.",
            )
        )
    return _report(
        "elevated_api_error_rate",
        "More than 5% of API requests are failing.",
        facts,
        hypotheses,
        "The buffered log events cover this process since its last restart only.",
    )


def _dependency(_: Signals, __: str) -> Report:
    return _report(
        "unhealthy_dependency",
        "PostgreSQL is not reachable.",
        [
            Fact(
                statement="SELECT 1 on the database failed during detection.",
                source="health",
                reference="/health/ready",
            )
        ],
        [
            Hypothesis(
                cause="The postgres container is stopped or restarting.",
                confidence="medium",
                basis="The usual cause here; the check cannot see the container.",
            ),
            Hypothesis(
                cause="The password or address in DATABASE_URL is wrong.",
                confidence="low",
                basis="Would give the same failure; recent changes would show it.",
            ),
        ],
        "The check reports that the database failed, not why. Evaluation history "
        "could not be read, so regressions were not checked.",
    )


RUN_FIELDS = (
    "chat_model",
    "embed_model",
    "prompt_version",
    "dataset_checksum",
    "top_k",
    "min_score",
)


def _regression(s: Signals, _: str) -> Report:
    run = s.failed_run
    assert run is not None  # checked by diagnose()
    base = s.passing_run
    link = f"/api/v1/evaluations/{run.id}/report"
    facts = [
        Fact(
            statement=f"Evaluation run {run.id} failed its gate: {failure}.",
            source="evaluation",
            reference=link,
        )
        for failure in run.gate_failures
    ]
    hypotheses = []
    uncertainty = "There is no passing run to compare with."
    if base is not None:
        link = f"{link}?baseline={base.id}"
        changes = [
            f"{field} {getattr(base, field)} -> {getattr(run, field)}"
            for field in RUN_FIELDS
            if getattr(base, field) != getattr(run, field)
        ]
        uncertainty = (
            "Nothing recorded differs from the last passing run. The document "
            "index and the model weights are not recorded with a run."
        )
        if changes:
            joined = ", ".join(changes)
            facts.append(
                Fact(
                    statement=f"Changed since passing run {base.id}: {joined}.",
                    source="evaluation",
                    reference=link,
                )
            )
            hypotheses.append(
                Hypothesis(
                    cause="The configuration change lowered quality.",
                    confidence="high" if len(changes) == 1 else "medium",
                    basis=f"The only recorded differences are: {joined}.",
                )
            )
            uncertainty = "The document index is not recorded with a run."
    if any(f.startswith("failure_rate") for f in run.gate_failures):
        hypotheses.append(
            Hypothesis(
                cause="Model calls failed during the run.",
                confidence="high",
                basis="failure_rate is above its limit.",
            )
        )
    return _report(
        "retrieval_quality_regression",
        "The latest evaluation run failed its quality gate.",
        facts,
        hypotheses,
        uncertainty,
    )


def diagnose(s: Signals, prometheus_url: str) -> list[Report]:
    """Every incident the snapshot shows, in a fixed order."""
    reports = []
    if not s.database_ok:
        reports.append(_dependency(s, prometheus_url))
    if "AegisModelFailures" in s.alerts:
        if s.model_failures.get("timeout", 0) >= 1:
            reports.append(_model_timeout(s, prometheus_url))
        if not s.model_failures or set(s.model_failures) != {"timeout"}:
            reports.append(_model_unavailable(s, prometheus_url))
    if "AegisHighErrorRate" in s.alerts:
        reports.append(_error_rate(s, prometheus_url))
    if "AegisChatLatencyHigh" in s.alerts:
        reports.append(_latency(s, prometheus_url))
    if s.failed_run is not None:
        reports.append(_regression(s, prometheus_url))
    return reports


def markdown_report(report: Report, source: str) -> str:
    lines = [
        f"# Incident: {report.type}",
        "",
        f"- Source: {source}",
        f"- Severity: {report.severity}",
        f"- Symptom: {report.symptom}",
        f"- Runbook: {report.runbook}",
        "",
        "## Observed facts",
        "",
    ]
    for fact in report.facts:
        ref = f" (`{fact.reference}`)" if fact.reference else ""
        lines.append(f"- [{fact.source}] {fact.statement}{ref}")
    lines += ["", "## Hypotheses (inferred, not confirmed)", ""]
    lines += [
        f"- {h.confidence} confidence: {h.cause} {h.basis}" for h in report.hypotheses
    ] or ["None."]
    lines += ["", "## Uncertainty", "", report.uncertainty]
    lines += ["", "## Suggested actions (nothing has been run)", ""]
    lines += [f"- {a}" for a in report.suggested_actions]
    return "\n".join(lines) + "\n"
