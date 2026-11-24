"""Plan 03B Settings > System Settings > Security and SSH access: Work Node ranges.

Save changes is draft-only; Apply changes makes the draft the policy for future launches with a new
revision; stale edits and invalid enabled lists are refused and leave the applied policy alone;
rollback restores the previous applied policy. Work Node range administration needs
work_nodes:configure and grants nothing on the application host.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import role_service
from app.services.auth_service import AuthService
from app.services.ssh_access_policy import applied_work_node_policy

BASE = "/api/v1/settings/ssh-access"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def _custom(session, org_id, email, grants):
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
    return _bearer(AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name))


@pytest_asyncio.fixture
async def admin(admin_token):
    return _bearer(admin_token)


async def _draft(client, headers, ranges, *, enabled=True, base=None):
    state = (await client.get(BASE, headers=headers)).json()
    version = state["work_nodes"]["draft"]["version"] if base is None else base
    return await client.put(
        f"{BASE}/work-nodes/draft",
        json={"enabled": enabled, "ranges": ranges, "base_version": version},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_new_installations_have_no_range_and_launches_keep_todays_behavior(client, session, admin):
    state = (await client.get(BASE, headers=admin)).json()
    assert state["work_nodes"]["applied"] == {"revision": 0, "enabled": False, "ranges": []}
    policy = await applied_work_node_policy(session)
    assert policy.ranges == () and policy.revision == 0


@pytest.mark.asyncio
async def test_save_is_draft_only_and_apply_creates_a_new_revision(client, session, admin):
    response = await _draft(
        client, admin, [{"name": "Lab VPN", "cidr": "10.20.0.0/16"}, {"name": "Me", "cidr": "203.0.113.7"}]
    )
    assert response.status_code == 200
    assert (await applied_work_node_policy(session)).ranges == ()

    state = (await client.get(BASE, headers=admin)).json()
    applied = await client.post(
        f"{BASE}/work-nodes/apply", json={"draft_version": state["work_nodes"]["draft"]["version"]}, headers=admin
    )
    assert applied.status_code == 200
    session.expire_all()
    policy = await applied_work_node_policy(session)
    assert policy.ranges == ("10.20.0.0/16", "203.0.113.7/32")
    assert policy.revision == 1
    audit = (
        await session.execute(select(AuditLog).where(AuditLog.action == "apply_work_node_ssh_policy"))
    ).scalar_one()
    assert audit.details_json["revision"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ranges",
    [
        [{"name": "All", "cidr": "0.0.0.0/0"}],
        [{"name": "All6", "cidr": "::/0"}],
        [{"name": "Bad", "cidr": "10.0.0.1/8"}],
        [{"name": "Bad", "cidr": "lab"}],
        [],
    ],
)
async def test_invalid_or_empty_enabled_lists_are_refused(client, session, admin, ranges):
    response = await _draft(client, admin, ranges)
    assert response.status_code == 400
    state = (await client.get(BASE, headers=admin)).json()
    assert state["work_nodes"]["draft"]["ranges"] == []


@pytest.mark.asyncio
async def test_a_stale_edit_is_refused(client, admin):
    first = await _draft(client, admin, [{"name": "A", "cidr": "10.1.0.0/16"}])
    assert first.status_code == 200
    stale = await _draft(client, admin, [{"name": "B", "cidr": "10.2.0.0/16"}], base=0)
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_check_explains_the_login_rule_and_remaining_exposure(client, admin):
    await _draft(client, admin, [{"name": "A", "cidr": "10.1.0.0/16"}])
    result = (await client.post(f"{BASE}/work-nodes/check", headers=admin)).json()
    assert result["valid"] is True
    text = " ".join(result["messages"])
    assert "password or key" in text and "key only" in text
    assert "launched after" in text
    assert "does not limit which networks can reach SSH" in text


@pytest.mark.asyncio
async def test_rollback_restores_the_previous_applied_policy(client, session, admin):
    for cidr in ("10.1.0.0/16", "10.2.0.0/16"):
        await _draft(client, admin, [{"name": "A", "cidr": cidr}])
        version = (await client.get(BASE, headers=admin)).json()["work_nodes"]["draft"]["version"]
        await client.post(f"{BASE}/work-nodes/apply", json={"draft_version": version}, headers=admin)
    rolled = await client.post(f"{BASE}/work-nodes/rollback", headers=admin)
    assert rolled.status_code == 200
    session.expire_all()
    policy = await applied_work_node_policy(session)
    assert policy.ranges == ("10.1.0.0/16",)
    assert policy.revision == 3


@pytest.mark.asyncio
async def test_turning_ranges_off_is_its_own_applied_change(client, session, admin):
    await _draft(client, admin, [{"name": "A", "cidr": "10.1.0.0/16"}])
    v = (await client.get(BASE, headers=admin)).json()["work_nodes"]["draft"]["version"]
    await client.post(f"{BASE}/work-nodes/apply", json={"draft_version": v}, headers=admin)
    await _draft(client, admin, [{"name": "A", "cidr": "10.1.0.0/16"}], enabled=False)
    v = (await client.get(BASE, headers=admin)).json()["work_nodes"]["draft"]["version"]
    await client.post(f"{BASE}/work-nodes/apply", json={"draft_version": v}, headers=admin)
    session.expire_all()
    assert (await applied_work_node_policy(session)).ranges == ()


@pytest.mark.asyncio
async def test_work_node_only_operators_see_no_application_host_section(client, session, admin_user):
    headers = await _custom(session, admin_user.organization_id, "wn@lab.test", [("work_nodes", "configure")])
    state = (await client.get(BASE, headers=headers)).json()
    assert "work_nodes" in state
    assert "application_host" not in state
    response = await _draft(client, headers, [{"name": "A", "cidr": "10.1.0.0/16"}])
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_without_either_permission_the_section_is_refused(client, comp_bio_token):
    assert (await client.get(BASE, headers=_bearer(comp_bio_token))).status_code == 403


@pytest.mark.asyncio
async def test_infrastructure_configure_alone_cannot_change_work_node_ranges(client, session, admin_user):
    headers = await _custom(session, admin_user.organization_id, "infra@lab.test", [("infrastructure", "configure")])
    state = (await client.get(BASE, headers=headers)).json()
    assert "work_nodes" not in state
    response = await client.put(
        f"{BASE}/work-nodes/draft",
        json={"enabled": True, "ranges": [{"name": "A", "cidr": "10.1.0.0/16"}], "base_version": 0},
        headers=headers,
    )
    assert response.status_code == 403


# -- A launch applies the policy in force ----------------------------------------------------

from tests import test_work_nodes as _work_node_suite  # noqa: E402

seed_environment = _work_node_suite.seed_environment
seed_platform_config = _work_node_suite.seed_platform_config
seed_project = _work_node_suite.seed_project
seed_session_credentials = _work_node_suite.seed_session_credentials


@pytest.mark.asyncio
async def test_a_range_enabled_launch_generates_the_missing_key_before_the_provider_call(
    client,
    session,
    admin,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.adapters.models import ServiceState, VmInfo
    from app.database import async_session_factory
    from app.models.session_credential import SessionCredential

    await _draft(client, admin, [{"name": "Lab", "cidr": "10.20.0.0/16"}])
    v = (await client.get(BASE, headers=admin)).json()["work_nodes"]["draft"]["version"]
    await client.post(f"{BASE}/work-nodes/apply", json={"draft_version": v}, headers=admin)
    seen = {}

    async def fake_launch(spec):
        async with async_session_factory() as other:
            cred = (
                await other.execute(select(SessionCredential).where(SessionCredential.user_id == comp_bio_user.id))
            ).scalar_one()
            seen["committed_public"] = cred.ssh_public_key
        seen["spec"] = spec
        return VmInfo(instance_name="bioaf-worknode-1", zone="z", status=ServiceState.STARTING)

    adapter = MagicMock()
    adapter.launch_vm = AsyncMock(side_effect=fake_launch)
    with patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter):
        response = await client.post(
            "/api/v1/work-nodes/sessions",
            json={
                "project_id": seed_project.id,
                "environment_version_id": seed_environment["ready_version"].id,
                "machine_type": "n2-standard-4",
            },
            headers=_bearer(comp_bio_token),
        )
    assert response.status_code == 200, response.text
    assert seen["committed_public"] and seen["spec"]["login_public_key"] == seen["committed_public"]
    assert seen["spec"]["ssh_policy"] == {"revision": 1, "ranges": ["10.20.0.0/16"]}
    assert response.json()["ssh_policy_revision"] == 1


@pytest.mark.asyncio
async def test_without_a_range_a_user_without_a_key_keeps_password_login(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.adapters.models import ServiceState, VmInfo

    adapter = MagicMock()
    adapter.launch_vm = AsyncMock(return_value=VmInfo(instance_name="n", zone="z", status=ServiceState.STARTING))
    with patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter):
        response = await client.post(
            "/api/v1/work-nodes/sessions",
            json={
                "project_id": seed_project.id,
                "environment_version_id": seed_environment["ready_version"].id,
                "machine_type": "n2-standard-4",
            },
            headers=_bearer(comp_bio_token),
        )
    assert response.status_code == 200
    spec = adapter.launch_vm.call_args.args[0]
    assert spec["login_public_key"] is None
    assert spec["ssh_policy"] == {"revision": 0, "ranges": []}
