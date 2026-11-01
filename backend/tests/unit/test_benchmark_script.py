import importlib.util
from pathlib import Path
from typing import Any

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


def test_docker_memory_units() -> None:
    to_mib = load().to_mib
    assert to_mib("512KiB") == 0.5
    assert to_mib("123.4MiB") == 123.4
    assert to_mib("1.5GiB") == 1536.0
