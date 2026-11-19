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


_UNSET = object()


def _transition_statement(
    *,
    organization_id: int,
    session_id: int,
    expected_statuses: tuple[str, ...],
    expected_job_ref: str | None,
    status: str | None,
    access_url,
    failure_reason: str | None,
    failure_message: str | None,
    lifecycle: dict | None,
    started_at=None,
):
    """The one conditional UPDATE every lifecycle transition uses (None when nothing to write)."""
    import json

    params: dict = {
        "id": session_id,
        "org": organization_id,
        "expected": list(expected_statuses),
        "ref": expected_job_ref,
    }
    assignments: list[str] = []
    if status is not None:
        assignments.append("status = :status")
        params["status"] = status
    if access_url is not _UNSET:
        assignments.append("access_url = :url")
        params["url"] = access_url
    if started_at is not None:
        assignments.append("started_at = COALESCE(started_at, :started_at)")
        params["started_at"] = started_at
    if failure_reason is not None or failure_message is not None:
        assignments.append("failure_reason = :reason, failure_message = :msg")
        params.update(reason=failure_reason, msg=failure_message)
    if lifecycle:
        assignments.append(
            "provider_metadata = COALESCE(provider_metadata, '{}'::jsonb) || jsonb_build_object("
            "'lifecycle', COALESCE(provider_metadata -> 'lifecycle', '{}'::jsonb) || CAST(:lifecycle AS jsonb))"
        )
        params["lifecycle"] = json.dumps(lifecycle)
    if not assignments:
        return None
    sql = (
        f"UPDATE compute_sessions SET {', '.join(assignments)} "
        "WHERE id = :id AND organization_id = :org AND status = ANY(:expected) "
        "AND compute_job_ref IS NOT DISTINCT FROM :ref"
    )
    return text(sql), params


async def transition_in(db, **kwargs) -> bool:
    """``transition_compute_session`` inside the caller's transaction (no commit). True if it applied."""
    kwargs.setdefault("access_url", _UNSET)
    for key in ("status", "failure_reason", "failure_message", "lifecycle", "started_at"):
        kwargs.setdefault(key, None)
    statement = _transition_statement(**kwargs)
    if statement is None:
        return False
    result = await db.execute(*statement)
    return bool(result.rowcount)


async def transition_compute_session(
    session_factory,
    *,
    organization_id: int | None,
    session_id: int,
    expected_statuses: tuple[str, ...],
    expected_job_ref: str | None,
    status: str | None = None,
    access_url: str | None | object = _UNSET,
    failure_reason: str | None = None,
    failure_message: str | None = None,
    lifecycle: dict | None = None,
) -> bool:
    """Change a session only while it is still in ``expected_statuses`` under ``expected_job_ref``.

    The compare and the write are one statement, so a stop (or a relaunch under another workload)
    that lands first always wins and this changes nothing. ``lifecycle`` keys are merged into
    ``provider_metadata.lifecycle``; every other metadata key is preserved. True if the row changed.
    Never raises (logged), like ``update_compute_session``.
    """
    if not session_factory or organization_id is None:
        logger.warning("Session %s: no session factory or organization; not updating it", session_id)
        return False
    try:
        async with session_factory() as db:
            applied = await transition_in(
                db,
                organization_id=organization_id,
                session_id=session_id,
                expected_statuses=expected_statuses,
                expected_job_ref=expected_job_ref,
                status=status,
                access_url=access_url,
                failure_reason=failure_reason,
                failure_message=failure_message,
                lifecycle=lifecycle,
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to update session %s in DB", session_id)
        return False
    if not applied:
        logger.info(
            "Session %s was not updated: it is no longer %s under workload %s",
            session_id,
            "/".join(expected_statuses),
            expected_job_ref,
        )
        return False
    logger.info("Updated session %s: status=%s lifecycle=%s", session_id, status, sorted((lifecycle or {}).keys()))
    return True
