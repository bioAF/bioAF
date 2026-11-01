"""Behavioral grant, key-scope, and Organization-boundary regressions.

test_route_permission_contract.py proves each route declares its gate; this file
proves the gate does what it says for JWT users, custom roles, and API keys, and
that a record owned by another Organization answers 404.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.services import api_key_service, role_service, service_account_service
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles


async def _custom_role_headers(session, org_id: int, email: str, grants: list[tuple[str, str]]) -> dict:
    """A signed-in JWT user holding a custom role with exactly these grants."""
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
    token = AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name)
    return {"Authorization": f"Bearer {token}"}


async def _api_key_headers(session, admin_user, role_id: int, scopes: list[str]) -> dict:
    """An API key on a service account with this role, narrowed to these scopes."""
    account = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name=f"Key {','.join(scopes)}",
        role_id=role_id,
        created_by_user_id=admin_user.id,
    )
    _row, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=account.id,
        name="key",
        scopes=scopes,
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def foreign(session, admin_user) -> SimpleNamespace:
    """A second Organization with its own admin, and role, in the same install."""
    org = Organization(name="Other Org", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="admin@other.test",
        password_hash=AuthService.hash_password("otherpassword1"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return SimpleNamespace(org=org, roles=roles, user=user)


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/users"),
        ("get", "/api/users/{me}"),
        ("post", "/api/users/{me}/deactivate"),
        ("post", "/api/components/select-batch"),
        ("get", "/api/bootstrap/smtp-settings"),
        ("post", "/api/bootstrap/complete"),
    ],
)
@pytest.mark.asyncio
async def test_a_narrow_key_cannot_use_its_admin_roles_other_grants(client, session, admin_user, method, path):
    """Role holds the grant, key scope does not: the narrower of the two wins."""
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:view"])
    kwargs = {"json": {"keys": []}} if path.endswith("select-batch") else {}
    response = await getattr(client, method)(path.format(me=admin_user.id), headers=headers, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"] == "key_scope_missing"


@pytest.mark.asyncio
async def test_a_custom_role_gets_only_the_user_routes_it_was_granted(client, session, admin_user):
    headers = await _custom_role_headers(session, admin_user.organization_id, "cr@test.com", [("users", "view")])
    assert (await client.get("/api/users", headers=headers)).status_code == 200
    denied = await client.post(f"/api/users/{admin_user.id}/deactivate", headers=headers)
    assert denied.status_code == 403


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("get", "", None),
        ("patch", "", {"name": "Taken Over"}),
        ("post", "/deactivate", None),
        ("post", "/lock", None),
        ("post", "/reactivate", None),
        ("delete", "", None),
        ("post", "/resend-invite", None),
        ("post", "/admin-reset-password", {"mode": "temporary", "temporary_password": "Pwned-password-1"}),
    ],
)
@pytest.mark.asyncio
async def test_a_user_in_another_organization_answers_404(
    client, session, admin_headers, foreign, method, suffix, body
):
    original_hash, original_name, original_status = foreign.user.password_hash, foreign.user.name, foreign.user.status
    kwargs = {"json": body} if body is not None else {}
    response = await getattr(client, method)(f"/api/users/{foreign.user.id}{suffix}", headers=admin_headers, **kwargs)
    assert response.status_code == 404

    await session.refresh(foreign.user)
    assert foreign.user.status == original_status
    assert foreign.user.name == original_name
    assert foreign.user.password_hash == original_hash


@pytest.mark.asyncio
async def test_a_user_cannot_be_given_another_organizations_role(client, session, admin_headers, admin_user, foreign):
    viewer = admin_user._test_role_map["viewer"]
    target = User(email="t@test.com", password_hash="x", role_id=viewer, organization_id=admin_user.organization_id)
    session.add(target)
    await session.commit()

    response = await client.patch(
        f"/api/users/{target.id}", headers=admin_headers, json={"role_id": foreign.roles["admin"]}
    )
    assert response.status_code == 404
    await session.refresh(target)
    assert target.role_id == viewer


@pytest.mark.asyncio
async def test_an_invitation_cannot_carry_another_organizations_role(client, session, admin_headers, foreign):
    single = await client.post(
        "/api/users", headers=admin_headers, json={"email": "new@test.com", "role_id": foreign.roles["admin"]}
    )
    assert single.status_code == 404

    bulk = await client.post(
        "/api/users/bulk-invite",
        headers=admin_headers,
        json={"invites": [{"email": "bulk@test.com", "role_id": foreign.roles["admin"]}]},
    )
    assert bulk.status_code == 404

    invited = await session.execute(select(User).where(User.email.in_(["new@test.com", "bulk@test.com"])))
    assert invited.scalars().all() == []


@pytest.mark.asyncio
async def test_a_bulk_invitation_with_one_foreign_role_invites_nobody(
    client, session, admin_headers, admin_user, foreign
):
    response = await client.post(
        "/api/users/bulk-invite",
        headers=admin_headers,
        json={
            "invites": [
                {"email": "ok@test.com", "role_id": admin_user._test_role_map["viewer"]},
                {"email": "bad@test.com", "role_id": foreign.roles["admin"]},
            ]
        },
    )
    assert response.status_code == 404
    invited = await session.execute(select(User).where(User.email == "ok@test.com"))
    assert invited.scalars().all() == []


@pytest.mark.asyncio
async def test_same_organization_user_management_still_works(client, session, admin_headers, admin_user):
    viewer = admin_user._test_role_map["viewer"]
    comp_bio = admin_user._test_role_map["comp_bio"]
    target = User(email="same@test.com", password_hash="x", role_id=viewer, organization_id=admin_user.organization_id)
    session.add(target)
    await session.commit()

    assert (await client.get(f"/api/users/{target.id}", headers=admin_headers)).status_code == 200
    changed = await client.patch(f"/api/users/{target.id}", headers=admin_headers, json={"role_id": comp_bio})
    assert changed.status_code == 200
    assert changed.json()["role_id"] == comp_bio
    roles = await session.execute(select(Role.id).where(Role.organization_id == admin_user.organization_id))
    assert comp_bio in roles.scalars().all()
