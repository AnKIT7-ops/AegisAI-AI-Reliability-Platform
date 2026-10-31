"""Each incident fixture gives one deterministic, evidence-backed report."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import metrics
from app.services.incidents.diagnosis import (
    CHAT_P95,
    ERROR_RATIO,
    MODEL_FAILURES,
    MODEL_P95,
    RETRIEVAL_P95,
    RUNBOOKS,
    UNKNOWN,
    LogEvent,
    Report,
    RunSignal,
    Signals,
    diagnose,
    markdown_report,
)

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "data" / "incidents"
UI = "http://prom.test"
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def fixture(name: str) -> Signals:
    return Signals.model_validate_json((FIXTURES / f"{name}.json").read_text("utf-8"))


def one(signals: Signals) -> Report:
    reports = diagnose(signals, UI)
    assert len(reports) == 1, [r.type for r in reports]
    return reports[0]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("model_timeout", ["model_timeout"]),
        ("model_unavailable", ["model_unavailable"]),
        ("increased_latency", ["increased_latency"]),
        ("elevated_api_error_rate", ["model_unavailable", "elevated_api_error_rate"]),
        ("retrieval_quality_regression", ["retrieval_quality_regression"]),
        ("unhealthy_dependency", ["unhealthy_dependency"]),
        ("unexplained_error_rate", ["elevated_api_error_rate"]),
    ],
)
def test_fixture_gives_the_expected_incident(name: str, expected: list[str]) -> None:
    signals = fixture(name)
    reports = diagnose(signals, UI)
    assert [r.type for r in reports] == expected
    assert reports == diagnose(signals, UI)  # deterministic
    for r in reports:
        assert r.facts and r.uncertainty and r.suggested_actions
        assert r.runbook == RUNBOOKS[r.type]


def test_all_fixtures_are_covered() -> None:
    assert len(list(FIXTURES.glob("*.json"))) == 7


def test_every_incident_type_has_an_existing_runbook() -> None:
    for path in RUNBOOKS.values():
        assert (ROOT / path).is_file(), path


@pytest.mark.parametrize(
    "promql", [ERROR_RATIO, MODEL_FAILURES, CHAT_P95, MODEL_P95, RETRIEVAL_P95]
)
def test_queries_use_exported_metrics(promql: str) -> None:
    exported = {
        m._name  # noqa: SLF001
        for m in vars(metrics).values()
        if hasattr(m, "_name") and hasattr(m, "_type")
    }
    names = {n.removesuffix("_bucket").removesuffix("_total") for n in _names(promql)}
    assert names <= exported, names - exported


def _names(text: str) -> set[str]:
    return set(re.findall(r"\baegis_[a-z0-9_]+", text))


def test_no_explanation_means_cause_unknown_not_a_guess() -> None:
    report = one(fixture("unexplained_error_rate"))
    assert report.hypotheses == []
    assert report.uncertainty.startswith(UNKNOWN)
    assert "12% of API requests" in report.facts[1].statement


def test_alert_with_no_failure_counts_is_unknown() -> None:
    report = one(Signals(observed_at=NOW, alerts=["AegisModelFailures"]))
    assert report.type == "model_unavailable"
    assert report.hypotheses == []
    assert report.uncertainty.startswith(UNKNOWN)


def test_quiet_signals_give_no_incidents() -> None:
    assert diagnose(Signals(observed_at=NOW), UI) == []


def test_one_outcome_is_high_confidence_several_are_not() -> None:
    alone = Signals(
        observed_at=NOW,
        alerts=["AegisModelFailures"],
        model_failures={"unreachable": 3},
    )
    assert one(alone).hypotheses[0].confidence == "high"
    mixed = alone.model_copy(
        update={"model_failures": {"unreachable": 3, "http_error": 1}}
    )
    assert {h.confidence for h in one(mixed).hypotheses} == {"medium"}


def test_timeouts_and_other_failures_give_two_incidents() -> None:
    signals = Signals(
        observed_at=NOW,
        alerts=["AegisModelFailures"],
        model_failures={"timeout": 2, "unreachable": 2},
    )
    assert [r.type for r in diagnose(signals, UI)] == [
        "model_timeout",
        "model_unavailable",
    ]


def test_events_are_correlated_by_request_id_and_time() -> None:
    old = NOW - timedelta(minutes=6)
    signals = Signals(
        observed_at=NOW,
        alerts=["AegisHighErrorRate"],
        log_events=[
            LogEvent(ts=old, request_id="stale", event="request", status=503),
            LogEvent(ts=NOW, request_id="r1", event="model_error", reason="injected"),
            LogEvent(ts=NOW, request_id="r1", event="request", status=503),
            LogEvent(ts=NOW, request_id="r2", event="request", status=503),
        ],
    )
    report = one(signals)
    logged = next(f for f in report.facts if f.source == "log")
    assert "2 request(s) returned 5xx" in logged.statement
    assert "stale" not in logged.statement
    assert logged.reference and '"request_id": "r1"' in logged.reference
    cause = report.hypotheses[0]
    assert cause.confidence == "high"
    assert "1 failed request(s)" in cause.basis


def test_a_crash_is_reported_with_its_request() -> None:
    signals = Signals(
        observed_at=NOW,
        alerts=["AegisHighErrorRate"],
        log_events=[
            LogEvent(ts=NOW, request_id="boom", event="error"),
            LogEvent(ts=NOW, request_id="boom", event="request", status=500),
        ],
    )
    report = one(signals)
    assert any("unhandled exception" in f.statement for f in report.facts)
    assert [h.cause for h in report.hypotheses] == [
        "An unexpected exception in the backend."
    ]


def test_fault_injection_is_named_as_fact_and_cause() -> None:
    latency = fixture("increased_latency").model_copy(update={"fault_delay_ms": 6000})
    report = one(latency)
    assert any(f.source == "config" and "6000" in f.statement for f in report.facts)
    assert report.hypotheses[0].confidence == "high"


@pytest.mark.parametrize(
    "chat, model, retrieval, cause",
    [
        (14.0, 13.0, 0.2, "The model is slow"),
        (10.0, 2.0, 6.0, "Retrieval is slow"),
    ],
)
def test_latency_cause_follows_the_slow_stage(
    chat: float, model: float, retrieval: float, cause: str
) -> None:
    signals = Signals(
        observed_at=NOW,
        alerts=["AegisChatLatencyHigh"],
        chat_p95_s=chat,
        model_p95_s=model,
        retrieval_p95_s=retrieval,
    )
    assert one(signals).hypotheses[0].cause.startswith(cause)


def test_latency_with_no_slow_stage_is_unknown() -> None:
    signals = Signals(
        observed_at=NOW,
        alerts=["AegisChatLatencyHigh"],
        chat_p95_s=12.0,
        model_p95_s=1.0,
        retrieval_p95_s=0.5,
    )
    assert one(signals).hypotheses == []


def test_regression_names_the_config_change_and_links_the_comparison() -> None:
    signals = fixture("retrieval_quality_regression")
    report = one(signals)
    change = report.facts[-1]
    assert "top_k 4 -> 1" in change.statement
    assert "min_score 0.7 -> 0.0" in change.statement
    assert signals.failed_run and signals.passing_run
    assert change.reference == (
        f"/api/v1/evaluations/{signals.failed_run.id}/report"
        f"?baseline={signals.passing_run.id}"
    )
    assert report.hypotheses[0].confidence == "medium"  # two fields changed


def _run(**changes: object) -> RunSignal:
    base = {
        "id": "a",
        "chat_model": "m",
        "embed_model": "e",
        "prompt_version": "2",
        "dataset_checksum": "c",
        "top_k": 4,
        "min_score": 0.7,
    }
    return RunSignal.model_validate(base | changes)


def test_regression_with_nothing_changed_is_unknown() -> None:
    signals = Signals(
        observed_at=NOW,
        failed_run=_run(id="b", gate_failures=["mrr: 0.5 < 0.9"]),
        passing_run=_run(),
    )
    report = one(signals)
    assert report.hypotheses == []
    assert report.uncertainty.startswith(UNKNOWN)
    assert "index" in report.uncertainty


def test_regression_without_a_passing_run_is_unknown() -> None:
    report = one(
        Signals(observed_at=NOW, failed_run=_run(gate_failures=["mrr: 0.5 < 0.9"]))
    )
    assert report.hypotheses == []
    assert "no passing run" in report.uncertainty


def test_failed_model_calls_during_a_run_are_a_cause() -> None:
    report = one(
        Signals(observed_at=NOW, failed_run=_run(gate_failures=["failure_rate: 1 > 0"]))
    )
    assert report.hypotheses[0].cause == "Model calls failed during the run."


def test_metric_facts_link_to_the_query() -> None:
    report = one(fixture("increased_latency"))
    metric = next(f for f in report.facts if f.source == "metric")
    assert metric.reference and metric.reference.startswith(f"{UI}/query?g0.expr=")


def test_markdown_separates_facts_from_hypotheses() -> None:
    text = markdown_report(one(fixture("model_unavailable")), "live")
    facts = text.index("## Observed facts")
    hypotheses = text.index("## Hypotheses (inferred, not confirmed)")
    assert facts < hypotheses < text.index("## Uncertainty")
    assert "Failed model calls" in text[facts:hypotheses]
    assert "Ollama is not running" in text[hypotheses:]
    assert "## Suggested actions (nothing has been run)" in text


def test_markdown_says_when_there_are_no_hypotheses() -> None:
    text = markdown_report(one(fixture("unexplained_error_rate")), "simulated")
    assert "not confirmed)\n\nNone.\n" in text
    assert "- Source: simulated\n" in text


def test_fixtures_are_valid_json_with_no_extra_keys() -> None:
    for path in FIXTURES.glob("*.json"):
        Signals.model_validate(json.loads(path.read_text("utf-8")))
