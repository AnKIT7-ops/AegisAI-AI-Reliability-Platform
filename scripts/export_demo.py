"""Regenerate the demo fixtures in data/demo from real evaluation runs.

Reads through the running stack, because PostgreSQL is not published. Sign-in
must be off. recorded_answers.json is chosen by hand and is left alone.

Usage: python scripts/export_demo.py [--api http://127.0.0.1:8080]
"""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"
SAMPLE_DOCS = ROOT / "data" / "sample_docs"
# Fixture name to evaluation run. Two pass the quality gate and one fails it.
RUNS = {"known-good": "9b6ec6a2", "current": "8e4b042f", "degraded": "1b3018f0"}
RELEASES = [
    {"version": "demo-1", "run": "known-good", "status": "superseded"},
    {"version": "demo-2", "run": "current", "status": "active"},
    {"version": "demo-3", "run": "degraded", "status": "rejected"},
]
# Local to the machine that ran the evaluation, or set at load time.
DROPPED = ("artifact_path", "error", "gate", "source")


def get(api: str, path: str) -> Any:
    with urllib.request.urlopen(f"{api}/api/v1{path}", timeout=30) as response:
        return json.load(response)


def write(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(content, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def export_run(api: str, name: str, prefix: str, listed: list[dict[str, Any]]) -> None:
    matches = [run["id"] for run in listed if run["id"].startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"run {prefix}: found {len(matches)} matches")
    run = get(api, f"/evaluations/{matches[0]}")
    if run["status"] != "done":
        raise SystemExit(f"run {prefix} is {run['status']}, not done")
    fixture = {k: v for k, v in run.items() if k not in DROPPED}
    fixture["exported_from"] = fixture.pop("id")
    write(DEMO / "runs" / f"{name}.json", fixture)


def document_rows(api: str) -> list[dict[str, Any]]:
    """LF-normalised checksums: Windows working copies are CRLF, CI is LF."""
    chunks = {d["filename"]: d["chunks"] for d in get(api, "/documents")["documents"]}
    rows = []
    for path in sorted(SAMPLE_DOCS.glob("*.md")):
        if path.name == "ATTRIBUTION.md":
            continue
        if path.name not in chunks:
            raise SystemExit(f"{path.name} is not ingested; run ingest_samples.ps1")
        content = path.read_bytes().replace(b"\r\n", b"\n")
        rows.append(
            {
                "filename": path.name,
                "size_bytes": len(content),
                "checksum": hashlib.sha256(content).hexdigest(),
                "chunks": chunks[path.name],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8080")
    api = parser.parse_args().api
    listed = get(api, "/evaluations?limit=200")["runs"]
    for name, prefix in RUNS.items():
        export_run(api, name, prefix, listed)
    write(DEMO / "releases.json", RELEASES)
    write(DEMO / "documents.json", document_rows(api))
    print(f"wrote {DEMO}")


if __name__ == "__main__":
    main()
