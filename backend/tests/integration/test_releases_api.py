"""The release lifecycle through the API."""

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import EvaluationRun, Release

RELEASES = "/api/v1/releases"
CHECKSUM = "abc"
GOOD = {"recall_at_k": 1.0, "latency_ms_p95": 1000, "examples": 3}
BETTER = {"recall_at_k": 1.0, "latency_ms_p95": 900, "examples": 3}
SLOWER = {"recall_at_k": 1.0, "latency_ms_p95": 2000, "examples": 3}
BAD = {"recall_at_k": 0.5, "latency_ms_p95": 1000, "examples": 3}


@pytest.fixture
def gated(api: TestClient, settings: Settings) -> TestClient:
    thresholds = {
        "dataset_version": "v1",
        "dataset_checksum": CHECKSUM,
        "basis": "test",
        "limits": {"recall_at_k": 0.9, "latency_ms_p95": 5000},
    }
    (settings.eval_dir / "v1.thresholds.json").write_text(
        json.dumps(thresholds), encoding="utf-8"
    )
    return api


def add_run(
    sessions: sessionmaker[Session],
    metrics: dict[str, Any] | None = None,
    status: str = "done",
    source: str = "live",
    checksum: str = CHECKSUM,
) -> str:
    with sessions() as session:
        run = EvaluationRun(
            status=status,
            source=source,
            label="r",
            dataset_version="v1",
            dataset_checksum=checksum,
            prompt_version="p2",
            chat_model="chat",
            embed_model="embed",
            top_k=4,
            min_score=0.7,
            config={"chunk_size": 800},
            metrics=GOOD if metrics is None else metrics,
        )
        session.add(run)
        session.commit()
        return str(run.id)


class Flow:
    def __init__(self, client: TestClient, sessions: sessionmaker[Session]) -> None:
        self.client = client
        self.sessions = sessions
        self.count = 0

    def candidate(self, **run: Any) -> str:
        self.count += 1
        r = self.client.post(
            RELEASES,
            json={
                "version": f"r{self.count}",
                "evaluation_run_id": add_run(self.sessions, **run),
            },
        )
        assert r.status_code == 201, r.text
        release_id: str = r.json()["id"]
        return release_id

    def canary(self, release_id: str, **body: Any) -> dict[str, Any]:
        r = self.client.post(f"{RELEASES}/{release_id}/canary", json=body)
        assert r.status_code == 200, r.text
        detail: dict[str, Any] = r.json()
        return detail

    def passed(self, **run: Any) -> str:
        release_id = self.candidate(**run)
        self.canary(release_id)
        return release_id

    def promote(self, release_id: str) -> dict[str, Any]:
        r = self.client.post(f"{RELEASES}/{release_id}/promote", json={})
        assert r.status_code == 200, r.text
        detail: dict[str, Any] = r.json()
        return detail

    def active(self, **run: Any) -> str:
        release_id = self.passed(**run)
        self.promote(release_id)
        return release_id

    def status(self, release_id: str) -> str:
        status: str = self.client.get(f"{RELEASES}/{release_id}").json()["status"]
        return status

    def events(self, release_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = self.client.get(
            f"{RELEASES}/{release_id}"
        ).json()["events"]
        return events


@pytest.fixture
def flow(gated: TestClient, sessions: sessionmaker[Session]) -> Flow:
    return Flow(gated, sessions)


def refused(r: Any, status: int, code: str) -> None:
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)


def test_create_snapshots_the_run(flow: Flow, sessions: sessionmaker[Session]) -> None:
    run_id = add_run(sessions)
    r = flow.client.post(RELEASES, json={"version": "1.0", "evaluation_run_id": run_id})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "candidate"
    assert body["simulated"] is True
    assert body["source"] == "live"
    assert body["evaluation_run_id"] == run_id
    assert body["dataset_checksum"] == CHECKSUM
    assert body["prompt_version"] == "p2"
    assert body["chat_model"] == "chat"
    assert body["embed_model"] == "embed"
    assert (body["top_k"], body["min_score"]) == (4, 0.7)
    assert body["config"] == {"chunk_size": 800}
    assert body["metrics"] == GOOD
    assert body["gate_passed"] is None
    assert body["gate_failures"] == []
    assert body["comparison"] is None
    assert [e["event"] for e in body["events"]] == ["created"]
    assert body["events"][0]["to_status"] == "candidate"


