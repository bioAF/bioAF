"""Plan 03B: automatic preflight and the application-host filter lifecycle.

Check access is read-only (it also asks the host for fresh guest diagnostics). Apply changes needs
infrastructure:deploy, reconciles the protected recovery sources, refuses while recovery Needs
attention, and keeps the previous access on any failure. Automatic checks are labelled
"configured_automatic", never "login_tested", unless a matching installer probe was recorded.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.models.user import User
from app.services import role_service
from app.services.auth_service import AuthService
from app.services.host_ssh_filter import FilterApplyError
from app.services.ssh_recovery import HostIdentity

BASE = "/api/v1/settings/ssh-access"
IDENTITY = HostIdentity(
    provider="gcp",
    account="lab-project",
    region="us-central1",
    zone="us-central1-b",
    instance_id="481516",
    instance_name="bioaf",
    public=True,
    network="default",
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    status_dir = tmp_path / "update-status"
    requests_dir = tmp_path / "update-requests"
    status_dir.mkdir()
    requests_dir.mkdir()
    monkeypatch.setattr(settings, "update_status_dir", str(status_dir))
    monkeypatch.setattr(settings, "update_requests_dir", str(requests_dir))
    driver = MagicMock()
    driver.ensure_recovery.return_value = [
        {"kind": "gcp_iap", "source": "35.235.240.0/20", "priority": 850, "ports": "tcp:22"}
    ]
    driver.recovery_sources.return_value = driver.ensure_recovery.return_value
    driver.check_recovery.return_value = []
    driver.recovery_missing.return_value = False
    driver.apply_filter.return_value = {"provider": "gcp", "rules": ["bioaf-ssh-filter-deny-v4"]}
    driver.snapshot.return_value = {"provider": "gcp", "rules": {}}
    with (
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=IDENTITY)),
        patch("app.services.host_ssh_access.network_for", AsyncMock(return_value=driver)),
    ):
        yield type("Host", (), {"status_dir": status_dir, "requests_dir": requests_dir, "driver": driver})


def _diagnostics(host, **over):
    report = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instance_id": "481516",
        "provider": "gcp",
        "sshd_ok": True,
        "pubkey_auth": True,
        "guest_agent": True,
        "oslogin": False,
        "eic_package": False,
        "authorized_keys_command": "",
    }
    report.update(over)
    (host.status_dir / "ssh-recovery-diagnostics.json").write_text(json.dumps(report))


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
    return {
        "Authorization": f"Bearer {AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name)}"
    }


@pytest_asyncio.fixture
async def admin(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _save_draft(client, headers, ranges, enabled=True):
    state = (await client.get(BASE, headers=headers)).json()
    version = state["application_host"]["filter"]["draft"]["version"]
    return await client.put(
        f"{BASE}/host/draft", json={"enabled": enabled, "ranges": ranges, "base_version": version}, headers=headers
    )


@pytest.mark.asyncio
async def test_check_access_is_configured_by_automatic_checks_and_requests_fresh_diagnostics(client, admin, host):
    _diagnostics(host)
    result = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert result["recovery"]["status"] == "configured_automatic"
    assert result["recovery"]["problems"] == []
    host.driver.ensure_recovery.assert_not_called()  # read-only
    assert list(host.requests_dir.glob("*.json")), "a fixed diagnostics refresh was requested"
    trigger = json.loads(next(host.requests_dir.glob("*.json")).read_text())
    assert trigger == {"operation": "ssh-recovery-diagnostics"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "report, expected",
    [
        (None, "diagnostics"),
        ({"generated_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()}, "diagnostics"),
        ({"instance_id": "999"}, "diagnostics"),
        ({"guest_agent": False}, "guest environment"),
        ({"pubkey_auth": False}, "public key"),
    ],
)
async def test_missing_stale_or_failing_guest_checks_need_attention(client, admin, host, report, expected):
    if report is not None:
        _diagnostics(host, **report)
    result = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert result["recovery"]["status"] == "needs_attention"
    assert any(expected in p for p in result["recovery"]["problems"])


@pytest.mark.asyncio
async def test_apply_reconciles_recovery_then_applies_the_filter_with_a_new_revision(client, session, admin, host):
    _diagnostics(host)
    assert (await _save_draft(client, admin, [{"name": "Office", "cidr": "203.0.113.7"}])).status_code == 200
    version = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    response = await client.post(f"{BASE}/host/apply", json={"draft_version": version}, headers=admin)
    assert response.status_code == 200, response.text
    host.driver.ensure_recovery.assert_called_once()
    host.driver.apply_filter.assert_called_once_with(["203.0.113.7/32"])
    state = (await client.get(BASE, headers=admin)).json()["application_host"]
    assert state["filter"]["applied"]["revision"] == 1
    assert state["filter"]["applied"]["ranges"][0]["cidr"] == "203.0.113.7/32"
    assert state["recovery"]["sources"][0]["source"] == "35.235.240.0/20"
    assert (
        state["recovery_caption"]
        == "Cloud recovery access is always allowed. Your cloud-account permissions are still required."
    )


@pytest.mark.asyncio
async def test_apply_is_refused_while_recovery_needs_attention_and_nothing_changes(client, admin, host):
    _diagnostics(host, guest_agent=False)
    await _save_draft(client, admin, [{"name": "Office", "cidr": "203.0.113.7"}])
    version = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    response = await client.post(f"{BASE}/host/apply", json={"draft_version": version}, headers=admin)
    assert response.status_code == 409
    host.driver.apply_filter.assert_not_called()
    assert (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["applied"]["revision"] == 0


@pytest.mark.asyncio
async def test_a_failed_provider_apply_keeps_the_previous_policy(client, admin, host):
    _diagnostics(host)
    host.driver.apply_filter.side_effect = FilterApplyError(
        "The SSH filter could not be applied; previous access was kept."
    )
    await _save_draft(client, admin, [{"name": "Office", "cidr": "203.0.113.7"}])
    version = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    response = await client.post(f"{BASE}/host/apply", json={"draft_version": version}, headers=admin)
    assert response.status_code == 409
    assert "previous access was kept" in response.json()["detail"]
    assert (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["applied"]["revision"] == 0


@pytest.mark.asyncio
async def test_turning_the_filter_off_removes_it_and_keeps_recovery(client, admin, host):
    _diagnostics(host)
    await _save_draft(client, admin, [{"name": "Office", "cidr": "203.0.113.7"}])
    v = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    await client.post(f"{BASE}/host/apply", json={"draft_version": v}, headers=admin)
    await _save_draft(client, admin, [], enabled=False)
    v = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    response = await client.post(f"{BASE}/host/apply", json={"draft_version": v}, headers=admin)
    assert response.status_code == 200
    host.driver.remove_filter.assert_called_once()
    assert host.driver.ensure_recovery.call_count == 2


@pytest.mark.asyncio
async def test_an_enabled_filter_needs_at_least_one_range(client, admin, host):
    response = await _save_draft(client, admin, [])
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_applying_needs_infrastructure_deploy_but_checking_does_not(client, session, admin_user, host):
    _diagnostics(host)
    headers = await _custom(session, admin_user.organization_id, "cfg@lab.test", [("infrastructure", "configure")])
    assert (await client.post(f"{BASE}/host/check", headers=headers)).status_code == 200
    response = await client.post(f"{BASE}/host/apply", json={"draft_version": 0}, headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_matching_installer_probe_shows_login_tested(client, session, admin, host):
    from app.platform.platform_config_service import PlatformConfigService

    _diagnostics(host)
    await PlatformConfigService.set(
        session,
        "ssh_recovery_probe",
        json.dumps(
            {
                "instance_id": "481516",
                "result": "succeeded",
                "path": "gcp_iap",
                "probed_at": "2026-11-24T10:00:00Z",
                "filter_revision": 0,
            }
        ),
    )
    await session.commit()
    result = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert result["recovery"]["status"] == "login_tested"
    assert result["recovery"]["login_tested_at"] == "2026-11-24T10:00:00Z"


@pytest.mark.asyncio
async def test_a_probe_for_another_server_is_not_login_tested(client, session, admin, host):
    from app.platform.platform_config_service import PlatformConfigService

    _diagnostics(host)
    await PlatformConfigService.set(
        session,
        "ssh_recovery_probe",
        json.dumps(
            {
                "instance_id": "111",
                "result": "succeeded",
                "path": "gcp_iap",
                "probed_at": "2026-11-24T10:00:00Z",
                "filter_revision": 0,
            }
        ),
    )
    await session.commit()
    result = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert result["recovery"]["status"] == "configured_automatic"


@pytest.mark.asyncio
async def test_the_installer_administrator_key_is_shown_by_fingerprint_only(client, session, admin, host):
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(
        session,
        "admin_ssh_key",
        json.dumps({"fingerprint": "SHA256:abc", "account": "ubuntu", "instance_id": "481516"}),
    )
    await session.commit()
    state = (await client.get(BASE, headers=admin)).json()["application_host"]
    assert state["admin_key"] == {"fingerprint": "SHA256:abc", "account": "ubuntu", "current": True}


@pytest.mark.asyncio
async def test_a_repository_on_a_custom_host_without_an_approved_key_is_reported(
    client, session, admin, admin_user, host
):
    from app.models.github_repo import GitHubRepo

    session.add(
        GitHubRepo(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            git_ssh_url="git@git.lab.example.org:team/analysis.git",
            display_name="analysis",
        )
    )
    await session.commit()
    state = (await client.get(BASE, headers=admin)).json()["application_host"]
    assert state["git_trust"]["hosts_needing_keys"] == ["git.lab.example.org"]
    assert state["git_trust"]["message"].startswith("A connected code repository needs a security setting")
    assert "import-git-known-hosts" in state["git_trust"]["procedure"]


async def _apply_ranges(client, admin, ranges, enabled=True):
    await _save_draft(client, admin, ranges, enabled=enabled)
    v = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    return await client.post(f"{BASE}/host/apply", json={"draft_version": v}, headers=admin)


@pytest.mark.asyncio
async def test_editing_the_aws_filter_keeps_the_original_revocations_so_turning_it_off_restores_them(
    client, admin, host
):
    _diagnostics(host)
    original = {"GroupId": "sg-base", "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22}]}
    host.driver.apply_filter.return_value = {"provider": "aws", "revoked": [original], "filter_group": "sg-f"}
    assert (await _apply_ranges(client, admin, [{"name": "A", "cidr": "203.0.113.7"}])).status_code == 200
    host.driver.apply_filter.return_value = {"provider": "aws", "revoked": [], "filter_group": "sg-f"}
    assert (await _apply_ranges(client, admin, [{"name": "B", "cidr": "198.51.100.9"}])).status_code == 200
    assert (await _apply_ranges(client, admin, [], enabled=False)).status_code == 200
    record = host.driver.remove_filter.call_args.args[0]
    assert record["revoked"] == [original]
    assert record["filter_group"] == "sg-f"


# -- Review: durable, exclusive filter changes off the event loop ---------------------------------


@pytest.mark.asyncio
async def test_filter_provider_calls_run_off_the_event_loop(client, admin, host):
    import threading

    _diagnostics(host)
    seen = {}

    def record(name, value):
        def call(*args, **kwargs):
            seen[name] = threading.current_thread() is threading.main_thread()
            return value

        return call

    host.driver.ensure_recovery.side_effect = record("ensure_recovery", host.driver.ensure_recovery.return_value)
    host.driver.check_recovery.side_effect = record("check_recovery", [])
    host.driver.apply_filter.side_effect = record("apply_filter", {"provider": "gcp", "rules": []})
    assert (await _apply_ranges(client, admin, [{"name": "A", "cidr": "203.0.113.7"}])).status_code == 200
    assert seen == {"ensure_recovery": False, "check_recovery": False, "apply_filter": False}


@pytest.mark.asyncio
async def test_a_filter_change_is_refused_while_another_infrastructure_operation_runs(client, admin, host):
    from app.services import infrastructure_operations as ops

    _diagnostics(host)
    await _save_draft(client, admin, [{"name": "A", "cidr": "203.0.113.7"}])
    v = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]["version"]
    busy, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id=None))
    try:
        response = await client.post(f"{BASE}/host/apply", json={"draft_version": v}, headers=admin)
    finally:
        await ops.finish(busy, ops.succeeded())
    assert response.status_code == 409
    host.driver.apply_filter.assert_not_called()


@pytest.mark.asyncio
async def test_a_filter_change_is_one_settled_infrastructure_operation(client, session, admin, host):
    from sqlalchemy import select

    from app.models.infrastructure_operation import InfrastructureOperation

    _diagnostics(host)
    assert (await _apply_ranges(client, admin, [{"name": "A", "cidr": "203.0.113.7"}])).status_code == 200
    session.expire_all()
    ops_rows = (
        (
            await session.execute(
                select(InfrastructureOperation).where(InfrastructureOperation.action == "host_ssh_filter")
            )
        )
        .scalars()
        .all()
    )
    assert [(o.status, o.exclusive_slot) for o in ops_rows] == [("succeeded", None)]


@pytest.mark.asyncio
async def test_an_interrupted_filter_change_is_restored_from_its_saved_snapshot_before_the_next(client, admin, host):
    """The pre-change state is committed before the cloud changes, so a crash mid-change (here an
    unexpected error the driver could not roll back) leaves what is needed to put access back."""
    _diagnostics(host)
    host.driver.snapshot.return_value = {"provider": "gcp", "rules": {"bioaf-ssh-filter-deny-v4": None}}
    host.driver.apply_filter.side_effect = RuntimeError("connection reset")
    # An unexpected failure is a server error (the test client re-raises it); the operation is failed.
    with pytest.raises(RuntimeError):
        await _apply_ranges(client, admin, [{"name": "A", "cidr": "203.0.113.7"}])
    host.driver.restore.assert_not_called()

    check = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert any("did not finish" in p for p in check["recovery"]["problems"])

    host.driver.apply_filter.side_effect = None
    host.driver.apply_filter.return_value = {"provider": "gcp", "rules": []}
    assert (await _apply_ranges(client, admin, [{"name": "B", "cidr": "198.51.100.9"}])).status_code == 200
    host.driver.restore.assert_called_once_with({"provider": "gcp", "rules": {"bioaf-ssh-filter-deny-v4": None}})
    check = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert not any("did not finish" in p for p in check["recovery"]["problems"])


# -- Review 3: one writer at a time for the filter record; cancellation keeps ownership -------------


async def _hold_record_with_pending(other):
    """In ``other``'s open transaction: lock the filter record and add a recovery journal (not committed)."""
    from app.platform.platform_config_service import PlatformConfigService
    from app.services import host_ssh_access

    await PlatformConfigService.set(other, host_ssh_access.FILTER_KEY, json.dumps(host_ssh_access._empty()))
    await other.commit()
    record = json.loads(await PlatformConfigService.get_for_update(other, host_ssh_access.FILTER_KEY))
    record["pending"] = {"operation_id": 7, "snapshot": {"provider": "gcp", "rules": {}}}
    await PlatformConfigService.set(other, host_ssh_access.FILTER_KEY, json.dumps(record))


