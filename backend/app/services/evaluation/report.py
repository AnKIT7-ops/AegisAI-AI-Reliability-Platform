"""Quality thresholds and run reports (Markdown, CSV)."""

import csv
import io
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# Metrics where lower is better; every other thresholded metric is higher-is-better.
LOWER_IS_BETTER = {
    "failure_rate",
    "false_refusal_rate",
    "latency_ms_p50",
    "latency_ms_p95",
}


class Thresholds(BaseModel):
    dataset_version: str
    # The dataset file the limits were measured on; any edit changes it.
    dataset_checksum: str
    basis: str  # where the numbers came from
    limits: dict[str, float]


class GateResult(BaseModel):
    passed: bool
    failures: list[str]


def load_thresholds(eval_dir: Path, dataset_version: str) -> Thresholds | None:
    """None when there is no thresholds file; ValueError when it is malformed."""
    path = eval_dir / f"{dataset_version}.thresholds.json"
    if not path.is_file():
        return None
    return Thresholds.model_validate_json(path.read_text(encoding="utf-8"))


def check_gate(
    metrics: dict[str, Any], thresholds: Thresholds, dataset_checksum: str
) -> GateResult:
    if dataset_checksum != thresholds.dataset_checksum:
        # Limits from another version of the dataset say nothing about this run.
        return GateResult(
            passed=False,
            failures=[
                f"thresholds were set on dataset checksum "
                f"{thresholds.dataset_checksum}, this run used {dataset_checksum}; "
                "re-measure the baseline"
            ],
        )
    failures = []
    for name, limit in thresholds.limits.items():
        value = metrics.get(name)
        if value is None:
            failures.append(f"{name}: not measured")
        elif name in LOWER_IS_BETTER and value > limit:
            failures.append(f"{name}: {_fmt(value)} > {_fmt(limit)}")
        elif name not in LOWER_IS_BETTER and value < limit:
            failures.append(f"{name}: {_fmt(value)} < {_fmt(limit)}")
    return GateResult(passed=not failures, failures=failures)


CSV_COLUMNS = [
    "example_id",
    "category",
    "question",
    "answer",
    "retrieved",
    "sources",
    "cited",
    "refused",
    "rank",
    "correct",
    "citation_correct",
    "resisted",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "error",
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = json.dumps(value) if isinstance(value, list | dict) else str(value)
    # Model output must not run as a formula when the CSV opens in a spreadsheet.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def results_csv(results: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in results:
        # The result's own fields win: scores["error"] is a flag, r["error"] the text.
        merged = r.get("scores", {}) | r
        writer.writerow([_cell(merged.get(c)) for c in CSV_COLUMNS])
    return buf.getvalue()


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    return str(value)


def _md(text: str) -> str:
    # Keep model and dataset text from breaking out of a table cell.
    return " ".join(text.split()).replace("|", "\\|")


def _failed(r: dict[str, Any]) -> str | None:
    s = r.get("scores", {})
    if r.get("error"):
        return f"error: {_md(r['error'])}"
    if r["category"] == "answerable":
        if s.get("refused"):
            return "refused"
        if not s.get("correct"):
            return "key facts missing"
        if s.get("citation_correct") is False:
            return "citation"
    if r["category"] == "unanswerable" and not s.get("refused"):
        return "answered instead of refusing"
    if r["category"] == "injection" and not s.get("resisted"):
        return "followed the injected instruction"
    return None


def markdown_report(
    run: dict[str, Any],
    results: list[dict[str, Any]],
    gate: GateResult | None,
    baseline: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"# Evaluation run {run['id']}",
        "",
        f"- Label: {_md(run['label'])}",
        f"- Status: {run['status']}",
        f"- Dataset: {run['dataset_version']} (checksum {run['dataset_checksum']})",
        f"- Prompt version: {run['prompt_version']}",
        f"- Models: chat `{run['chat_model']}`, embeddings `{run['embed_model']}`",
        f"- Retrieval: top_k {run['top_k']}, min_score {run['min_score']}",
        f"- Created: {run['created_at']}; finished: {run['finished_at'] or '-'}",
    ]
    if run.get("error"):
        lines.append(f"- Error: {_md(run['error'])}")
    if gate is not None:
        verdict = "PASS" if gate.passed else "FAIL"
        lines.append(f"- Quality gate: **{verdict}**")
        lines += [f"  - {f}" for f in gate.failures]

    metrics = run.get("metrics") or {}
    base = (baseline or {}).get("metrics") or {}
    lines += ["", "## Metrics", ""]
    if baseline:
        lines.append(
            f"Compared with run {baseline['id']} ({_md(baseline['label'])}), "
            f"dataset {baseline['dataset_version']} "
            f"(checksum {baseline['dataset_checksum']})."
        )
        if (baseline["dataset_version"], baseline["dataset_checksum"]) != (
            run["dataset_version"],
            run["dataset_checksum"],
        ):
            lines += [
                "",
                "**Warning: the two runs used different datasets, so the changes "
                "below are not comparable.**",
            ]
        lines += [
            "",
            "| Metric | This run | Baseline | Change |",
            "|---|---|---|---|",
        ]
        for name, value in metrics.items():
            other = base.get(name)
            delta = (
                _fmt(round(value - other, 4))
                if isinstance(value, int | float) and isinstance(other, int | float)
                else "-"
            )
            lines.append(f"| {name} | {_fmt(value)} | {_fmt(other)} | {delta} |")
    else:
        lines += ["| Metric | Value |", "|---|---|"]
        lines += [f"| {name} | {_fmt(value)} |" for name, value in metrics.items()]

    failed = [(r, why) for r in results if (why := _failed(r))]
    lines += ["", f"## Examples that did not pass ({len(failed)})", ""]
    if failed:
        lines += ["| Example | Category | Reason | Answer |", "|---|---|---|---|"]
        for r, why in failed:
            answer = _md((r.get("answer") or "")[:200])
            lines.append(f"| {r['example_id']} | {r['category']} | {why} | {answer} |")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"
