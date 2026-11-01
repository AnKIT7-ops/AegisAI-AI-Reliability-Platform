"""Measure a running stack and save the evidence.

Needs Ollama running, sign-in off and the sample documents ingested. See
docs/benchmarks.md. Standard library only, so it needs no setup.
"""

import argparse
import http.client
import json
import math
import platform
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "data" / "evaluation" / "v1.jsonl"
OUT_OF_SCOPE = "What is the capital of France?"
CONCURRENCY = (1, 2, 4)
CHAT_REQUESTS = 40
READ_REQUESTS = 200
SAMPLE_EVERY_S = 5.0
EVALUATION_LIMIT_S = 900
# Stands in for the status of a 200 whose answer is not the kind expected.
WRONG_OUTCOME = -1
MIB_PER_UNIT = {
    "B": 1 / 1024**2,
    "KiB": 1 / 1024,
    "MiB": 1.0,
    "GiB": 1024.0,
    "TiB": 1024.0**2,
}


def percentile(values: list[float], p: float) -> float | None:
    """Nearest rank, as in the evaluation harness."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summarize(
    name: str, concurrency: int, results: list[tuple[int, float]], wall_s: float
) -> dict[str, Any]:
    """Latency covers successful requests only; the other outcomes are counted."""
    ok = [ms for status, ms in results if status == 200]
    limited = sum(status == 429 for status, _ in results)
    wrong = sum(status == WRONG_OUTCOME for status, _ in results)
    return {
        "scenario": name,
        "concurrency": concurrency,
        "requests": len(results),
        "ok": len(ok),
        "wrong_outcome": wrong,
        "rate_limited": limited,
        "failed": len(results) - len(ok) - limited - wrong,
        "failure_rate": round((len(results) - len(ok)) / len(results), 4),
        "wall_s": round(wall_s, 2),
        "throughput_per_s": round(len(ok) / wall_s, 2),
        "p50_ms": percentile(ok, 50),
        "p95_ms": percentile(ok, 95),
        "max_ms": max(ok, default=None),
        "statuses": sorted({status for status, _ in results}),
        "latencies_ms": ok,
    }


def call(url: str, body: dict[str, Any] | None = None) -> tuple[int, float, bytes]:
    """(status, milliseconds, reply); status 0 means no complete response."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data)
    request.add_header("Content-Type", "application/json")
    payload = b""
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=180) as reply:
            payload = reply.read()
            status = reply.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (OSError, http.client.HTTPException):
        status = 0
    return status, round((time.perf_counter() - started) * 1000, 1), payload


def outcome(status: int, payload: bytes, expect_sources: bool | None) -> int:
    """A 200 with the wrong kind of answer must not pass as a success."""
    if status != 200 or expect_sources is None:
        return status
    try:
        has_sources = bool(json.loads(payload)["sources"])
    except (ValueError, KeyError, TypeError):
        return WRONG_OUTCOME
    return status if has_sources == expect_sources else WRONG_OUTCOME


def read_json(url: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data)
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=180) as reply:
        return json.load(reply)


def run(
    name: str,
    url: str,
    bodies: list[dict[str, Any] | None],
    concurrency: int,
    pace_s: float = 0.0,
    expect_sources: bool | None = None,
) -> dict[str, Any]:
    def one(body: dict[str, Any] | None) -> tuple[int, float]:
        status, ms, payload = call(url, body)
        time.sleep(pace_s)
        return outcome(status, payload, expect_sources), ms

    started = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        results = list(pool.map(one, bodies))
    summary = summarize(name, concurrency, results, time.perf_counter() - started)
    print(
        f"{name} x{concurrency}: {summary['ok']}/{summary['requests']} ok, "
        f"{summary['wrong_outcome']} wrong outcome, "
        f"p50 {summary['p50_ms']} ms, p95 {summary['p95_ms']} ms"
    )
    return summary


def to_mib(text: str) -> float:
    """'123.4MiB' or '1.2GiB' from docker stats. Raises on anything else."""
    match = re.fullmatch(r"([\d.]+)(\w+)", text)
    if match is None or match[2] not in MIB_PER_UNIT:
        raise ValueError(f"unknown memory value: {text!r}")
    return float(match[1]) * MIB_PER_UNIT[match[2]]


