"""Server support for reviewing an unfinished setup (project 12A, approved D3).

A returning administrator reviews saved settings: the organization name comes from a guarded,
organization-scoped read (never from the public status), the bootstrap writes act on the caller's
own organization, re-submitting already-enabled components never requeues them, and deployment
history can be read for compute deployments only, filtered before the limit.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.organization import Organization
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def unfinished(session, admin_user):
    org = await session.get(Organization, admin_user.organization_id)
    org.name, org.setup_complete = "Lab North", False
    await session.commit()
    return org


@pytest_asyncio.fixture
async def other_org(session, unfinished):
    """A second organization created before the caller's, so 'the first organization' is not theirs."""
    from app.services.bootstrap_roles import seed_builtin_roles
    from app.models.user import User

    other = Organization(name="Elsewhere", setup_complete=False, smtp_host="smtp.elsewhere")
    session.add(other)
    await session.flush()
    # Make "the first row" ambiguous: give the foreign organization the lowest id.
    await session.execute(text("UPDATE organizations SET id = 0 WHERE id = :i").bindparams(i=other.id))
    await session.commit()
    roles = await seed_builtin_roles(session, 0)
    user = User(
        email="elsewhere@test.com",
        password_hash=AuthService.hash_password("pw-elsewhere-1"),
        role_id=roles["admin"],
        organization_id=0,
        status="active",
    )
    session.add(user)
    await session.commit()
    return 0


# ---------------------------------------------------------------------------
# GET /api/bootstrap/org-settings
# ---------------------------------------------------------------------------


async def test_an_administrator_reads_their_own_organization_name(client, admin_token, unfinished, other_org):
    resp = await client.get("/api/bootstrap/org-settings", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {"org_name": "Lab North"}


async def test_the_organization_name_needs_a_session(client, unfinished):
    resp = await client.get("/api/bootstrap/org-settings")
    assert resp.status_code == 401
    assert "Lab North" not in resp.text


async def test_the_organization_name_needs_configure_permission(client, viewer_token, unfinished):
    resp = await client.get("/api/bootstrap/org-settings", headers=_auth(viewer_token))
    assert resp.status_code == 403
    assert "Lab North" not in resp.text


async def test_an_api_key_without_the_configure_scope_is_refused(client, session, admin_user, unfinished):
    from tests.test_permissions_and_ownership import _api_key_headers

    headers = await _api_key_headers(session, admin_user, admin_user._test_role_map["admin"], ["files:view"])
    resp = await client.get("/api/bootstrap/org-settings", headers=headers)
    assert resp.status_code == 403
    assert "Lab North" not in resp.text


async def test_a_missing_organization_is_not_found(client, session, admin_user, unfinished):
    token = AuthService.create_token(admin_user.id, admin_user.email, admin_user.role_id, 987654, role_name="admin")
    resp = await client.get("/api/bootstrap/org-settings", headers=_auth(token))
    assert resp.status_code == 404
    assert "Lab North" not in resp.text


async def test_the_public_status_never_carries_the_organization_name(client, admin_token, unfinished):
    for headers in ({}, _auth(admin_token)):
        body = (await client.get("/api/bootstrap/status", headers=headers)).json()
        assert "org_name" not in body and "Lab North" not in str(body)


# ---------------------------------------------------------------------------
# bootstrap writes act on the caller's organization
# ---------------------------------------------------------------------------


async def test_configure_org_renames_the_callers_organization(client, session, admin_token, unfinished, other_org):
    resp = await client.post("/api/bootstrap/configure-org", json={"org_name": "Lab South"}, headers=_auth(admin_token))
    assert resp.status_code == 200
    names = dict((await session.execute(select(Organization.id, Organization.name))).all())
    assert names[unfinished.id] == "Lab South" and names[other_org] == "Elsewhere"


async def test_smtp_settings_are_the_callers_organizations(client, session, admin_token, unfinished, other_org):
    body = (await client.get("/api/bootstrap/smtp-settings", headers=_auth(admin_token))).json()
    assert body["host"] != "smtp.elsewhere"
    resp = await client.post(
        "/api/bootstrap/configure-smtp",
        json={"host": "smtp.north", "port": 587, "username": "u", "password": "p", "from_address": "a@b.c"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    hosts = dict((await session.execute(select(Organization.id, Organization.smtp_host))).all())
    assert hosts[unfinished.id] == "smtp.north" and hosts[other_org] == "smtp.elsewhere"


async def test_completion_completes_the_callers_organization(client, session, admin_token, unfinished, other_org):
    resp = await client.post("/api/bootstrap/complete", headers=_auth(admin_token))
    assert resp.status_code == 200
    done = dict((await session.execute(select(Organization.id, Organization.setup_complete))).all())
    assert done[unfinished.id] is True and done[other_org] is False


# ---------------------------------------------------------------------------
# component admission is idempotent for enabled components
# ---------------------------------------------------------------------------


async def test_a_repeated_selection_never_requeues_an_enabled_component(client, session, admin_token, unfinished):
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) VALUES "
            "('nextflow', true, 'enabled', '{}'), ('jupyterhub', true, 'provisioning', '{}'), "
            "('rstudio', false, 'disabled', '{}')"
        )
    )
    await session.commit()
    resp = await client.post(
        "/api/components/select-batch",
        json={"keys": ["nextflow", "jupyterhub", "rstudio"]},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    session.expire_all()
    rows = {
        k: (e, s)
        for k, e, s in (
            await session.execute(text("SELECT component_key, enabled, status FROM component_states"))
        ).all()
    }
    assert rows["nextflow"] == (True, "enabled")
    assert rows["jupyterhub"] == (True, "provisioning")
    assert rows["rstudio"] == (True, "queued_for_infra")


# ---------------------------------------------------------------------------
# deployment history
# ---------------------------------------------------------------------------


async def test_operation_history_can_be_filtered_to_compute_deployments_before_the_limit(
    client, session, admin_token, unfinished
):
    from app.models.infrastructure_operation import InfrastructureOperation

    session.add(InfrastructureOperation(action="compute_deploy", status="failed", principal="user:1", steps=[]))
    for _ in range(5):
        session.add(InfrastructureOperation(action="image_build", status="succeeded", principal="user:1", steps=[]))
    await session.commit()

    resp = await client.get(
        "/api/v1/infrastructure/operations", params={"action": "compute_deploy", "limit": 1}, headers=_auth(admin_token)
    )
    assert resp.status_code == 200, resp.text
    assert [(o["action"], o["status"]) for o in resp.json()["operations"]] == [("compute_deploy", "failed")]

    unknown = await client.get(
        "/api/v1/infrastructure/operations", params={"action": "drop_tables"}, headers=_auth(admin_token)
    )
    assert unknown.status_code == 422


async def test_operation_history_keeps_its_permission(client, viewer_token, unfinished):
    resp = await client.get(
        "/api/v1/infrastructure/operations", params={"action": "compute_deploy"}, headers=_auth(viewer_token)
    )
    assert resp.status_code == 403
