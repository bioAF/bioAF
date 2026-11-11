"""The readiness pollers' write to a compute session row.

The notebook Kubernetes adapter and the GCE/EC2 work-node adapters each poll a session they just
launched and record the outcome. Their update was the same SQL three times; it lives here once.

Scoped by session id AND organization. The id comes from a row the server created, so this is not
a fix for a demonstrated exploit; it makes the organization boundary hold at the write as well,
for every poller, without trusting that a bare id still names the session the poller was started
for.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger("bioaf.adapters.session_status")


async def update_compute_session(
    session_factory,
    *,
    organization_id: int | None,
    session_id: int,
    status: str,
    access_url: str | None,
    failure_reason: str | None = None,
    failure_message: str | None = None,
) -> bool:
    """Write status/access_url (and the failure fields only when given). True if a row changed.

    Owns its transaction (a fresh session from ``session_factory``, committed here). Never raises:
    a poller has no caller to report to, so failures are logged.
    """
    if not session_factory:
        logger.warning("No session_factory, cannot update session %s", session_id)
        return False
    if organization_id is None:
        logger.warning("No organization for session %s; not updating it", session_id)
        return False

    params: dict = {"status": status, "url": access_url, "id": session_id, "org": organization_id}
    assignments = "status = :status, access_url = :url"
    if failure_reason is not None or failure_message is not None:
        # Only when set, so a row the service layer already explained is not clobbered.
        assignments += ", failure_reason = :reason, failure_message = :msg"
        params.update(reason=failure_reason, msg=failure_message)
    try:
        async with session_factory() as db:
            result = await db.execute(
                text(f"UPDATE compute_sessions SET {assignments} WHERE id = :id AND organization_id = :org"),
                params,
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to update session %s in DB", session_id)
        return False
    if not result.rowcount:
        logger.warning("Session %s of organization %s not found; nothing updated", session_id, organization_id)
        return False
    logger.info(
        "Updated session %s: status=%s access_url=%s failure_reason=%s", session_id, status, access_url, failure_reason
    )
    return True