async def _stored(session) -> dict:
    from app.platform.platform_config_service import PlatformConfigService
    from app.services import host_ssh_access

    session.expire_all()
    return json.loads(await PlatformConfigService.get(session, host_ssh_access.FILTER_KEY))


@pytest.mark.asyncio
async def test_a_draft_save_keeps_a_recovery_journal_committed_while_it_ran(session, admin_user, host):
    import asyncio

    import app.database as database
    from app.services import host_ssh_access

    async with database.async_session_factory() as other:
        await _hold_record_with_pending(other)
        save = asyncio.create_task(
            host_ssh_access.save_draft(
                session,
                enabled=True,
                ranges=[{"name": "A", "cidr": "203.0.113.7"}],
                base_version=0,
                actor_id=admin_user.id,
            )
        )
        await asyncio.sleep(0.5)
        await other.commit()
    await save
    await session.commit()
    stored = await _stored(session)
    assert stored["pending"]["operation_id"] == 7
    assert stored["draft"]["version"] == 1


@pytest.mark.asyncio
async def test_check_access_keeps_a_recovery_journal_committed_while_it_ran(session, admin_user, host):
    import asyncio

    import app.database as database
    from app.services import host_ssh_access

    _diagnostics(host)
    async with database.async_session_factory() as other:
        await _hold_record_with_pending(other)
        check = asyncio.create_task(host_ssh_access.check_access(session))
        await asyncio.sleep(0.5)
        await other.commit()
    await check
    stored = await _stored(session)
    assert stored["pending"]["operation_id"] == 7
    assert any("did not finish" in p for p in stored["recovery"]["problems"])


