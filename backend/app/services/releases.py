"""Simulated release records: candidate, canary, promotion and rollback.

Nothing here reads or writes live settings or the document index. A release
records a decision; gate_for_run alone decides whether it may pass.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logs import request_id
from app.db.models import EvaluationRun, Release, ReleaseEvent
from app.services.evaluation.report import (
    LOWER_IS_BETTER,
    GateResult,
    gate_for_run,
)

BASELINE_STATUSES = ("active", "superseded")
# Sizes and costs: they have no better direction.
NOT_COMPARED = {
    "examples",
    "errors",
    "prompt_tokens_total",
    "completion_tokens_total",
}


def actor_for(settings: Settings) -> str:
    return "shared-password" if settings.auth_password_hash else "local-no-auth"


def _record(
    release: Release,
    event: str,
    actor: str,
    from_status: str | None = None,
    to_status: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    release.events.append(
        ReleaseEvent(
            event=event,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            reason=reason,
            request_id=request_id.get(),
            details=details or {},
        )
    )


def _get(session: Session, release_id: uuid.UUID) -> Release:
    release = session.get(Release, release_id, with_for_update=True)
    if release is None:
        raise ApiError(404, "not_found", "release not found")
    return release


def _active(session: Session, source: str) -> Release | None:
    return session.scalars(
        select(Release)
        .where(Release.source == source, Release.status == "active")
        .with_for_update()
    ).first()


def _gate(release: Release, settings: Settings) -> GateResult | None:
    return gate_for_run(
        settings.eval_dir,
        release.dataset_version,
        release.metrics,
        release.dataset_checksum,
    )


def _transition_refused(release: Release, action: str) -> ApiError:
    return ApiError(
        409, "invalid_transition", f"a {release.status} release cannot {action}"
    )


def _commit_transition(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        # Another promotion took the active slot first; the index refused this one.
        session.rollback()
        raise ApiError(
            409, "invalid_transition", "another release changed first; reload"
        ) from None


def snapshot(run: EvaluationRun) -> dict[str, Any]:
    """What a release keeps from its run. Frozen once stored."""
    return {
        "evaluation_run_id": run.id,
        "dataset_version": run.dataset_version,
        "dataset_checksum": run.dataset_checksum,
        "prompt_version": run.prompt_version,
        "chat_model": run.chat_model,
        "embed_model": run.embed_model,
        "top_k": run.top_k,
        "min_score": run.min_score,
        "config": run.config,
        "metrics": run.metrics or {},
    }


def create_release(
    session: Session, version: str, run_id: uuid.UUID, actor: str
) -> Release:
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", "evaluation run not found")
    if run.status != "done":
        raise ApiError(
            409,
            "evaluation_not_completed",
            f"evaluation run is {run.status}; only a completed run can be released",
        )
    release = Release(
        version=version, source=run.source, status="candidate", **snapshot(run)
    )
    _record(release, "created", actor, to_status="candidate")
    session.add(release)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ApiError(
            409, "version_exists", f"release {version} already exists"
        ) from None
    return release


def compare(candidate: Release, baseline: Release | None) -> dict[str, Any]:
    """A record of the canary moment. Regressions are shown, never decisive."""
    if baseline is None:
        return {
            "status": "no_baseline",
            "baseline_release_id": None,
            "baseline_run_id": None,
            "metrics": [],
        }
    same_dataset = candidate.dataset_checksum == baseline.dataset_checksum
    return {
        "status": "comparable" if same_dataset else "dataset_changed",
        "baseline_release_id": str(baseline.id),
        "baseline_run_id": str(baseline.evaluation_run_id),
        # Deltas across different datasets would mislead.
        "metrics": _metric_rows(candidate, baseline) if same_dataset else [],
    }


def _metric_rows(candidate: Release, baseline: Release) -> list[dict[str, Any]]:
    rows = []
    for name in sorted(candidate.metrics.keys() - NOT_COMPARED):
        new, old = candidate.metrics[name], baseline.metrics.get(name)
        if not isinstance(new, int | float) or not isinstance(old, int | float):
            continue
        delta = round(new - old, 4)
        better = delta < 0 if name in LOWER_IS_BETTER else delta > 0
        rows.append(
            {
                "name": name,
                "candidate": new,
                "baseline": old,
                "delta": delta,
                "direction": "same"
                if delta == 0
                else ("improved" if better else "regressed"),
            }
        )
    return rows


def _baseline(
    session: Session, release: Release, baseline_id: uuid.UUID | None
) -> Release | None:
    if baseline_id is None:
        return _active(session, release.source)
    baseline = session.get(Release, baseline_id)
    if (
        baseline is None
        or baseline.source != release.source
        or baseline.status not in BASELINE_STATUSES
    ):
        raise ApiError(
            409,
            "invalid_baseline",
            "the baseline must be an active or superseded release of the same source",
        )
    return baseline


def run_canary(
    session: Session,
    release_id: uuid.UUID,
    baseline_id: uuid.UUID | None,
    settings: Settings,
    actor: str,
) -> Release:
    """The run behind the candidate is the canary evaluation; none is started."""
    release = _get(session, release_id)
    if release.status != "candidate":
        raise _transition_refused(release, "run a canary")
    gate = _gate(release, settings)
    if gate is None:
        raise ApiError(
            409,
            "gate_unavailable",
            f"no quality thresholds exist for dataset {release.dataset_version}",
        )
    baseline = _baseline(session, release, baseline_id)
    release.comparison = compare(release, baseline)
    release.baseline_release_id = baseline.id if baseline else None
    release.gate_passed = gate.passed
    release.gate_failures = gate.failures
    release.status = "canary_passed" if gate.passed else "rejected"
    _record(
        release,
        "canary_passed" if gate.passed else "canary_failed",
        actor,
        "candidate",
        release.status,
    )
    _commit_transition(session)
    return release


def _promotion_refusal(release: Release, settings: Settings) -> ApiError | None:
    if release.status != "canary_passed":
        return _transition_refused(release, "be promoted")
    gate = _gate(release, settings)
    if gate is None:
        return ApiError(
            409, "gate_failed", "the quality thresholds for this dataset are gone"
        )
    if not gate.passed:
        return ApiError(409, "gate_failed", "; ".join(gate.failures))
    return None


def promote(
    session: Session,
    release_id: uuid.UUID,
    reason: str | None,
    settings: Settings,
    actor: str,
) -> Release:
    release = _get(session, release_id)
    if (refusal := _promotion_refusal(release, settings)) is not None:
        _record(
            release,
            "promotion_refused",
            actor,
            release.status,
            release.status,
            reason,
            {"code": refusal.code},
        )
        session.commit()
        raise refusal
    previous = _active(session, release.source)
    if previous is not None:
        previous.status = "superseded"
        _record(
            previous,
            "superseded",
            actor,
            "active",
            "superseded",
            details={"superseded_by": str(release.id)},
        )
        # Written first, or the one-active index refuses the promotion below.
        session.flush()
    release.status = "active"
    _record(
        release,
        "promoted",
        actor,
        "canary_passed",
        "active",
        reason,
        {"previous_release_id": str(previous.id) if previous else None},
    )
    _commit_transition(session)
    return release


def rollback(
    session: Session,
    release_id: uuid.UUID,
    target_id: uuid.UUID,
    reason: str,
    actor: str,
) -> Release:
    """Reactivates a named earlier release. No new release is created."""
    release = _get(session, release_id)
    if release.status != "active":
        raise _transition_refused(release, "roll back")
    target = session.get(Release, target_id, with_for_update=True)
    if (
        target is None
        or target.source != release.source
        or (target.status != "superseded")
    ):
        raise ApiError(
            409,
            "invalid_rollback_target",
            "the target must be a superseded release of the same source",
        )
    release.status = "rolled_back"
    _record(
        release,
        "rolled_back",
        actor,
        "active",
        "rolled_back",
        reason,
        {"target_release_id": str(target.id)},
    )
    # Written first, or the one-active index refuses the restore below.
    session.flush()
    target.status = "active"
    _record(
        target,
        "restored",
        actor,
        "superseded",
        "active",
        reason,
        {"rolled_back_release_id": str(release.id)},
    )
    _commit_transition(session)
    return release


def release_to_dict(release: Release, events: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(release.id),
        "version": release.version,
        "source": release.source,
        "status": release.status,
        "simulated": True,
        "evaluation_run_id": str(release.evaluation_run_id),
        "dataset_version": release.dataset_version,
        "dataset_checksum": release.dataset_checksum,
        "prompt_version": release.prompt_version,
        "chat_model": release.chat_model,
        "embed_model": release.embed_model,
        "top_k": release.top_k,
        "min_score": release.min_score,
        "config": release.config,
        "metrics": release.metrics,
        "gate_passed": release.gate_passed,
        "gate_failures": release.gate_failures,
        "baseline_release_id": (
            str(release.baseline_release_id) if release.baseline_release_id else None
        ),
        "comparison": release.comparison,
        "created_at": release.created_at.isoformat(),
    }
    if events:
        body["events"] = [
            {
                "event": e.event,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "actor": e.actor,
                "reason": e.reason,
                "request_id": e.request_id,
                "details": e.details,
                "created_at": e.created_at.isoformat(),
            }
            for e in release.events
        ]
    return body
