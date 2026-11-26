"""Plan 03B (D03-6 / D03-10): the user's bioAF Git key is also their Work Node login key.

Owner-only download, never replaced while a Work Node may still need it, generated before a launch
that needs it, and installed only into that user's own authorized_keys.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.notebook_session import ComputeSession
from app.services.session_credential_service import SessionCredentialService

REPLACE_REFUSAL = (
    "Finish and stop your current work nodes before replacing this key. You can download your current key again."
)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def keyed_user(session, comp_bio_user):
    await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="pw",
    )
    cred = await SessionCredentialService.get_by_user_id(session, comp_bio_user.id)
    private, public = SessionCredentialService.generate_ssh_keypair(comp_bio_user.email)
    cred.ssh_private_key, cred.ssh_public_key = private, public
    await session.commit()
    return cred


def _node(user, status, **extra):
    return ComputeSession(
        user_id=user.id,
        organization_id=user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=2,
        memory_gb=4,
        status=status,
        **extra,
    )


@pytest.mark.asyncio
async def test_the_key_download_keeps_the_documented_file_name(client, comp_bio_token, keyed_user):
    """A text/plain body makes Chromium save "bioaf_id_rsa.txt", which the Profile's mv command misses."""
    response = await client.get("/api/auth/me/ssh-key/private", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="bioaf_id_rsa"'


@pytest.mark.asyncio
async def test_the_owner_downloads_their_current_private_key_uncached_and_audited(
    client, session, comp_bio_token, comp_bio_user, keyed_user
):
    response = await client.get("/api/auth/me/ssh-key/private", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    assert response.text.strip() == keyed_user.ssh_private_key.strip()
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    audit = (
        await session.execute(
            select(AuditLog).where(AuditLog.action == "download_ssh_key", AuditLog.user_id == comp_bio_user.id)
        )
    ).scalar_one()
    assert audit.details_json["fingerprint"].startswith("SHA256:")
    assert "PRIVATE KEY" not in str(audit.details_json)


@pytest.mark.asyncio
async def test_download_without_a_key_is_404(client, comp_bio_token, comp_bio_user):
    response = await client.get("/api/auth/me/ssh-key/private", headers=_bearer(comp_bio_token))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_another_user_or_an_admin_never_receives_someone_elses_key(client, admin_token, keyed_user):
    # The route is the caller's own key only; there is no user parameter to override.
    response = await client.get("/api/auth/me/ssh-key/private", headers=_bearer(admin_token))
    assert response.status_code == 404
    assert keyed_user.ssh_private_key not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "starting", "running", "stopping", "stop_failed"])
async def test_replacement_waits_for_work_nodes_that_may_need_the_key(
    client, session, comp_bio_token, comp_bio_user, keyed_user, status
):
    user_id = comp_bio_user.id
    session.add(_node(comp_bio_user, status, gce_instance_name="bioaf-worknode-1"))
    await session.commit()
    before = keyed_user.ssh_private_key
    response = await client.post("/api/auth/me/ssh-key/generate", headers=_bearer(comp_bio_token))
    assert response.status_code == 409
    assert response.json()["detail"] == REPLACE_REFUSAL
    session.expire_all()
    cred = await SessionCredentialService.get_by_user_id(session, user_id)
    assert cred.ssh_private_key == before
    # The current key stays downloadable.
    download = await client.get("/api/auth/me/ssh-key/private", headers=_bearer(comp_bio_token))
    assert download.status_code == 200


