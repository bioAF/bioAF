"""Interactive notebook session lifecycle: one set of decisions for every observer.

The session list, the periodic reconciliation pass, and (until it is retired) the launch's own
readiness check all decide through ``readiness.decide_launch`` and write through one conditional
update, so a stop that lands first always wins and no observer revives a finished session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import ServiceState
from app.adapters.notebooks.readiness import INTERACTIVE_SESSION_TYPES, decide_launch, launch_started_at
from app.adapters.registry import get_notebook_adapter
from app.adapters.session_status import transition_in
from app.models.notebook_session import NotebookSession

logger = logging.getLogger("bioaf.notebook_lifecycle")

STARTING = ("pending", "starting")
LIVE = ("running", "idle")
FINISHED = ("failed", "stopped")

# Bounded work per pass; the oldest outstanding items go first, so nothing is starved.
OBSERVE_BATCH = 50
CLEANUP_BATCH = 25
# Bound on observing or releasing one session; output saving has its own request timeouts.
ITEM_TIMEOUT_SECONDS = 60.0


def _pending_cleanup(now: datetime) -> dict:
    return {"cleanup": {"state": "pending", "updated_at": now.isoformat()}}


async def observe_session(db: AsyncSession, ns: NotebookSession, *, now: datetime | None = None) -> bool:
    """Read ``ns``'s workload and apply the shared decision; True if the row changed.

    Writes go through the caller's transaction and only apply while the row is still in the
    state that was read, under the same workload.
    """
    if not ns.compute_job_ref:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        observed = await get_notebook_adapter().get_session_status(
            session_id=ns.id,
            pod_name=ns.compute_job_ref,
            namespace=ns.provider_namespace or "bioaf-notebooks",
            session_type=ns.session_type,
        )
    except Exception:
        logger.debug("Session %s status read failed", ns.id, exc_info=True)
        return False

    target = {"organization_id": ns.organization_id, "session_id": ns.id, "expected_job_ref": ns.compute_job_ref}
    details = observed.provider_details or {}
    applied = False
    if ns.status in STARTING:
        started = launch_started_at(ns.provider_metadata, started_at=ns.started_at, created_at=ns.created_at)
        status = observed.status
        if ns.status == "pending" and details.get("workload_missing"):
            # Reserved but not created yet (the launch request may still be creating it): only
            # the overall deadline ends this, never the absence itself.
            status = ServiceState.STARTING
        decision = decide_launch(status, observed.access_url, details, started=started, now=now)
        if decision.action == "ready":
            applied = await transition_in(
                db,
                expected_statuses=STARTING,
                status="running",
                access_url=decision.access_url,
                started_at=now,
                **target,
            )
        elif decision.action == "fail":
            logger.warning("Notebook session %s failed to start: %s", ns.id, decision.reason)
            applied = await transition_in(
                db,
                expected_statuses=STARTING,
                status="failed",
                access_url=None,
                failure_reason=decision.reason,
                failure_message=decision.message,
                lifecycle=_pending_cleanup(now),
                **target,
            )
    elif ns.status in LIVE:
        if observed.status == ServiceState.ERROR:
            logger.warning("Notebook session %s's workload failed while running", ns.id)
            # It ran: whatever can still be saved is saved before its resources are released.
            lifecycle = _pending_cleanup(now)
            lifecycle["cleanup"]["save_outputs"] = True
            applied = await transition_in(
                db,
                expected_statuses=LIVE,
                status="failed",
                failure_reason=details.get("failure_reason") or "unknown",
                failure_message=details.get("failure_message") or "The session stopped unexpectedly.",
                lifecycle=lifecycle,
                **target,
            )
        elif observed.status == ServiceState.RUNNING and observed.access_url and not ns.access_url:
            applied = await transition_in(db, expected_statuses=LIVE, access_url=observed.access_url, **target)
    if applied:
        await db.refresh(ns)
    return applied


# -- the periodic pass ---------------------------------------------------------------------------


def _cleanup_state():
    return func.jsonb_extract_path_text(NotebookSession.provider_metadata, "lifecycle", "cleanup", "state")


def _cleanup_updated():
    return func.jsonb_extract_path_text(NotebookSession.provider_metadata, "lifecycle", "cleanup", "updated_at")


async def _ids(factory, query) -> list[int]:
    async with factory() as db:
        return list((await db.execute(query)).scalars().all())


async def _load(db: AsyncSession, session_id: int) -> NotebookSession | None:
    return (await db.execute(select(NotebookSession).where(NotebookSession.id == session_id))).scalar_one_or_none()


async def _record_cleanup(db, ns: NotebookSession, cleanup: dict) -> None:
    await transition_in(
        db,
        organization_id=ns.organization_id,
        session_id=ns.id,
        expected_statuses=(ns.status,),
        expected_job_ref=ns.compute_job_ref,
        lifecycle={"cleanup": cleanup},
    )


async def cleanup_session(db: AsyncSession, ns: NotebookSession, *, now: datetime) -> None:
    """Release a finished session's resources, verified, and record the outcome."""
    adapter = get_notebook_adapter()
    lifecycle = (ns.provider_metadata or {}).get("lifecycle") or {}
    cleanup = lifecycle.get("cleanup") or {}

    recorded_cluster = lifecycle.get("cluster")
    if recorded_cluster:
        current = await adapter.workload_cluster()
        if current is None:
            logger.warning("Notebook session %s: cluster identity unavailable; cleanup deferred", ns.id)
            return
        if current != recorded_cluster:
            logger.warning(
                "Notebook session %s was launched on %s but the cluster is now %s; not deleting automatically",
                ns.id,
                recorded_cluster,
                current,
            )
            await _record_cleanup(
                db,
                ns,
                {
                    "state": "blocked",
                    "detail": f"cluster changed (launched on {recorded_cluster}, now {current})",
                    "updated_at": now.isoformat(),
                },
            )
            return

    if cleanup.get("save_outputs"):
        from app.services.notebook_service import NotebookService

        await NotebookService.terminate_and_save_outputs(db, ns, ran=True, adapter=adapter)
        return

    workload = adapter.recorded_workload(ns)
    if workload is None:
        await _record_cleanup(
            db, ns, {"state": "blocked", "detail": "no usable recorded workload", "updated_at": now.isoformat()}
        )
        return
    release = await asyncio.wait_for(adapter.release_session_resources(workload), timeout=ITEM_TIMEOUT_SECONDS)
    if release.blocked:
        logger.warning(
            "Notebook session %s: resources under its recorded names belong to something else; "
            "an operator should review them",
            ns.id,
        )
    await _record_cleanup(db, ns, release.as_cleanup(now))


