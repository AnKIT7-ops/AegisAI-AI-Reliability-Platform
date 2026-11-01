"""The demo: real exported evaluation runs, kept apart from live data.

Loading only inserts rows that are absent, in one transaction, under keys derived
from the fixture names. It never updates or deletes, so anything a person did to
a demo record survives another load. Nothing reaches the vector index or the
Prometheus metrics, and the gate is applied now, not read from the fixture.
"""

import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import (
    EvaluationResult,
    EvaluationRun,
    Incident,
    Release,
    ReleaseEvent,
)
from app.services.evaluation.jobs import result_to_dict
from app.services.evaluation.report import GateResult, gate_for_run
from app.services.incidents.diagnosis import Report, Signals, diagnose
from app.services.releases import compare, snapshot

SEED_VERSION = "1"
NAMESPACE = uuid.UUID("6f1d3a52-7a2e-4c0e-9b1a-3f6d2c8e5a10")
ACTOR = "demo-seed"
SCENARIO = "model_timeout"
TABLES = ("evaluation_runs", "releases", "release_events", "incidents")

_CREATED = ("created", None, "candidate")
_PASSED = ("canary_passed", "candidate", "canary_passed")
_PROMOTED = ("promoted", "canary_passed", "active")
# The events that lead to each final status, as (event, from, to).
HISTORY: dict[str, list[tuple[str, str | None, str]]] = {
    "superseded": [
        _CREATED,
        _PASSED,
        _PROMOTED,
        ("superseded", "active", "superseded"),
    ],
    "active": [_CREATED, _PASSED, _PROMOTED],
    "rejected": [_CREATED, ("canary_failed", "candidate", "rejected")],
}


