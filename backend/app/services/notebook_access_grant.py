"""Who may open a notebook session through the protected gateway, and the credentials that carry it
(plan 03.5A).

Opening is for the session's owner only: permission to list or stop other users' sessions is not
permission to read their files or run code. A bootstrap ticket is single use, expires in 60 seconds and
is bound to the live Pod confirmed at issue. The gateway exchanges it once for an access credential: a
JWT signed with the existing key under its own purpose, scoped to one session and Pod, which the API
middleware and the content-token validator both refuse.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_notebook_adapter
from app.config import settings
from app.exceptions import ConnectionUnavailableError, NotFoundError, PermissionDeniedError
from app.models.notebook_access_ticket import NotebookAccessTicket
from app.services.notebook_service import NotebookService

NOTEBOOK_ACCESS_PURPOSE = "notebook_access"
TICKET_LIFETIME = timedelta(seconds=60)
ACCESS_LIFETIME = timedelta(hours=8)
OPENABLE_STATES = ("running", "idle")
OPENABLE_TYPES = ("jupyter", "rstudio")


@dataclass(frozen=True)
class AccessGrant:
    user_id: int
    organization_id: int
    session_id: int
    pod_uid: str
    pod_name: str
    namespace: str
    # When the ticket or credential carrying this grant was issued, and when the credential expires
    # (None for a grant fresh from a ticket, whose own expiry was checked when it was used).
    issued_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class IssuedTicket:
    ticket: str
    expires_in: int


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


async def issue_ticket(session: AsyncSession, *, session_id: int, user_id: int, org_id: int) -> IssuedTicket:
    """A bootstrap ticket for the caller's own running session, bound to its live Pod.

    NotFoundError for a session outside the caller's Organization, PermissionDeniedError for another
    user's session, ConnectionUnavailableError when the session is not running or its Pod cannot be
    confirmed. Nothing is stored unless every check passes.
    """
    nb = await NotebookService.get_session(session, session_id, org_id=org_id)
    if nb is None:
        raise NotFoundError("Session not found")
    if nb.user_id != user_id:
        raise PermissionDeniedError("Only the person who started this session can open it.")
    if nb.session_type not in OPENABLE_TYPES:
        raise ConnectionUnavailableError("This session has no notebook to open.", reason="unsupported_session_type")
    if nb.status not in OPENABLE_STATES:
        raise ConnectionUnavailableError("This session is not running yet.", reason="not_running")
    adapter = get_notebook_adapter()
    workload, why = adapter.resolve_workload(nb)
    if workload is None:
        raise ConnectionUnavailableError("bioAF could not confirm this session's workload.", reason="unconfirmed")
    pod_uid = await adapter.live_pod_uid(workload)
    ticket = secrets.token_urlsafe(32)
    session.add(
        NotebookAccessTicket(
            ticket_hash=_hash(ticket),
            user_id=user_id,
            organization_id=org_id,
            session_id=nb.id,
            pod_name=workload.pod_name,
            namespace=workload.namespace,
            pod_uid=pod_uid,
            expires_at=datetime.now(timezone.utc) + TICKET_LIFETIME,
        )
    )
    await session.flush()
    return IssuedTicket(ticket=ticket, expires_in=int(TICKET_LIFETIME.total_seconds()))


async def consume_ticket(session: AsyncSession, ticket: str) -> AccessGrant | None:
    """The grant a valid, unused, unexpired ticket carries, marking it used; otherwise None.

    One conditional UPDATE: concurrent exchanges of the same ticket admit exactly one.
    """
    if not ticket or len(ticket) > 200:
        return None
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            update(NotebookAccessTicket)
            .where(
                NotebookAccessTicket.ticket_hash == _hash(ticket),
                NotebookAccessTicket.used_at.is_(None),
                NotebookAccessTicket.expires_at > now,
            )
            .values(used_at=now)
            .returning(
                NotebookAccessTicket.user_id,
                NotebookAccessTicket.organization_id,
                NotebookAccessTicket.session_id,
                NotebookAccessTicket.pod_uid,
                NotebookAccessTicket.pod_name,
                NotebookAccessTicket.namespace,
                NotebookAccessTicket.expires_at,
            )
        )
    ).first()
    if row is None:
        return None
    return AccessGrant(
        user_id=row.user_id,
        organization_id=row.organization_id,
        session_id=row.session_id,
        pod_uid=row.pod_uid,
        pod_name=row.pod_name,
        namespace=row.namespace,
        issued_at=row.expires_at - TICKET_LIFETIME,
    )


def access_credential(grant: AccessGrant, *, now: datetime | None = None) -> str:
    """The gateway cookie value for one session: a purpose-scoped JWT, never an API session."""
    issued = now or datetime.now(timezone.utc)
    payload = {
        "purpose": NOTEBOOK_ACCESS_PURPOSE,
        "sub": str(grant.user_id),
        "org_id": grant.organization_id,
        "sid": grant.session_id,
        "pod_uid": grant.pod_uid,
        "pod": grant.pod_name,
        "ns": grant.namespace,
        "jti": uuid.uuid4().hex,
        # Fractional (RFC 7519 allows it), so a credential issued just before signing out is told
        # apart from one issued just after.
        "iat": issued.timestamp(),
        "exp": issued + ACCESS_LIFETIME,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def read_access_credential(token: str | None, *, session_id: int) -> AccessGrant | None:
    """The grant in a valid access credential for ``session_id``; None for anything else."""
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "purpose"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != NOTEBOOK_ACCESS_PURPOSE or payload.get("sid") != session_id:
        return None
    try:
        return AccessGrant(
            user_id=int(payload["sub"]),
            organization_id=int(payload["org_id"]),
            session_id=int(payload["sid"]),
            pod_uid=str(payload["pod_uid"]),
            pod_name=str(payload["pod"]),
            namespace=str(payload["ns"]),
            issued_at=datetime.fromtimestamp(float(payload["iat"]), timezone.utc),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), timezone.utc),
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


async def revoke_user_access(session: AsyncSession, user_id: int) -> None:
    """End every notebook gateway credential ``user_id`` holds (signing out of bioAF)."""
    from app.models.user import User

    await session.execute(
        update(User).where(User.id == user_id).values(notebook_access_revoked_at=datetime.now(timezone.utc))
    )