async def reconcile_pass(factory, *, now: datetime | None = None) -> dict:
    """One pass: observe starting launches, then release finished sessions' resources.

    Each item has its own short transaction; a failure is logged and the pass moves on.
    """
    now = now or datetime.now(timezone.utc)
    counts = {"observed": 0, "cleaned": 0, "errors": 0}

    starting = await _ids(
        factory,
        select(NotebookSession.id)
        .where(
            NotebookSession.status.in_(STARTING),
            NotebookSession.session_type.in_(INTERACTIVE_SESSION_TYPES),
            NotebookSession.compute_job_ref.isnot(None),
        )
        .order_by(NotebookSession.id)
        .limit(OBSERVE_BATCH),
    )
    for session_id in starting:
        try:
            async with factory() as db:
                ns = await _load(db, session_id)
                if ns is not None and ns.status in STARTING:
                    await asyncio.wait_for(observe_session(db, ns, now=now), timeout=ITEM_TIMEOUT_SECONDS)
                    await db.commit()
                    counts["observed"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Observing notebook session %s failed", session_id)

    state = _cleanup_state()
    finished = await _ids(
        factory,
        select(NotebookSession.id)
        .where(
            NotebookSession.status.in_(FINISHED),
            NotebookSession.session_type.in_(INTERACTIVE_SESSION_TYPES),
            NotebookSession.compute_job_ref.isnot(None),
            or_(state.is_(None), state == "pending"),
        )
        .order_by(_cleanup_updated().asc().nulls_first(), NotebookSession.id)
        .limit(CLEANUP_BATCH),
    )
    for session_id in finished:
        try:
            async with factory() as db:
                ns = await _load(db, session_id)
                if ns is not None and ns.status in FINISHED:
                    await cleanup_session(db, ns, now=now)
                    await db.commit()
                    counts["cleaned"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Cleaning up notebook session %s failed", session_id)
    try:
        await report_unrecorded_resources(factory)
    except Exception:
        # A read failure is never taken as an empty inventory; nothing is concluded this pass.
        logger.warning("Could not inventory notebook resources this pass", exc_info=True)
    return counts


# Report at most this many unrecorded sessions per pass.
REPORT_LIMIT = 20


async def report_unrecorded_resources(factory) -> None:
    """Log labelled notebook resources whose session has no record, for an operator to review.

    Never deletes: with no trustworthy record there is no verified owner. New launches record
    their workload before creating it, so this only finds leftovers from before that.
    """
    labelled = await get_notebook_adapter().labelled_workloads()
    if not labelled:
        return
    ids = sorted(labelled)
    known = set(await _ids(factory, select(NotebookSession.id).where(NotebookSession.id.in_(ids))))
    unrecorded = [i for i in ids if i not in known]
    for session_id in unrecorded[:REPORT_LIMIT]:
        logger.warning(
            "Notebook resources %s carry session %s, which has no session record; not deleting them. "
            "An operator should review them.",
            ", ".join(sorted(labelled[session_id])),
            session_id,
        )