def seed_id(kind: str, name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{SEED_VERSION}:{kind}:{name}")


def _stale(reason: str) -> ApiError:
    return ApiError(
        409,
        "demo_stale",
        f"the demo data is out of date ({reason}); "
        "regenerate it with scripts/export_demo.py",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Fixtures:
    runs: dict[str, dict[str, Any]]
    releases: list[dict[str, str]]


def _read_fixtures(demo_dir: Path) -> Fixtures:
    releases = _read_json(demo_dir / "releases.json")
    runs = {
        entry["run"]: _read_json(demo_dir / "runs" / f"{entry['run']}.json")
        for entry in releases
    }
    return Fixtures(runs, releases)


def _gates(fixtures: Fixtures, settings: Settings) -> dict[str, GateResult]:
    """Today's gate must still tell the story the fixtures tell."""
    gates = {}
    for entry in fixtures.releases:
        run = fixtures.runs[entry["run"]]
        gate = gate_for_run(
            settings.eval_dir,
            run["dataset_version"],
            run["metrics"],
            run["dataset_checksum"],
        )
        if gate is None or gate.passed != (entry["status"] != "rejected"):
            raise _stale(f"the quality gate disagrees with {entry['version']}")
        gates[entry["version"]] = gate
    return gates


def _incident_report(settings: Settings) -> Report:
    path = settings.incident_dir / f"{SCENARIO}.json"
    signals = Signals.model_validate_json(path.read_text(encoding="utf-8"))
    reports = diagnose(signals, settings.prometheus_public_url)
    for report in reports:
        if report.type == SCENARIO:
            return report
    raise _stale(f"the {SCENARIO} scenario no longer diagnoses as one")


def _add_run(session: Session, name: str, fixture: dict[str, Any]) -> bool:
    run_id = seed_id("run", name)
    if session.get(EvaluationRun, run_id) is not None:
        return False
    session.add(
        EvaluationRun(
            id=run_id,
            status="done",
            source="demo",
            label=fixture["label"],
            dataset_version=fixture["dataset_version"],
            dataset_checksum=fixture["dataset_checksum"],
            prompt_version=fixture["prompt_version"],
            chat_model=fixture["chat_model"],
            embed_model=fixture["embed_model"],
            top_k=fixture["top_k"],
            min_score=fixture["min_score"],
            config=fixture["config"],
            metrics=fixture["metrics"],
            created_at=datetime.fromisoformat(fixture["created_at"]),
            started_at=datetime.fromisoformat(fixture["started_at"]),
            finished_at=datetime.fromisoformat(fixture["finished_at"]),
            results=[EvaluationResult(**r) for r in fixture["results"]],
        )
    )
    session.flush()
    return True


def _add_release(
    session: Session,
    entry: dict[str, str],
    gate: GateResult,
    baseline: Release | None,
) -> tuple[Release, bool]:
    release_id = seed_id("release", entry["version"])
    existing = session.get(Release, release_id)
    if existing is not None:
        return existing, False
    run = session.get(EvaluationRun, seed_id("run", entry["run"]))
    if run is None:
        raise _stale(f"{entry['version']} names no run")
    release = Release(
        id=release_id,
        version=entry["version"],
        source="demo",
        status=entry["status"],
        created_at=run.finished_at,
        gate_passed=gate.passed,
        gate_failures=gate.failures,
        baseline_release_id=baseline.id if baseline else None,
        **snapshot(run),
    )
    release.comparison = compare(release, baseline)
    session.add(release)
    # One at a time: the baseline foreign key points at the previous release.
    session.flush()
    return release, True


def _add_events(release: Release, status: str) -> int:
    present = {event.details.get("seed_key") for event in release.events}
    added = 0
    for event, before, after in HISTORY[status]:
        key = f"{SEED_VERSION}:{release.version}:{event}"
        if key not in present:
            release.events.append(
                ReleaseEvent(
                    event=event,
                    from_status=before,
                    to_status=after,
                    actor=ACTOR,
                    details={"seed_key": key},
                    created_at=release.created_at,
                )
            )
            added += 1
    return added


def _add_incident(session: Session, report: Report) -> bool:
    incident_id = seed_id("incident", SCENARIO)
    if session.get(Incident, incident_id) is not None:
        return False
    now = datetime.now(UTC)
    session.add(
        Incident(
            id=incident_id,
            type=report.type,
            severity=report.severity,
            source="simulated",
            report=report.model_dump(mode="json"),
            first_seen=now,
            last_seen=now,
        )
    )
    return True


def _insert_all(
    session: Session,
    fixtures: Fixtures,
    gates: dict[str, GateResult],
    report: Report,
) -> Counter[str]:
    created: Counter[str] = Counter()
    for name, fixture in fixtures.runs.items():
        created["evaluation_runs"] += _add_run(session, name, fixture)
    baseline = None
    for entry in fixtures.releases:
        release, added = _add_release(session, entry, gates[entry["version"]], baseline)
        created["releases"] += added
        created["release_events"] += _add_events(release, entry["status"])
        baseline = release
    created["incidents"] += _add_incident(session, report)
    session.commit()
    return created


def load_demo(session: Session, settings: Settings) -> dict[str, dict[str, int]]:
    try:
        fixtures = _read_fixtures(settings.demo_dir)
        gates = _gates(fixtures, settings)
        report = _incident_report(settings)
        totals = Counter(
            evaluation_runs=len(fixtures.runs),
            releases=len(fixtures.releases),
            release_events=sum(len(HISTORY[r["status"]]) for r in fixtures.releases),
            incidents=1,
        )
        created = _insert_all(session, fixtures, gates, report)
    except IntegrityError:
        # A simultaneous load won: everything is present, and this one wrote nothing.
        session.rollback()
        created = Counter()
    except (OSError, ValueError, KeyError, TypeError):
        session.rollback()
        raise _stale("a fixture is missing or malformed") from None
    return {
        "created": {table: created[table] for table in TABLES},
        "already_present": {table: totals[table] - created[table] for table in TABLES},
    }


def demo_status(session: Session, settings: Settings) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "loaded": False,
        "documents": [],
        "recorded_answers": [],
        "run_ids": {},
        "incident_id": None,
    }
    try:
        releases = _read_json(settings.demo_dir / "releases.json")
        documents = _read_json(settings.demo_dir / "documents.json")
        recorded = _read_json(settings.demo_dir / "recorded_answers.json")
        names = [entry["run"] for entry in releases]
    except (OSError, ValueError, KeyError, TypeError):
        return empty
    run_ids = {name: seed_id("run", name) for name in names}
    found = set(
        session.scalars(
            select(EvaluationRun.id).where(EvaluationRun.id.in_(run_ids.values()))
        )
    )
    if found != set(run_ids.values()):
        return empty | {"documents": documents}
    source_run = run_ids[recorded["run"]]
    rows = session.scalars(
        select(EvaluationResult).where(
            EvaluationResult.run_id == source_run,
            EvaluationResult.example_id.in_(recorded["examples"]),
        )
    )
    by_example = {row.example_id: row for row in rows}
    incident_id = seed_id("incident", SCENARIO)
    return {
        "loaded": True,
        "documents": documents,
        "recorded_answers": [
            result_to_dict(by_example[example]) | {"run_id": str(source_run)}
            for example in recorded["examples"]
            if example in by_example
        ],
        "run_ids": {name: str(run_id) for name, run_id in run_ids.items()},
        "incident_id": (
            str(incident_id) if session.get(Incident, incident_id) else None
        ),
    }