def output(*command: str) -> str:
    """A command's output, or empty if the tool is missing or fails."""
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=20, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class Peaks:
    """Samples Docker and the GPU in the background and keeps the highest values."""

    def __init__(self, project: str) -> None:
        self.project = project
        self.containers: dict[str, dict[str, float]] = {}
        self.gpu_memory_mib = 0.0
        self.samples = 0
        self.skipped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _services(self) -> dict[str, str]:
        names = output(
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={self.project}",
            "--format",
            '{{.Label "com.docker.compose.service"}}={{.Names}}',
        )
        return dict(line.split("=", 1) for line in names.splitlines())

    def _sample(self) -> None:
        services = self._services()
        if not services:
            raise ValueError("no containers")
        stats = output(
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}",
            *services.values(),
        )
        by_container = {name: service for service, name in services.items()}
        for line in stats.splitlines():
            name, cpu, memory = line.split("|")
            peak = self.containers.setdefault(
                by_container.get(name, name), {"cpu_percent": 0.0, "memory_mib": 0.0}
            )
            peak["cpu_percent"] = max(peak["cpu_percent"], float(cpu.rstrip("%")))
            peak["memory_mib"] = max(
                peak["memory_mib"], round(to_mib(memory.split(" / ")[0]), 1)
            )
        gpu = output(
            "nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"
        )
        if gpu:
            self.gpu_memory_mib = max(self.gpu_memory_mib, float(gpu.splitlines()[0]))
        self.samples += 1

    def _try_sample(self) -> None:
        """A bad sample is counted, so sampling cannot stop without anyone seeing."""
        try:
            self._sample()
        except ValueError:
            self.skipped += 1

    def _loop(self) -> None:
        while not self._stop.wait(SAMPLE_EVERY_S):
            self._try_sample()

    def __enter__(self) -> "Peaks":
        if not self._services():
            raise SystemExit(f"No running containers in project {self.project!r}.")
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def evaluate(api: str) -> dict[str, Any]:
    run_id = read_json(f"{api}/evaluations/run", {"label": "benchmark"})["id"]
    deadline = time.monotonic() + EVALUATION_LIMIT_S
    while time.monotonic() < deadline:
        time.sleep(5)
        detail: dict[str, Any] = read_json(f"{api}/evaluations/{run_id}")
        if detail["status"] not in ("pending", "running"):
            detail.pop("results", None)
            print(f"evaluation: {detail['status']}, gate {detail.get('gate')}")
            return detail
    raise SystemExit("The evaluation did not finish in time.")


def milliseconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def report(results: dict[str, Any]) -> str:
    run_config = results["evaluation"]
    environment = results["environment"]
    gate_passed = (run_config.get("gate") or {}).get("passed")
    lines = [
        f"# Benchmark {results['date']}",
        "",
        f"Commit `{results['commit']}`. Took {results['duration_s']} s. Raw values "
        "are in `results.json`.",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Hardware | {results['hardware']} |",
        f"| Operating system | {environment['os']} |",
        f"| Docker | {environment['docker']} |",
        f"| Ollama | {environment['ollama']} |",
        f"| Chat model | `{run_config['chat_model']}` |",
        f"| Embedding model | `{run_config['embed_model']}` |",
        f"| Prompt version | {run_config['prompt_version']} |",
        f"| Dataset | {run_config['dataset_version']}, "
        f"checksum `{run_config['dataset_checksum']}` |",
        f"| Retrieval | top_k {run_config['top_k']}, "
        f"min_score {run_config['min_score']} |",
        f"| Documents ingested | {results['documents']} |",
        "",
        "## Latency",
        "",
        "Client side, through nginx. Percentiles are nearest rank over successful "
        "requests. Wrong means HTTP 200 with the wrong kind of reply: a refusal in "
        "the Answered rows, or an answer in the Refused row. Those requests are "
        "left out of the latency figures.",
        "",
        "| Scenario | Concurrency | Requests | OK | Wrong | Failed | 429 "
        "| Requests/s | p50 ms | p95 ms | Max ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in results["scenarios"]:
        lines.append(
            f"| {row['scenario']} | {row['concurrency']} | {row['requests']} "
            f"| {row['ok']} | {row['wrong_outcome']} | {row['failed']} "
            f"| {row['rate_limited']} "
            f"| {row['throughput_per_s']} | {milliseconds(row['p50_ms'])} "
            f"| {milliseconds(row['p95_ms'])} | {milliseconds(row['max_ms'])} |"
        )
    lines += [
        "",
        "## Evaluation",
        "",
        f"Status `{run_config['status']}`, quality gate "
        f"{'passed' if gate_passed else 'not passed'}.",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    lines += [f"| `{k}` | {v} |" for k, v in run_config["metrics"].items()]
    peaks = results["peaks"]
    lines += [
        "",
        "## Peak resource use",
        "",
        f"Highest of {peaks['samples']} samples ({peaks['skipped']} skipped), one "
        f"about every {SAMPLE_EVERY_S:.0f} s, so short spikes can be missed. "
        "GPU memory is the "
        f"whole GPU: {peaks['gpu_memory_mib']:.0f} MiB.",
        "",
        "| Service | CPU % | Memory MiB |",
        "|---|---|---|",
    ]
    lines += [
        f"| {name} | {peak['cpu_percent']:.1f} | {peak['memory_mib']:.1f} |"
        for name, peak in sorted(peaks["containers"].items())
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", required=True, help="CPU, RAM and GPU, as text")
    parser.add_argument(
        "--out", required=True, type=Path, help="Folder for the results"
    )
    # Not localhost: nginx is on IPv4 only, and on Windows each new connection
    # would first wait about 2 s for ::1 to fail, which swamps every measurement.
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--project", default="aegis-ai", help="Compose project name")
    args = parser.parse_args()
    api = f"{args.url}/api/v1"

    if read_json(f"{api}/auth/session")["enabled"]:
        raise SystemExit("Turn sign-in off first: this script holds no session.")
    if not read_json(f"{api}/models")["reachable"]:
        raise SystemExit("Ollama is not reachable.")
    documents = len(read_json(f"{api}/documents")["documents"])
    if documents == 0:
        raise SystemExit("Ingest the sample documents first.")

    examples = [json.loads(line) for line in DATASET.read_text("utf-8").splitlines()]
    questions = [e["question"] for e in examples if e["category"] == "answerable"]
    answered: list[dict[str, Any] | None] = [
        {"question": question} for question in questions[:CHAT_REQUESTS]
    ]
    refused: list[dict[str, Any] | None] = [{"question": OUT_OF_SCOPE}] * CHAT_REQUESTS

    began = time.perf_counter()
    print("warming the model; this request is not measured")
    call(f"{api}/chat", {"question": questions[0]})

    scenarios = []
    with Peaks(args.project) as peaks:
        for concurrency in (1, 8):
            scenarios.append(
                run(
                    "Read document list",
                    f"{api}/documents",
                    [None] * READ_REQUESTS,
                    concurrency,
                )
            )
        # Paced under the rate limit: this measures latency, not the limit.
        scenarios.append(
            run(
                "Refused, no chat model",
                f"{api}/chat",
                refused,
                1,
                pace_s=0.6,
                expect_sources=False,
            )
        )
        for concurrency in CONCURRENCY:
            scenarios.append(
                run(
                    "Answered",
                    f"{api}/chat",
                    answered,
                    concurrency,
                    expect_sources=True,
                )
            )
        evaluation = evaluate(api)

    results = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "commit": output("git", "-C", str(REPO), "rev-parse", "--short", "HEAD"),
        "hardware": args.hardware,
        "duration_s": round(time.perf_counter() - began),
        "environment": {
            "os": platform.platform(),
            "docker": output("docker", "version", "--format", "{{.Server.Version}}"),
            "ollama": output("ollama", "--version").rsplit(" ", 1)[-1],
        },
        "documents": documents,
        "scenarios": scenarios,
        "evaluation": evaluation,
        "peaks": {
            "samples": peaks.samples,
            "skipped": peaks.skipped,
            "containers": peaks.containers,
            "gpu_memory_mib": peaks.gpu_memory_mib,
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(results, indent=2) + "\n", "utf-8"
    )
    (args.out / "report.md").write_text(report(results), "utf-8")
    print(f"saved to {args.out}")


if __name__ == "__main__":
    main()
