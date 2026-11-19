"""Interactive notebook session lifecycle: one set of decisions for every observer.

The session list, the periodic reconciliation pass, and (until it is retired) the launch's own
readiness check all decide through ``readiness.decide_launch`` and write through one conditional
update, so a stop that lands first always wins and no observer revives a finished session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import ServiceState, SessionStatus
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
# A status read that takes longer is uncertainty: the session is still judged against its deadline.
STATUS_READ_TIMEOUT_SECONDS = 30.0


def _pending_cleanup(now: datetime) -> dict:
    return {"cleanup": {"state": "pending", "updated_at": now.isoformat()}}


async def observe_session(
    db: AsyncSession, ns: NotebookSession, *, now: datetime | None = None, record_observation: bool = False
) -> bool:
    """Read ``ns``'s workload and apply the shared decision; True if the row changed.

    Writes go through the caller's transaction and only apply while the row is still in the
    state that was read, under the same workload.
    """
    if not ns.compute_job_ref:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        observed = await asyncio.wait_for(
            get_notebook_adapter().get_session_status(
                session_id=ns.id,
                pod_name=ns.compute_job_ref,
                namespace=ns.provider_namespace or "bioaf-notebooks",
                session_type=ns.session_type,
            ),
            timeout=STATUS_READ_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # including a read that hangs past its bound
        # Uncertainty, not failure: the overall startup deadline still applies.
        logger.warning("Session %s status read failed (%s)", ns.id, type(exc).__name__)
        observed = SessionStatus(session_id=str(ns.id), status=ServiceState.UNKNOWN)

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
        elif record_observation:
            # The periodic pass rotates through starting sessions by this timestamp.
            await transition_in(db, expected_statuses=STARTING, lifecycle={"observed_at": now.isoformat()}, **target)
            return False
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


# -- the one guard before any exec or deletion ----------------------------------------------------


class Verdict:
    """``ok`` (act on ``workload``), ``unmanaged`` (no lifecycle: act as before), or a ``cleanup``
    record to store instead of acting: ``blocked`` (never automatically) or ``pending`` (later)."""

    __slots__ = ("kind", "workload", "cleanup")

    def __init__(self, kind: str, workload=None, cleanup: dict | None = None):
        self.kind = kind
        self.workload = workload
        self.cleanup = cleanup


async def verify_workload(adapter, ns: NotebookSession, *, now: datetime | None = None) -> Verdict:
    """Resolve ``ns``'s workload under the shared identity rules and check it is on the recorded cluster.

    Every path that execs into or deletes a session's resources (stop, both idle stops, periodic
    cleanup) asks this first. A conflicting identity or a changed cluster blocks automatic action;
    a cluster that cannot be confirmed right now defers it.
    """
    now = now or datetime.now(timezone.utc)
    if getattr(adapter, "manages_workloads", False) is not True or ns.session_type not in INTERACTIVE_SESSION_TYPES:
        return Verdict("unmanaged")
    workload, why = adapter.resolve_workload(ns)
    if workload is None:
        return Verdict("blocked", cleanup={"state": "blocked", "detail": why, "updated_at": now.isoformat()})
    lifecycle = (ns.provider_metadata or {}).get("lifecycle") or {}
    recorded = lifecycle.get("cluster")
    if not recorded and lifecycle.get("reserved_at"):
        # A reserved launch always records its cluster; one without it is never acted on here.
        logger.warning("Notebook session %s's launch recorded no cluster; not acting automatically", ns.id)
        return Verdict(
            "blocked",
            cleanup={"state": "blocked", "detail": "launch recorded no cluster", "updated_at": now.isoformat()},
        )
    if recorded:
        current = await adapter.workload_cluster()
        if current is None:
            logger.warning("Notebook session %s: cluster identity unavailable; acting on it later", ns.id)
            return Verdict("deferred", cleanup={"state": "pending", "updated_at": now.isoformat()})
        if current != recorded:
            logger.warning(
                "Notebook session %s was launched on %s but the cluster is now %s; not acting automatically",
                ns.id,
                recorded,
                current,
            )
            return Verdict(
                "blocked",
                cleanup={
                    "state": "blocked",
                    "detail": f"cluster changed (launched on {recorded}, now {current})",
                    "updated_at": now.isoformat(),
                },
            )
    return Verdict("ok", workload=workload)


# -- the periodic pass ---------------------------------------------------------------------------


def _lifecycle_field(name: str):
    return func.jsonb_extract_path_text(NotebookSession.provider_metadata, "lifecycle", name)


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
    # An owed save is never erased by a cleanup outcome; only a confirmed save clears it.
    owed = (((ns.provider_metadata or {}).get("lifecycle") or {}).get("cleanup") or {}).get("save_outputs")
    if owed:
        cleanup = {**cleanup, "save_outputs": True}
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
    cleanup = ((ns.provider_metadata or {}).get("lifecycle") or {}).get("cleanup") or {}

    if cleanup.get("save_outputs"):
        # The shared stop path verifies first, saves what it can, then releases.
        from app.services.notebook_service import NotebookService

        await NotebookService.terminate_and_save_outputs(db, ns, ran=True, adapter=adapter)
        return

    verdict = await verify_workload(adapter, ns, now=now)
    if verdict.kind == "unmanaged":
        return
    if verdict.kind != "ok":
        if verdict.kind == "blocked":
            await _record_cleanup(db, ns, verdict.cleanup or {})
        return
    release = await asyncio.wait_for(adapter.release_session_resources(verdict.workload), timeout=ITEM_TIMEOUT_SECONDS)
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
    if getattr(get_notebook_adapter(), "manages_workloads", False) is not True:
        return counts

    starting = await _ids(
        factory,
        select(NotebookSession.id)
        .where(
            NotebookSession.status.in_(STARTING),
            NotebookSession.session_type.in_(INTERACTIVE_SESSION_TYPES),
            NotebookSession.compute_job_ref.isnot(None),
        )
        # Least recently observed first, so sessions that keep erroring cannot crowd out others.
        .order_by(_lifecycle_field("observed_at").asc().nulls_first(), NotebookSession.id)
        .limit(OBSERVE_BATCH),
    )
    for session_id in starting:
        try:
            async with factory() as db:
                ns = await _load(db, session_id)
                if ns is not None and ns.status in STARTING:
                    await asyncio.wait_for(
                        observe_session(db, ns, now=now, record_observation=True), timeout=ITEM_TIMEOUT_SECONDS
                    )
                    await db.commit()
                    counts["observed"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Observing notebook session %s failed", session_id)

    state = _cleanup_state()
    # Every finished interactive session, including legacy records that name their workload only in
    # older columns (or not at all: the launch naming rule then applies, and labels are verified).
    finished_rows = (
        NotebookSession.status.in_(FINISHED),
        NotebookSession.session_type.in_(INTERACTIVE_SESSION_TYPES),
    )
    # Two queues, so a backlog of never-inspected older sessions never delays a pending retry:
    # pending cleanup, least recently attempted first; then first inspections, oldest first.
    pending = await _ids(
        factory,
        select(NotebookSession.id)
        .where(*finished_rows, state == "pending")
        .order_by(_cleanup_updated().asc().nulls_first(), NotebookSession.id)
        .limit(CLEANUP_BATCH),
    )
    uninspected = await _ids(
        factory,
        select(NotebookSession.id)
        .where(*finished_rows, state.is_(None))
        .order_by(NotebookSession.id)
        .limit(CLEANUP_BATCH),
    )
    finished = pending + uninspected
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