@pytest.mark.asyncio
async def test_a_cancelled_filter_change_keeps_the_operation_until_the_cloud_call_returns(session, admin_user, host):
    """A worker thread keeps running after its awaiting task is cancelled, so the operation's exclusive
    slot is held until the provider call returns; a retry cannot start meanwhile."""
    import asyncio
    import threading

    import app.database as database
    from app.services import host_ssh_access
    from app.services import infrastructure_operations as ops

    _diagnostics(host)
    started, release = threading.Event(), threading.Event()

    def slow_apply(cidrs):
        started.set()
        release.wait(10)
        return {"provider": "gcp", "rules": []}

    host.driver.apply_filter.side_effect = slow_apply
    draft = await host_ssh_access.save_draft(
        session, enabled=True, ranges=[{"name": "A", "cidr": "203.0.113.7"}], base_version=0, actor_id=admin_user.id
    )
    await session.commit()
    change = asyncio.create_task(
        host_ssh_access.apply_draft(session, draft_version=draft["version"], actor_id=admin_user.id)
    )
    assert await asyncio.to_thread(started.wait, 10)
    change.cancel()
    await asyncio.sleep(0.3)
    async with database.async_session_factory() as s:
        assert await ops.active_operation(s) is not None
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await change
    async with database.async_session_factory() as s:
        assert await ops.active_operation(s) is None


