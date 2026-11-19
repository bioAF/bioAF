"""Interactive notebook session lifecycle: one set of decisions for every observer.

The session list, the periodic reconciliation pass, and (until it is retired) the launch's own
readiness check all decide through ``readiness.decide_launch`` and write through one conditional
update, so a stop that lands first always wins and no observer revives a finished session.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import ServiceState
from app.adapters.notebooks.readiness import decide_launch, launch_started_at
from app.adapters.registry import get_notebook_adapter
from app.adapters.session_status import transition_in
from app.models.notebook_session import NotebookSession

logger = logging.getLogger("bioaf.notebook_lifecycle")

STARTING = ("pending", "starting")
LIVE = ("running", "idle")


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
        decision = decide_launch(observed.status, observed.access_url, details, started=started, now=now)
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
            applied = await transition_in(
                db,
                expected_statuses=LIVE,
                status="failed",
                failure_reason=details.get("failure_reason") or "unknown",
                failure_message=details.get("failure_message") or "The session stopped unexpectedly.",
                lifecycle=_pending_cleanup(now),
                **target,
            )
        elif observed.status == ServiceState.RUNNING and observed.access_url and not ns.access_url:
            applied = await transition_in(db, expected_statuses=LIVE, access_url=observed.access_url, **target)
    if applied:
        await db.refresh(ns)
    return applied