@pytest.mark.parametrize("status", ["failed", "pending", "running"])
def test_incomplete_run_cannot_become_a_release(
    flow: Flow, sessions: sessionmaker[Session], status: str
) -> None:
    run_id = add_run(sessions, status=status)
    r = flow.client.post(RELEASES, json={"version": "1.0", "evaluation_run_id": run_id})
    refused(r, 409, "evaluation_not_completed")
    assert flow.client.get(RELEASES).json()["releases"] == []


def test_unknown_run_is_not_found(flow: Flow) -> None:
    r = flow.client.post(
        RELEASES, json={"version": "1.0", "evaluation_run_id": str(uuid.uuid4())}
    )
    refused(r, 404, "not_found")


def test_duplicate_version_is_refused(
    flow: Flow, sessions: sessionmaker[Session]
) -> None:
    body = {"version": "1.0", "evaluation_run_id": add_run(sessions)}
    assert flow.client.post(RELEASES, json=body).status_code == 201
    refused(flow.client.post(RELEASES, json=body), 409, "version_exists")


def test_same_version_in_another_source_is_allowed(
    flow: Flow, sessions: sessionmaker[Session]
) -> None:
    for source in ("live", "demo"):
        run_id = add_run(sessions, source=source)
        r = flow.client.post(
            RELEASES, json={"version": "1.0", "evaluation_run_id": run_id}
        )
        assert r.status_code == 201
        assert r.json()["source"] == source


@pytest.mark.parametrize(
    "body",
    [
        {"version": "bad version", "evaluation_run_id": str(uuid.uuid4())},
        {"version": "a/b", "evaluation_run_id": str(uuid.uuid4())},
        {"version": "", "evaluation_run_id": str(uuid.uuid4())},
        {"version": "x" * 65, "evaluation_run_id": str(uuid.uuid4())},
        {"version": "1.0", "evaluation_run_id": "nope"},
        {"version": "1.0", "evaluation_run_id": str(uuid.uuid4()), "status": "active"},
        {"version": "1.0", "evaluation_run_id": str(uuid.uuid4()), "gate_passed": True},
    ],
)
def test_invalid_create_bodies_are_rejected(flow: Flow, body: dict[str, Any]) -> None:
    assert flow.client.post(RELEASES, json=body).status_code == 422


def test_passing_canary(flow: Flow) -> None:
    detail = flow.canary(flow.candidate())
    assert detail["status"] == "canary_passed"
    assert detail["gate_passed"] is True
    assert detail["gate_failures"] == []
    event = detail["events"][-1]
    assert (event["event"], event["from_status"], event["to_status"]) == (
        "canary_passed",
        "candidate",
        "canary_passed",
    )


def test_failing_canary_rejects_and_keeps_the_failures(flow: Flow) -> None:
    detail = flow.canary(flow.candidate(metrics=BAD))
    assert detail["status"] == "rejected"
    assert detail["gate_passed"] is False
    assert detail["gate_failures"] == ["recall_at_k: 0.5 < 0.9"]
    assert detail["events"][-1]["event"] == "canary_failed"


def test_canary_needs_thresholds(flow: Flow, settings: Settings) -> None:
    release_id = flow.candidate()
    (settings.eval_dir / "v1.thresholds.json").unlink()
    r = flow.client.post(f"{RELEASES}/{release_id}/canary", json={})
    refused(r, 409, "gate_unavailable")
    assert flow.status(release_id) == "candidate"


def test_invalid_thresholds_fail_the_canary(flow: Flow, settings: Settings) -> None:
    release_id = flow.candidate()
    (settings.eval_dir / "v1.thresholds.json").write_text("{", encoding="utf-8")
    assert flow.canary(release_id)["status"] == "rejected"


def test_canary_needs_a_candidate(flow: Flow) -> None:
    release_id = flow.passed()
    r = flow.client.post(f"{RELEASES}/{release_id}/canary", json={})
    refused(r, 409, "invalid_transition")


def test_canary_body_is_optional(flow: Flow) -> None:
    r = flow.client.post(f"{RELEASES}/{flow.candidate()}/canary")
    assert r.status_code == 200


