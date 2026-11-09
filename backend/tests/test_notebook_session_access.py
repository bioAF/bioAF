"""Who can see and stop notebook sessions, on both route families.

``/api/notebooks`` and ``/api/v1/notebooks`` both stay. A session in another
Organization, or one that does not exist, answers 404 before any adapter call,
record change, or audit entry. Within the Organization, seeing or stopping other
users' sessions follows the ``users:deactivate`` grant, narrowed by an API key's
scopes like every other grant.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.notebook_session import NotebookSession
from app.models.organization import Organization
from app.models.user import User
from app.services import api_key_service, role_service, service_account_service
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

FAMILIES = ["/api/notebooks", "/api/v1/notebooks"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _session_row(user: User, **extra) -> NotebookSession:
    return NotebookSession(
        user_id=user.id,
        organization_id=user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        slurm_job_id="job-1",
        compute_job_ref="bioaf-notebook-1",
        gcs_home_prefix="gs://bioaf-working/notebooks/1/",
        **extra,
    )


@pytest_asyncio.fixture
async def world(session, admin_user, comp_bio_user):
    """An admin's and a comp_bio user's sessions here, and one in another Organization."""
    other_org = Organization(name="Other Org", setup_complete=True)
    session.add(other_org)
    await session.flush()
    other_roles = await seed_builtin_roles(session, other_org.id)
    outsider = User(
        email="out@other.test",
        password_hash=AuthService.hash_password("outsiderpass1"),
        role_id=other_roles["admin"],
        organization_id=other_org.id,
        status="active",
    )
    session.add(outsider)
    await session.flush()
    admins = _session_row(admin_user)
    mine = _session_row(comp_bio_user)
    foreign = _session_row(outsider)
    session.add_all([admins, mine, foreign])
    await session.commit()
    return SimpleNamespace(admin=admins.id, comp_bio=mine.id, foreign=foreign.id, outsider=outsider.id)


async def _audit_count(session, entity_id: int) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == entity_id, AuditLog.entity_type.in_(["notebook", "notebook_session"]))
        )
    ).scalar_one()


async def _status(session, session_id: int) -> str:
    session.expire_all()
    return (await session.execute(select(NotebookSession.status).where(NotebookSession.id == session_id))).scalar_one()


@pytest.fixture
def adapters():
    """Both adapter lookups, so a test can prove neither was reached."""
    service_adapter = MagicMock(name="service adapter")
    route_adapter = MagicMock(name="route adapter")
    with (
        patch("app.services.notebook_service.get_notebook_adapter", service_adapter),
        patch("app.api.notebook_sessions.get_notebook_adapter", route_adapter),
    ):
        yield SimpleNamespace(service=service_adapter, route=route_adapter)


# --- Organization boundary -----------------------------------------------------------------


DENIABLE = [(family, "get", "") for family in FAMILIES]
DENIABLE += [(family, "post", "/stop") for family in FAMILIES]
DENIABLE += [("/api/v1/notebooks", "post", "/sync"), ("/api/v1/notebooks", "get", "/provenance")]


@pytest.mark.parametrize("family,method,suffix", DENIABLE)
@pytest.mark.parametrize("target", ["foreign", "missing"])
@pytest.mark.asyncio
async def test_a_foreign_or_missing_session_answers_404_with_no_side_effects(
    client, session, admin_token, world, adapters, family, method, suffix, target
):
    session_id = world.foreign if target == "foreign" else world.foreign + 1000
    response = await getattr(client, method)(f"{family}/sessions/{session_id}{suffix}", headers=_bearer(admin_token))
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
    adapters.service.assert_not_called()
    adapters.route.assert_not_called()
    assert await _status(session, world.foreign) == "running"
    assert await _audit_count(session, world.foreign) == 0


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_lists_never_include_another_organizations_sessions(client, admin_token, world, family):
    response = await client.get(f"{family}/sessions", headers=_bearer(admin_token))
    assert response.status_code == 200
    ids = {s["id"] for s in response.json()["sessions"]}
    assert ids == {world.admin, world.comp_bio}


# --- same-Organization controls ------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_a_user_reads_and_stops_their_own_session(client, session, comp_bio_token, world, adapters, family):
    detail = await client.get(f"{family}/sessions/{world.comp_bio}", headers=_bearer(comp_bio_token))
    assert detail.status_code == 200
    stopped = await client.post(f"{family}/sessions/{world.comp_bio}/stop", headers=_bearer(comp_bio_token))
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"
    assert await _status(session, world.comp_bio) == "stopped"
    adapters.service.assert_called()


@pytest.mark.asyncio
async def test_v1_detail_audits_the_access_and_v1_sync_and_provenance_work(
    client, session, comp_bio_user, comp_bio_token, world, adapters
):
    detail = await client.get(f"/api/v1/notebooks/sessions/{world.comp_bio}", headers=_bearer(comp_bio_token))
    assert detail.status_code == 200
    accesses = (
        await session.execute(
            select(AuditLog.user_id).where(AuditLog.entity_id == world.comp_bio, AuditLog.action == "session_access")
        )
    ).all()
    assert [row.user_id for row in accesses] == [comp_bio_user.id]
    sync = await client.post(f"/api/v1/notebooks/sessions/{world.comp_bio}/sync", headers=_bearer(comp_bio_token))
    assert sync.json() == {"status": "ok", "message": "Sync triggered"}
    provenance = await client.get(
        f"/api/v1/notebooks/sessions/{world.comp_bio}/provenance", headers=_bearer(comp_bio_token)
    )
    assert provenance.status_code == 200
    assert provenance.json()["session_id"] == world.comp_bio


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_stopping_another_users_session_needs_the_management_grant(
    client, session, admin_user, admin_token, comp_bio_token, world, adapters, family
):
    admin_id = admin_user.id
    refused = await client.post(f"{family}/sessions/{world.admin}/stop", headers=_bearer(comp_bio_token))
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Can only stop your own sessions"}
    adapters.service.assert_not_called()
    assert await _status(session, world.admin) == "running"
    assert await _audit_count(session, world.admin) == 0

    allowed = await client.post(f"{family}/sessions/{world.comp_bio}/stop", headers=_bearer(admin_token))
    assert allowed.status_code == 200
    stops = (
        await session.execute(
            select(AuditLog.user_id).where(AuditLog.entity_id == world.comp_bio, AuditLog.action == "stop")
        )
    ).all()
    assert [row.user_id for row in stops] == [admin_id]


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_the_list_shows_everyone_only_with_the_management_grant(
    client, comp_bio_token, admin_token, world, family
):
    own = await client.get(f"{family}/sessions", headers=_bearer(comp_bio_token))
    assert {s["id"] for s in own.json()["sessions"]} == {world.comp_bio}
    everyone = await client.get(f"{family}/sessions", headers=_bearer(admin_token))
    assert {s["id"] for s in everyone.json()["sessions"]} == {world.admin, world.comp_bio}


async def _custom_role_user(session, org_id: int, email: str, grants: list[tuple[str, str]]):
    role = await role_service.create_role(session, org_id, f"custom-{email}", permissions=grants)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("customrolepass1"),
        role_id=role.id,
        organization_id=org_id,
        status="active",
    )
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    return user, _bearer(AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name))


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_custom_roles_follow_the_management_grant(client, session, admin_user, world, adapters, family):
    org_id = admin_user.organization_id
    _, plain = await _custom_role_user(
        session, org_id, f"plain{len(family)}@t.test", [("notebooks", "view"), ("notebooks", "stop")]
    )
    _, manager = await _custom_role_user(
        session,
        org_id,
        f"manager{len(family)}@t.test",
        [("notebooks", "view"), ("notebooks", "stop"), ("users", "deactivate")],
    )
    assert (await client.get(f"{family}/sessions", headers=plain)).json()["sessions"] == []
    assert {s["id"] for s in (await client.get(f"{family}/sessions", headers=manager)).json()["sessions"]} == {
        world.admin,
        world.comp_bio,
    }
    assert (await client.post(f"{family}/sessions/{world.admin}/stop", headers=plain)).status_code == 403
    assert (await client.post(f"{family}/sessions/{world.admin}/stop", headers=manager)).status_code == 200


# --- API keys ------------------------------------------------------------------------------


async def _admin_key(session, admin_user, scopes: list[str]) -> dict:
    """A key on an admin-role service account whose stored scopes are exactly these.

    The mint API only issues the public scope alphabet, which has no notebook scopes, so
    the scopes are written directly: this proves the route narrows by whatever a key holds.
    """
    account = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name=f"Key {','.join(scopes)}",
        role_id=admin_user.role_id,
        created_by_user_id=admin_user.id,
    )
    key, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=account.id,
        name="key",
        scopes=["projects:view"],
        created_by_user_id=admin_user.id,
    )
    await session.execute(update(ApiKey).where(ApiKey.id == key.id).values(scopes=scopes))
    await session.commit()
    return _bearer(secret)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_a_key_without_the_management_scope_sees_only_its_own_sessions(
    client, session, admin_user, world, adapters, family
):
    narrow = await _admin_key(session, admin_user, ["notebooks:view", "notebooks:stop"])
    wide = await _admin_key(session, admin_user, ["notebooks:view", "notebooks:stop", "users:deactivate"])
    listed = await client.get(f"{family}/sessions", headers=narrow)
    assert listed.status_code == 200, listed.text
    assert listed.json()["sessions"] == []
    assert (await client.post(f"{family}/sessions/{world.admin}/stop", headers=narrow)).status_code == 403
    assert await _status(session, world.admin) == "running"

    everyone = await client.get(f"{family}/sessions", headers=wide)
    assert {s["id"] for s in everyone.json()["sessions"]} == {world.admin, world.comp_bio}
    assert (await client.post(f"{family}/sessions/{world.admin}/stop", headers=wide)).status_code == 200


# --- response differences ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_two_families_keep_their_response_differences(client, comp_bio_token, world):
    legacy = (await client.get(f"/api/notebooks/sessions/{world.comp_bio}", headers=_bearer(comp_bio_token))).json()
    v1 = (await client.get(f"/api/v1/notebooks/sessions/{world.comp_bio}", headers=_bearer(comp_bio_token))).json()
    v1_only = {
        "project",
        "requested_disk_gb",
        "failure_reason",
        "failure_message",
        "environment_version_id",
        "input_file_ids",
        "provider_metadata",
    }
    assert set(legacy) == set(v1)  # one schema; the legacy serializer leaves the richer fields at their defaults
    assert all(legacy[k] is None for k in v1_only)
    assert v1["input_file_ids"] == []