@pytest.mark.asyncio
async def test_a_misleading_stopped_record_with_a_live_instance_still_blocks_replacement(
    client, session, comp_bio_token, comp_bio_user, keyed_user
):
    from app.services.work_node_readiness import ManagedInstance

    node = _node(comp_bio_user, "stopped", gce_instance_name="bioaf-worknode-2")
    session.add(node)
    await session.commit()
    adapter = MagicMock()
    adapter.inventory_instances = AsyncMock(
        return_value=[ManagedInstance(name="bioaf-worknode-2", session_id=str(node.id))]
    )
    with patch("app.services.session_credential_service.get_work_node_adapter", return_value=adapter):
        response = await client.post("/api/auth/me/ssh-key/generate", headers=_bearer(comp_bio_token))
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_an_unavailable_inventory_blocks_replacement(client, session, comp_bio_token, comp_bio_user, keyed_user):
    from app.services.work_node_readiness import InventoryUnavailable

    session.add(_node(comp_bio_user, "stopped", gce_instance_name="bioaf-worknode-3"))
    await session.commit()
    adapter = MagicMock()
    adapter.inventory_instances = AsyncMock(side_effect=InventoryUnavailable("denied"))
    with patch("app.services.session_credential_service.get_work_node_adapter", return_value=adapter):
        response = await client.post("/api/auth/me/ssh-key/generate", headers=_bearer(comp_bio_token))
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_replacement_succeeds_once_affected_nodes_are_confirmed_gone(
    client, session, comp_bio_token, comp_bio_user, keyed_user
):
    session.add(_node(comp_bio_user, "stopped", gce_instance_name="bioaf-worknode-4"))
    await session.commit()
    before = keyed_user.ssh_public_key
    adapter = MagicMock()
    adapter.inventory_instances = AsyncMock(return_value=[])
    with patch("app.services.session_credential_service.get_work_node_adapter", return_value=adapter):
        response = await client.post("/api/auth/me/ssh-key/generate", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    assert response.json()["public_key"] != before
    assert "GitHub" in response.json()["message"]


@pytest.mark.asyncio
async def test_first_generation_never_needs_a_node_check(client, session, comp_bio_token, comp_bio_user):
    await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="pw",
    )
    session.add(_node(comp_bio_user, "running", gce_instance_name="bioaf-worknode-5"))
    await session.commit()
    response = await client.post("/api/auth/me/ssh-key/generate", headers=_bearer(comp_bio_token))
    assert response.status_code == 200


def test_generated_keypair_is_a_matching_openssh_pair():
    private, public = SessionCredentialService.generate_ssh_keypair("a@b.c")
    key = asyncssh.import_private_key(private)
    assert public.startswith(key.export_public_key().decode().strip())
    assert public.endswith(" a@b.c")


@pytest.mark.asyncio
async def test_ensure_login_key_generates_only_when_missing(session, comp_bio_user):
    cred = await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="pw",
    )
    generated = await SessionCredentialService.ensure_login_key(session, cred, comp_bio_user.email)
    assert generated is True
    first = cred.ssh_public_key
    assert await SessionCredentialService.ensure_login_key(session, cred, comp_bio_user.email) is False
    assert cred.ssh_public_key == first


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["private_only", "public_only", "mismatched"])
async def test_ensure_login_key_refuses_partial_material_instead_of_replacing_it(session, comp_bio_user, broken):
    from app.exceptions import ValidationError

    cred = await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="pw",
    )
    private, public = SessionCredentialService.generate_ssh_keypair(comp_bio_user.email)
    if broken == "private_only":
        cred.ssh_private_key = private
    elif broken == "public_only":
        cred.ssh_public_key = public
    else:
        cred.ssh_private_key = private
        cred.ssh_public_key = SessionCredentialService.generate_ssh_keypair(comp_bio_user.email)[1]
    with pytest.raises(ValidationError):
        await SessionCredentialService.ensure_login_key(session, cred, comp_bio_user.email)