def test_comparison_uses_the_active_release(flow: Flow) -> None:
    active = flow.active()
    detail = flow.canary(flow.candidate(metrics=SLOWER))
    assert detail["baseline_release_id"] == active
    assert detail["comparison"]["status"] == "comparable"
    assert detail["comparison"]["baseline_release_id"] == active
    latency = [
        m for m in detail["comparison"]["metrics"] if m["name"] == "latency_ms_p95"
    ]
    assert latency[0]["direction"] == "regressed"
    # Inside the gate limits, a regression warns and does not block.
    assert detail["status"] == "canary_passed"


def test_no_baseline_is_recorded(flow: Flow) -> None:
    detail = flow.canary(flow.candidate())
    assert detail["baseline_release_id"] is None
    assert detail["comparison"]["status"] == "no_baseline"


def test_named_baseline_can_be_superseded(flow: Flow) -> None:
    first = flow.active()
    flow.active(metrics=BETTER)
    detail = flow.canary(flow.candidate(), baseline_release_id=first)
    assert detail["baseline_release_id"] == first


def test_changed_dataset_is_flagged(flow: Flow, settings: Settings) -> None:
    active = flow.active()
    limits = json.loads((settings.eval_dir / "v1.thresholds.json").read_text())
    limits["dataset_checksum"] = "new"
    (settings.eval_dir / "v1.thresholds.json").write_text(json.dumps(limits))
    detail = flow.canary(flow.candidate(checksum="new"))
    assert detail["baseline_release_id"] == active
    assert detail["comparison"]["status"] == "dataset_changed"
    assert detail["comparison"]["metrics"] == []


def test_comparison_is_kept_after_promotion(flow: Flow) -> None:
    flow.active()
    release_id = flow.candidate(metrics=BETTER)
    recorded = flow.canary(release_id)["comparison"]
    assert flow.promote(release_id)["comparison"] == recorded


@pytest.mark.parametrize("status", ["candidate", "canary_passed", "rejected"])
def test_baseline_must_have_been_active(flow: Flow, status: str) -> None:
    other = {
        "candidate": flow.candidate,
        "canary_passed": flow.passed,
        "rejected": lambda: flow.passed(metrics=BAD),
    }[status]()
    r = flow.client.post(
        f"{RELEASES}/{flow.candidate()}/canary", json={"baseline_release_id": other}
    )
    refused(r, 409, "invalid_baseline")


def test_unknown_baseline_is_refused(flow: Flow) -> None:
    r = flow.client.post(
        f"{RELEASES}/{flow.candidate()}/canary",
        json={"baseline_release_id": str(uuid.uuid4())},
    )
    refused(r, 409, "invalid_baseline")


def test_baseline_from_another_source_is_refused(flow: Flow) -> None:
    demo = flow.active(source="demo")
    r = flow.client.post(
        f"{RELEASES}/{flow.candidate()}/canary", json={"baseline_release_id": demo}
    )
    refused(r, 409, "invalid_baseline")


def test_default_baseline_stays_within_the_source(flow: Flow) -> None:
    flow.active(source="demo")
    assert flow.canary(flow.candidate())["comparison"]["status"] == "no_baseline"


def test_first_promotion(flow: Flow) -> None:
    release_id = flow.passed()
    detail = flow.promote(release_id)
    assert detail["status"] == "active"
    assert detail["simulated"] is True
    assert detail["events"][-1]["event"] == "promoted"


def test_promotion_supersedes_the_previous_release(flow: Flow) -> None:
    first = flow.active()
    second = flow.active(metrics=BETTER)
    assert flow.status(first) == "superseded"
    assert flow.status(second) == "active"
    event = flow.events(first)[-1]
    assert (event["event"], event["from_status"], event["to_status"]) == (
        "superseded",
        "active",
        "superseded",
    )
    assert event["details"]["superseded_by"] == second


def test_promotion_keeps_a_reason(flow: Flow) -> None:
    r = flow.client.post(
        f"{RELEASES}/{flow.passed()}/promote", json={"reason": "better recall"}
    )
    assert r.json()["events"][-1]["reason"] == "better recall"


def test_promotion_stays_within_the_source(flow: Flow) -> None:
    live = flow.active()
    demo = flow.active(source="demo")
    assert (flow.status(live), flow.status(demo)) == ("active", "active")


def assert_refused_with_audit(flow: Flow, release_id: str, code: str) -> None:
    before = flow.status(release_id)
    r = flow.client.post(f"{RELEASES}/{release_id}/promote", json={})
    refused(r, 409, code)
    assert flow.status(release_id) == before
    event = flow.events(release_id)[-1]
    assert (event["event"], event["from_status"], event["to_status"]) == (
        "promotion_refused",
        before,
        before,
    )
    assert event["details"]["code"] == code


