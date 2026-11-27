"""Plan 03.5A: authorized Open of a notebook session through the protected gateway.

The caller gets a single-use, 60-second bootstrap ticket for their own running session, bound to the
live Pod; the gateway exchanges it once for a session-scoped access cookie. Neither credential is an
API session.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.config import settings
from app.exceptions import ConnectionUnavailableError
from app.models.notebook_access_ticket import NotebookAccessTicket
from app.models.notebook_session import ComputeSession
from app.services import notebook_access_grant as grants
from app.services.auth_service import AuthService


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def running(session, comp_bio_user):
    nb = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="running",
        compute_job_ref="bioaf-notebook-41",
        provider_metadata={"pod_name": "bioaf-notebook-41", "namespace": "bioaf-notebooks"},
    )
    session.add(nb)
    await session.commit()
    return nb


@pytest.fixture
def live_pod():
    adapter = MagicMock(name="notebook adapter")

    async def uid(workload):
        return "pod-uid-1"

    adapter.resolve_workload = MagicMock(
        side_effect=lambda ns: (MagicMock(namespace="bioaf-notebooks", pod_name="bioaf-notebook-41"), None)
    )
    adapter.live_pod_uid = MagicMock(side_effect=uid)
    with patch("app.services.notebook_access_grant.get_notebook_adapter", return_value=adapter):
        yield adapter


async def _open(client, token, sid):
    return await client.post(f"/api/v1/notebooks/sessions/{sid}/access", headers=_bearer(token))


# -- Opening ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_owner_gets_a_single_use_ticket_bound_to_the_live_pod(
    client, session, comp_bio_token, running, live_pod
):
    response = await _open(client, comp_bio_token, running.id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry_url"].endswith(":8443/_bioaf/enter")
    assert body["entry_url"].startswith("https://")
    assert body["expires_in"] == 60
    ticket = body["ticket"]
    assert len(ticket) >= 40
    # Only a hash is stored; the ticket itself appears nowhere in the database.
    row = (await session.execute(select(NotebookAccessTicket))).scalar_one()
    assert ticket not in (row.ticket_hash or "")
    assert (row.session_id, row.pod_uid, row.pod_name, row.namespace) == (
        running.id,
        "pod-uid-1",
        "bioaf-notebook-41",
        "bioaf-notebooks",
    )
    assert row.used_at is None


@pytest.mark.asyncio
async def test_permission_to_manage_sessions_is_not_access_to_another_users_session(
    client, admin_token, running, live_pod
):
    response = await _open(client, admin_token, running.id)
    assert response.status_code == 403
    assert "ticket" not in response.json()


@pytest.mark.asyncio
async def test_another_organizations_session_is_not_found(client, session, comp_bio_token, comp_bio_user, live_pod):
    from app.models.organization import Organization
    from app.models.user import User

    other = Organization(name="Elsewhere")
    session.add(other)
    await session.flush()
    stranger = User(
        email="s@else.org", password_hash="x", role_id=comp_bio_user.role_id, organization_id=other.id, status="active"
    )
    session.add(stranger)
    await session.flush()
    theirs = ComputeSession(
        user_id=stranger.id,
        organization_id=other.id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="running",
        compute_job_ref="bioaf-notebook-77",
    )
    session.add(theirs)
    await session.commit()
    assert (await _open(client, comp_bio_token, theirs.id)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["starting", "stopping", "stopped", "failed"])
async def test_only_a_running_session_can_be_opened(client, session, comp_bio_token, running, live_pod, status):
    running.status = status
    await session.commit()
    response = await _open(client, comp_bio_token, running.id)
    assert response.status_code == 409
    live_pod.live_pod_uid.assert_not_called()


@pytest.mark.asyncio
async def test_a_pod_that_cannot_be_confirmed_gets_no_ticket(client, session, comp_bio_token, running, live_pod):
    async def unavailable(workload):
        raise ConnectionUnavailableError("The session's workload could not be confirmed.", reason="unconfirmed")

    live_pod.live_pod_uid.side_effect = unavailable
    response = await _open(client, comp_bio_token, running.id)
    assert response.status_code == 409
    assert (await session.execute(select(NotebookAccessTicket))).scalars().all() == []


@pytest.mark.asyncio
async def test_opening_is_audited_without_the_ticket(client, session, comp_bio_token, running, live_pod):
    from app.models.audit_log import AuditLog

    ticket = (await _open(client, comp_bio_token, running.id)).json()["ticket"]
    rows = (await session.execute(select(AuditLog).where(AuditLog.action == "open_access"))).scalars().all()
    assert len(rows) == 1
    assert ticket not in str(rows[0].details_json)


# -- Exchanging the ticket ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ticket_is_exchanged_once(client, session, comp_bio_token, running, live_pod):
    ticket = (await _open(client, comp_bio_token, running.id)).json()["ticket"]
    first = await grants.consume_ticket(session, ticket)
    await session.commit()
    second = await grants.consume_ticket(session, ticket)
    assert first is not None and first.session_id == running.id
    assert second is None


@pytest.mark.asyncio
async def test_an_expired_or_unknown_ticket_is_refused(client, session, comp_bio_token, running, live_pod):
    ticket = (await _open(client, comp_bio_token, running.id)).json()["ticket"]
    await session.execute(text("UPDATE notebook_access_tickets SET expires_at = now() - interval '1 second'"))
    await session.commit()
    assert await grants.consume_ticket(session, ticket) is None
    assert await grants.consume_ticket(session, "not-a-ticket") is None


@pytest.mark.asyncio
async def test_two_concurrent_exchanges_of_one_ticket_admit_one(client, comp_bio_token, running, live_pod):
    import asyncio

    from app.database import async_session_factory

    ticket = (await _open(client, comp_bio_token, running.id)).json()["ticket"]

    async def exchange():
        async with async_session_factory() as s:
            grant = await grants.consume_ticket(s, ticket)
            await s.commit()
            return grant

    results = await asyncio.gather(exchange(), exchange(), exchange())
    assert sum(r is not None for r in results) == 1


# -- The access credential ----------------------------------------------------------------------


def _grant(**over):
    values = dict(
        user_id=5,
        organization_id=3,
        session_id=41,
        pod_uid="pod-uid-1",
        pod_name="bioaf-notebook-41",
        namespace="bioaf-notebooks",
    )
    values.update(over)
    return grants.AccessGrant(**values)


def test_the_access_credential_is_scoped_to_one_session_and_expires():
    token = grants.access_credential(_grant())
    claims = grants.read_access_credential(token, session_id=41)
    assert (claims.user_id, claims.organization_id, claims.session_id, claims.pod_uid) == (5, 3, 41, "pod-uid-1")
    assert grants.read_access_credential(token, session_id=42) is None
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["purpose"] == grants.NOTEBOOK_ACCESS_PURPOSE
    assert payload["exp"] - payload["iat"] <= 8 * 3600

    expired = grants.access_credential(_grant(), now=datetime.now(timezone.utc) - timedelta(hours=9))
    assert grants.read_access_credential(expired, session_id=41) is None


def test_an_api_session_token_is_not_an_access_credential():
    api = AuthService.create_token(5, "a@b.c", 1, 3)
    assert grants.read_access_credential(api, session_id=41) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [grants.NOTEBOOK_ACCESS_PURPOSE, "setup", "invite"])
async def test_no_purpose_token_is_accepted_as_an_api_session(client, comp_bio_user, purpose):
    token = jwt.encode(
        {
            "sub": str(comp_bio_user.id),
            "org_id": comp_bio_user.organization_id,
            "role_id": comp_bio_user.role_id,
            "purpose": purpose,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get("/api/v1/notebooks/sessions", headers=_bearer(token))
    assert response.status_code == 401


def test_the_content_token_validator_refuses_an_access_credential():
    from app.api.content_tokens import validate_content_token
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validate_content_token(grants.access_credential(_grant()))