@pytest.mark.parametrize("builder", ["gce", "ec2"])
def test_the_users_public_key_goes_only_into_their_own_authorized_keys(builder):
    from app.adapters.work_nodes.ec2 import _build_ec2_startup_script
    from app.adapters.work_nodes.gce import _build_startup_script

    build = _build_startup_script if builder == "gce" else _build_ec2_startup_script
    script = build(
        {
            "session_id": 7,
            "session_credentials": {"username": "sci", "password_hash": "$2b$x"},
            "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nk\n",
            "ssh_public_key": "ssh-rsa AAAAuser sci@lab.org",
            "login_public_key": "ssh-rsa AAAAuser sci@lab.org",
        }
    )
    assert "ssh-rsa AAAAuser sci@lab.org" in script
    lines = script.splitlines()
    write = next(i for i, ln in enumerate(lines) if "/home/sci/.ssh/authorized_keys" in ln and ">" in ln)
    assert "AAAAuser" in lines[write] or "AAAAuser" in lines[write + 1]
    assert "chmod 600 /home/sci/.ssh/authorized_keys" in script
    assert "chmod 700 /home/sci/.ssh" in script
    assert "chown -R sci:sci /home/sci/.ssh" in script


@pytest.mark.asyncio
async def test_launch_records_the_login_key_fingerprint(client, session, comp_bio_token, comp_bio_user, keyed_user):
    from tests.test_work_nodes import seed_environment  # noqa: F401
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    env = Environment(
        name="E",
        organization_id=comp_bio_user.organization_id,
        created_by_user_id=comp_bio_user.id,
        environment_type="work_node",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: t\n",
        image_uri="img",
        created_by_user_id=comp_bio_user.id,
    )
    session.add(version)
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('compute_deployed', 'true') ON CONFLICT (key) DO NOTHING"
        )
    )
    await session.commit()
    from app.adapters.models import ServiceState, VmInfo

    adapter = MagicMock()
    adapter.launch_vm = AsyncMock(
        return_value=VmInfo(instance_name="bioaf-worknode-x", zone="z", status=ServiceState.STARTING)
    )
    with patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter):
        response = await client.post(
            "/api/v1/work-nodes/sessions",
            json={"environment_version_id": version.id, "machine_type": "n2-standard-4"},
            headers=_bearer(comp_bio_token),
        )
    assert response.status_code == 200, response.text
    spec = adapter.launch_vm.call_args.args[0]
    assert spec["login_public_key"] == keyed_user.ssh_public_key
    row = (await session.execute(select(ComputeSession).where(ComputeSession.id == response.json()["id"]))).scalar_one()
    assert row.login_key_fingerprint and row.login_key_fingerprint.startswith("SHA256:")
    assert (await session.execute(select(func.count()).select_from(ComputeSession))).scalar_one() == 1
    assert datetime.now(timezone.utc)  # keeps imports used


@pytest.mark.asyncio
async def test_a_failed_launch_without_a_recorded_instance_is_checked_before_replacement(
    client, session, comp_bio_token, comp_bio_user, keyed_user
):
    """A launch attempt can have created a VM even when its result was never recorded. Owner-approved
    (2026-11-24, review 3 item 4): only its settled outcome (no VM made) lets the key change."""
    from app.platform.work_node_inventory import LaunchOutcome
    from app.services.work_node_service import launch_runner

    requests = [{"key": "us-central1-b", "zone": "us-central1-b", "project": "proj", "state": "requested"}]
    node = _node(
        comp_bio_user,
        "failed",
        launch_attempt_id="attempt-9",
        provider_metadata={"launch_journal": {"runner": launch_runner(), "requests": requests}},
    )
    session.add(node)
    await session.commit()
    adapter = MagicMock()
    adapter.inventory_instances = AsyncMock(return_value=[])

    async def generate(state):
        adapter.settle_launch = AsyncMock(return_value=LaunchOutcome(state))
        with (
            patch("app.services.session_credential_service.get_work_node_adapter", return_value=adapter),
            patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter),
        ):
            return await client.post("/api/auth/me/ssh-key/generate", headers=_bearer(comp_bio_token))

    assert (await generate("unknown")).status_code == 409
    assert (await generate("created")).status_code == 409
    assert adapter.settle_launch.await_args.args[0] == requests
    assert (await generate("none")).status_code == 200