def test_rejected_release_cannot_be_promoted(flow: Flow) -> None:
    assert_refused_with_audit(flow, flow.passed(metrics=BAD), "invalid_transition")


def test_candidate_cannot_be_promoted(flow: Flow) -> None:
    assert_refused_with_audit(flow, flow.candidate(), "invalid_transition")


def test_active_release_cannot_be_promoted_again(flow: Flow) -> None:
    assert_refused_with_audit(flow, flow.active(), "invalid_transition")


def test_rolled_back_release_cannot_be_promoted(flow: Flow) -> None:
    first = flow.active()
    second = flow.active(metrics=BETTER)
    flow.client.post(
        f"{RELEASES}/{second}/rollback",
        json={"target_release_id": first, "reason": "bad"},
    )
    assert_refused_with_audit(flow, second, "invalid_transition")


def test_promotion_rechecks_the_gate(flow: Flow, settings: Settings) -> None:
    release_id = flow.passed()
    limits = json.loads((settings.eval_dir / "v1.thresholds.json").read_text())
    limits["limits"]["recall_at_k"] = 1.5
    (settings.eval_dir / "v1.thresholds.json").write_text(json.dumps(limits))
    assert_refused_with_audit(flow, release_id, "gate_failed")


def test_promotion_without_thresholds_is_refused(
    flow: Flow, settings: Settings
) -> None:
    release_id = flow.passed()
    (settings.eval_dir / "v1.thresholds.json").unlink()
    assert_refused_with_audit(flow, release_id, "gate_failed")


def test_refused_promotion_leaves_the_active_release(flow: Flow) -> None:
    active = flow.active()
    flow.client.post(f"{RELEASES}/{flow.passed(metrics=BAD)}/promote", json={})
    assert flow.status(active) == "active"


