import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "benchmark.py"


def load() -> Any:
    spec = importlib.util.spec_from_file_location("benchmark", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_percentile_is_nearest_rank() -> None:
    benchmark = load()
    values = [float(n) for n in range(1, 21)]
    assert benchmark.percentile(values, 50) == 10
    assert benchmark.percentile(values, 95) == 19
    assert benchmark.percentile([], 95) is None


def test_summary_keeps_failures_out_of_latency() -> None:
    summary = load().summarize(
        "chat", 2, [(200, 10.0), (200, 30.0), (429, 1.0), (503, 900.0), (0, 5.0)], 2.0
    )
    assert summary["ok"] == 2
    assert summary["rate_limited"] == 1
    assert summary["failed"] == 2
    assert summary["failure_rate"] == 0.6
    assert summary["throughput_per_s"] == 1.0
    assert summary["max_ms"] == 30.0
    assert summary["latencies_ms"] == [10.0, 30.0]


def test_wrong_outcome_is_counted_apart_and_left_out_of_latency() -> None:
    benchmark = load()
    summary = benchmark.summarize(
        "chat", 1, [(200, 10.0), (benchmark.WRONG_OUTCOME, 50.0)], 1.0
    )
    assert summary["ok"] == 1
    assert summary["wrong_outcome"] == 1
    assert summary["failed"] == 0
    assert summary["latencies_ms"] == [10.0]


def test_outcome_checks_the_reply_only_when_asked() -> None:
    benchmark = load()
    answered = b'{"sources": [{"n": 1}]}'
    refused = b'{"sources": []}'
    wrong = benchmark.WRONG_OUTCOME
    assert benchmark.outcome(200, answered, True) == 200
    assert benchmark.outcome(200, refused, False) == 200
    assert benchmark.outcome(200, refused, True) == wrong
    assert benchmark.outcome(200, answered, False) == wrong
    assert benchmark.outcome(200, b"not json", True) == wrong
    assert benchmark.outcome(200, b"[]", True) == wrong
    assert benchmark.outcome(200, b"not json", None) == 200
    assert benchmark.outcome(503, b"", True) == 503


def test_docker_memory_units() -> None:
    to_mib = load().to_mib
    assert to_mib("0B") == 0.0
    assert to_mib("512KiB") == 0.5
    assert to_mib("123.4MiB") == 123.4
    assert to_mib("1.5GiB") == 1536.0
    with pytest.raises(ValueError):
        to_mib("--")
    with pytest.raises(ValueError):
        to_mib("5PB")


def test_a_bad_sample_is_counted_and_does_not_stop_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = load()
    replies = {
        "ps": "backend=stack-backend-1",
        "stats": "stack-backend-1|--|1MiB / 2MiB",
    }
    monkeypatch.setattr(
        benchmark, "output", lambda *command: replies.get(command[1], "")
    )
    peaks = benchmark.Peaks("stack")
    peaks._try_sample()
    assert (peaks.samples, peaks.skipped) == (0, 1)

    replies["stats"] = "stack-backend-1|12.5%|1.5GiB / 2GiB"
    peaks._try_sample()
    assert (peaks.samples, peaks.skipped) == (1, 1)
    assert peaks.containers["backend"] == {"cpu_percent": 12.5, "memory_mib": 1536.0}


def test_no_containers_stops_the_run_before_it_measures_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = load()
    monkeypatch.setattr(benchmark, "output", lambda *command: "")
    with pytest.raises(SystemExit, match="wrong-name"):
        benchmark.Peaks("wrong-name").__enter__()


def test_call_reports_status_zero_for_a_truncated_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client
    import urllib.error

    benchmark = load()

    def truncated(*args: Any, **kwargs: Any) -> Any:
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(benchmark.urllib.request, "urlopen", truncated)
    assert benchmark.call("http://x/health")[0] == 0

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("http://x", 429, "slow", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(benchmark.urllib.request, "urlopen", refused)
    assert benchmark.call("http://x/health")[0] == 429