# -- Cloud recovery not set up yet (owner decision 2026-11-25) ---------------------------------------


@pytest.mark.asyncio
async def test_recovery_that_was_never_set_up_is_reported_as_not_set_up(client, admin, host):
    _diagnostics(host)
    host.driver.recovery_missing.return_value = True
    result = (await client.post(f"{BASE}/host/check", headers=admin)).json()
    assert result["recovery"]["status"] == "not_configured"
    assert result["recovery"]["problems"] == []
    host.driver.ensure_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_set_up_recovery_runs_the_reconcile_as_an_operation_without_a_filter(client, session, admin, host):
    _diagnostics(host)
    host.driver.recovery_missing.return_value = True
    response = await client.post(f"{BASE}/host/recovery", headers=admin)
    assert response.status_code == 200, response.text
    host.driver.ensure_recovery.assert_called_once()
    host.driver.apply_filter.assert_not_called()
    assert response.json()["recovery"]["status"] == "configured_automatic"
    ops = (
        await session.execute(text("SELECT action, status FROM infrastructure_operations ORDER BY id DESC LIMIT 1"))
    ).one()
    assert tuple(ops) == ("host_recovery_setup", "succeeded")
    state = (await client.get(BASE, headers=admin)).json()["application_host"]
    assert state["recovery"]["status"] == "configured_automatic"
    assert state["filter"]["applied"]["revision"] == 0


