"""The check every notebook gateway request and WebSocket passes (plan 03.5A).

A valid access credential is necessary but not sufficient: the database must still show the same
owner, Organization, an active user and a running session with the recorded Pod, and the cluster must
still show the Pod UID the grant was issued for. Anything else refuses; nothing here stops a session
or touches its files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select

from app.models.notebook_session import ComputeSession
from app.models.user import User
from app.services.notebook_access_grant import OPENABLE_STATES, OPENABLE_TYPES, AccessGrant

# Pod UID confirmations are reused this long, so an IDE's burst of requests is not a burst of API reads.
POD_CONFIRMATION_SECONDS = 30.0


@dataclass(frozen=True)
class SessionAccess:
    session_type: str
    # The IDE's own secret, added upstream only (None for sessions launched before the gateway).
    ide_secret: str | None


class PodConfirmations:
    def __init__(self, ttl: float = POD_CONFIRMATION_SECONDS):
        self._ttl = ttl
        self._seen: dict[tuple[int, str, str], tuple[str, float]] = {}

    async def uid(self, adapter, workload) -> str:
        key = (workload.session_id, workload.namespace, workload.pod_name)
        now = time.monotonic()
        hit = self._seen.get(key)
        if hit and now - hit[1] < self._ttl:
            return hit[0]
        uid = await adapter.live_pod_uid(workload)
        self._seen[key] = (uid, now)
        return uid


async def current_access(factory, adapter, grant: AccessGrant, confirmations: PodConfirmations) -> SessionAccess | None:
    async with factory() as session:
        row = (
            await session.execute(
                select(ComputeSession, User)
                .join(User, User.id == ComputeSession.user_id)
                .where(ComputeSession.id == grant.session_id)
            )
        ).first()
    if row is None:
        return None
    nb, user = row
    if (
        nb.organization_id != grant.organization_id
        or nb.user_id != grant.user_id
        or user.organization_id != grant.organization_id
        or user.status != "active"
        or nb.status not in OPENABLE_STATES
        or nb.session_type not in OPENABLE_TYPES
    ):
        return None
    workload, _why = adapter.resolve_workload(nb)
    if workload is None or (workload.namespace, workload.pod_name) != (grant.namespace, grant.pod_name):
        return None
    try:
        if await confirmations.uid(adapter, workload) != grant.pod_uid:
            return None
    except Exception:
        return None
    return SessionAccess(session_type=nb.session_type, ide_secret=nb.notebook_access_secret)