def test_rollback_restores_the_named_release(flow: Flow) -> None:
    first = flow.active()
    second = flow.active(metrics=BETTER)
    r = flow.client.post(
        f"{RELEASES}/{second}/rollback",
        json={"target_release_id": first, "reason": "latency regression"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rolled_back"
    assert r.json()["simulated"] is True
    assert flow.status(first) == "active"
    rolled, restored = flow.events(second)[-1], flow.events(first)[-1]
    assert rolled["event"] == "rolled_back"
    assert rolled["reason"] == "latency regression"
    assert rolled["details"]["target_release_id"] == first
    assert restored["event"] == "restored"
    assert restored["reason"] == "latency regression"
    assert (restored["from_status"], restored["to_status"]) == ("superseded", "active")


def test_release_can_be_promoted_again_after_a_rollback(flow: Flow) -> None:
    first = flow.active()
    second = flow.active(metrics=BETTER)
    flow.client.post(
        f"{RELEASES}/{second}/rollback",
        json={"target_release_id": first, "reason": "bad"},
    )
    third = flow.active(metrics=BETTER)
    assert (flow.status(first), flow.status(third)) == ("superseded", "active")


def test_only_the_active_release_rolls_back(flow: Flow) -> None:
    first = flow.active()
    flow.active(metrics=BETTER)
    r = flow.client.post(
        f"{RELEASES}/{first}/rollback",
        json={"target_release_id": first, "reason": "x"},
    )
    refused(r, 409, "invalid_transition")


def test_rollback_needs_a_superseded_target(flow: Flow) -> None:
    active = flow.active()
    for target in (flow.candidate(), flow.passed(metrics=BAD), active):
        r = flow.client.post(
            f"{RELEASES}/{active}/rollback",
            json={"target_release_id": target, "reason": "x"},
        )
        refused(r, 409, "invalid_rollback_target")
    assert flow.status(active) == "active"


def test_rollback_to_unknown_release_is_refused(flow: Flow) -> None:
    r = flow.client.post(
        f"{RELEASES}/{flow.active()}/rollback",
        json={"target_release_id": str(uuid.uuid4()), "reason": "x"},
    )
    refused(r, 409, "invalid_rollback_target")


def test_rollback_to_another_source_is_refused(flow: Flow) -> None:
    flow.active(source="demo")
    demo_old = flow.active(source="demo", metrics=BETTER)
    flow.active(source="demo", metrics=SLOWER)
    live = flow.active()
    r = flow.client.post(
        f"{RELEASES}/{live}/rollback",
        json={"target_release_id": demo_old, "reason": "x"},
    )
    refused(r, 409, "invalid_rollback_target")


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_rollback_needs_a_reason(flow: Flow, reason: str | None) -> None:
    first = flow.active()
    second = flow.active(metrics=BETTER)
    body = {"target_release_id": first}
    if reason is not None:
        body["reason"] = reason
    r = flow.client.post(f"{RELEASES}/{second}/rollback", json=body)
    assert r.status_code == 422


@pytest.mark.parametrize("action", ["canary", "promote"])
def test_unknown_fields_are_rejected(flow: Flow, action: str) -> None:
    r = flow.client.post(
        f"{RELEASES}/{flow.candidate()}/{action}", json={"force": True}
    )
    assert r.status_code == 422


@pytest.mark.parametrize("action", ["canary", "promote", "rollback"])
def test_unknown_release_is_not_found(flow: Flow, action: str) -> None:
    body = (
        {"target_release_id": str(uuid.uuid4()), "reason": "x"}
        if action == "rollback"
        else {}
    )
    r = flow.client.post(f"{RELEASES}/{uuid.uuid4()}/{action}", json=body)
    refused(r, 404, "not_found")


def test_read_unknown_release_is_not_found(flow: Flow) -> None:
    refused(flow.client.get(f"{RELEASES}/{uuid.uuid4()}"), 404, "not_found")


def test_list_is_newest_first_without_events(flow: Flow) -> None:
    first = flow.candidate()
    second = flow.candidate()
    releases = flow.client.get(RELEASES).json()["releases"]
    assert [r["id"] for r in releases] == [second, first]
    assert all(r["simulated"] is True and "events" not in r for r in releases)


def test_list_filters(flow: Flow) -> None:
    live = flow.candidate()
    demo = flow.candidate(source="demo")
    rejected = flow.passed(metrics=BAD)

    def ids(query: str) -> list[str]:
        body = flow.client.get(f"{RELEASES}?{query}").json()
        return [r["id"] for r in body["releases"]]

    assert ids("source=demo") == [demo]
    assert ids("status=rejected") == [rejected]
    assert ids("source=live&status=candidate") == [live]
    assert len(ids("limit=1")) == 1


@pytest.mark.parametrize("query", ["source=staging", "status=shipped", "limit=0"])
def test_invalid_list_filters_are_rejected(flow: Flow, query: str) -> None:
    assert flow.client.get(f"{RELEASES}?{query}").status_code == 422


def test_actor_without_authentication(flow: Flow) -> None:
    assert flow.events(flow.candidate())[0]["actor"] == "local-no-auth"


def test_actor_with_authentication(flow: Flow, settings: Settings) -> None:
    settings.auth_password_hash = "set"
    assert flow.events(flow.candidate())[0]["actor"] == "shared-password"


def test_events_carry_the_request_id(
    flow: Flow, sessions: sessionmaker[Session]
) -> None:
    r = flow.client.post(
        RELEASES,
        json={"version": "1.0", "evaluation_run_id": add_run(sessions)},
        headers={"x-request-id": "req-1"},
    )
    assert r.json()["events"][0]["request_id"] == "req-1"


def test_release_actions_change_no_live_settings(
    flow: Flow, settings: Settings
) -> None:
    before = settings.model_dump()
    first = flow.active()
    second = flow.active(metrics=BETTER)
    flow.client.post(
        f"{RELEASES}/{second}/rollback",
        json={"target_release_id": first, "reason": "x"},
    )
    assert settings.model_dump() == before


def test_rejected_release_is_never_successful(
    flow: Flow, sessions: sessionmaker[Session]
) -> None:
    rejected = flow.passed(metrics=BAD)
    client = flow.client
    client.post(f"{RELEASES}/{rejected}/promote", json={})
    client.post(f"{RELEASES}/{rejected}/canary", json={})
    client.post(
        f"{RELEASES}/{rejected}/rollback",
        json={"target_release_id": flow.active(), "reason": "x"},
    )
    with sessions() as session:
        row = session.get(Release, uuid.UUID(rejected))
        assert row is not None
        assert (row.status, row.gate_passed) == ("rejected", False)