@pytest.mark.asyncio
async def test_set_up_recovery_needs_deploy_permission(client, session, admin_user, host):
    headers = await _custom(
        session, admin_user.organization_id, "configure-only@lab.org", [("infrastructure", "configure")]
    )
    response = await client.post(f"{BASE}/host/recovery", headers=headers)
    assert response.status_code == 403
    host.driver.ensure_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_recovery_setup_is_recorded_as_failed_and_can_be_retried(client, session, admin, host):
    """Review 7: a setup that did not end configured must not be recorded as succeeded."""
    _diagnostics(host)
    host.driver.recovery_missing.return_value = True
    host.driver.check_recovery.return_value = ["Permission denied"]
    response = await client.post(f"{BASE}/host/recovery", headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["recovery"]["status"] == "needs_attention"
    op = (
        await session.execute(
            text("SELECT action, status, message FROM infrastructure_operations ORDER BY id DESC LIMIT 1")
        )
    ).one()
    assert (op.action, op.status) == ("host_recovery_setup", "failed")
    assert "Permission denied" in op.message
    host.driver.check_recovery.return_value = []
    retry = await client.post(f"{BASE}/host/recovery", headers=admin)
    assert retry.json()["recovery"]["status"] == "configured_automatic"


@pytest.mark.asyncio
@pytest.mark.parametrize("established_by", ["check", "setup", "filter"])
async def test_recovery_removed_after_it_existed_needs_attention_not_first_setup(client, admin, host, established_by):
    """Review 7: once recovery existed here, its absence is lost protection, not a new install."""
    _diagnostics(host)
    if established_by == "check":
        assert (await client.post(f"{BASE}/host/check", headers=admin)).json()["recovery"]["status"] == (
            "configured_automatic"
        )
    elif established_by == "setup":
        assert (await client.post(f"{BASE}/host/recovery", headers=admin)).status_code == 200
    else:
        assert (await _save_draft(client, admin, [{"name": "Office", "cidr": "203.0.113.7"}])).status_code == 200
        draft = (await client.get(BASE, headers=admin)).json()["application_host"]["filter"]["draft"]
        applied = await client.post(f"{BASE}/host/apply", json={"draft_version": draft["version"]}, headers=admin)
        assert applied.status_code == 200, applied.text
    host.driver.recovery_missing.return_value = True
    result = (await client.post(f"{BASE}/host/check", headers=admin)).json()["recovery"]
    assert result["status"] == "needs_attention"
    assert "The cloud recovery rule was removed outside bioAF." in result["problems"]
