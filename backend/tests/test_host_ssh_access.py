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
    driver.apply_filter.return_value = {"provider": "gcp", "rules": ["bioaf-ssh-filter-deny-v4"]}
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
