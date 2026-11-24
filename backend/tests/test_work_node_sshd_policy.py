"""Plan 03B (D03-5): the Work Node account-scoped sshd policy.

The compiled include is exercised with the real OpenSSH parser (``sshd -T -C``) when one is
available, including an older image's earlier global ``PasswordAuthentication yes``. A local parse
is not guest/live SSH acceptance; it proves the generated configuration means what it claims.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.adapters.work_nodes.sshd_policy import (
    POLICY_PATH,
    SshPolicyError,
    compile_account_policy,
    probe_addresses,
    sshd_policy_install_lines,
)

SSHD = shutil.which("sshd") or ("/usr/sbin/sshd" if Path("/usr/sbin/sshd").exists() else None)
needs_sshd = pytest.mark.skipif(SSHD is None, reason="OpenSSH sshd is not installed here")


def _effective(tmp_path: Path, policy: str, user: str, addr: str, *, earlier_global: str = "yes") -> dict:
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir(exist_ok=True)
    host_key = tmp_path / "hostkey"
    if not host_key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True)
    (conf_d / "00-bioaf-account-policy.conf").write_text(policy)
    # An older image: cloud default first, then bioAF's old global override.
    (conf_d / "60-cloudimg-settings.conf").write_text("PasswordAuthentication no\n")
    (conf_d / "99-bioaf-password-auth.conf").write_text(f"PasswordAuthentication {earlier_global}\n")
    main = tmp_path / "sshd_config"
    main.write_text(
        f"Include {conf_d}/*.conf\nHostKey {host_key}\nPasswordAuthentication yes\nUsePAM no\n"
        "Subsystem sftp internal-sftp\n"
    )
    subprocess.run([SSHD, "-t", "-f", str(main)], check=True, capture_output=True)
    out = subprocess.run(
        [SSHD, "-T", "-f", str(main), "-C", f"user={user},addr={addr},host=h,laddr=10.0.0.2,lport=22"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


@needs_sshd
def test_with_a_range_inside_allows_password_or_key_and_outside_is_key_only(tmp_path):
    policy = compile_account_policy("sci", ["10.20.0.0/16", "2001:db8::/32"])
    inside = _effective(tmp_path, policy, "sci", "10.20.3.4")
    inside6 = _effective(tmp_path, policy, "sci", "2001:db8::5")
    outside = _effective(tmp_path, policy, "sci", "203.0.113.9")
    for eff in (inside, inside6):
        assert eff["passwordauthentication"] == "yes"
        assert eff["pubkeyauthentication"] == "yes"
    assert outside["authenticationmethods"] == "publickey"
    assert outside["passwordauthentication"] == "no"
    assert outside["kbdinteractiveauthentication"] == "no"


@needs_sshd
@pytest.mark.parametrize("ranges", [[], ["10.20.0.0/16"]])
def test_sync_is_key_only_everywhere_and_other_accounts_keep_their_policy(tmp_path, ranges):
    policy = compile_account_policy("sci", ranges)
    for addr in ("10.20.3.4", "203.0.113.9"):
        sync = _effective(tmp_path, policy, "bioaf-sync", addr)
        assert sync["authenticationmethods"] == "publickey"
        assert sync["passwordauthentication"] == "no"
    operator = _effective(tmp_path, policy, "ubuntu", "203.0.113.9")
    # The operator account gets whatever the image's own global configuration says.
    assert operator["authenticationmethods"] == "any"
    assert operator["passwordauthentication"] == "no"


@needs_sshd
def test_without_a_range_the_scientist_keeps_password_or_key_from_anywhere(tmp_path):
    policy = compile_account_policy("sci", [])
    eff = _effective(tmp_path, policy, "sci", "203.0.113.9", earlier_global="no")
    assert eff["passwordauthentication"] == "yes"
    assert eff["authenticationmethods"] == "any"


@pytest.mark.parametrize("username", ["root; rm", "Sci", "a b", "", "x" * 40, "bioaf-sync"])
def test_unsafe_usernames_are_refused(username):
    with pytest.raises(SshPolicyError):
        compile_account_policy(username, [])


@pytest.mark.parametrize(
    "ranges", [["0.0.0.0/0"], ["::/0"], ["10.0.0.0/8 PasswordAuthentication yes"], ["nope"], ["10.0.0.1/8"]]
)
def test_unsafe_or_malformed_ranges_are_refused(ranges):
    with pytest.raises(SshPolicyError):
        compile_account_policy("sci", ranges)


def test_probe_addresses_fall_inside_and_outside_every_range():
    inside, outside = probe_addresses(["192.0.2.0/24", "10.1.0.0/16"])
    assert inside == "192.0.2.1"
    assert outside not in ("192.0.2.1",) and not outside.startswith("192.0.2.") and not outside.startswith("10.1.")


def test_install_lines_validate_and_fail_closed_with_a_range():
    lines = "\n".join(sshd_policy_install_lines("sci", ["10.20.0.0/16"]))
    assert POLICY_PATH in lines
    assert "sshd -t" in lines
    assert "sshd -T -C" in lines
    # Retire the older global override; never write a new one.
    assert "rm -f /etc/ssh/sshd_config.d/99-bioaf-password-auth.conf" in lines
    assert "PasswordAuthentication yes' >" not in lines
    # If the effective result is not what the range promises, the scientist becomes key-only everywhere.
    assert "failed_closed" in lines
    assert "/etc/bioaf/ssh-policy-status" in lines


@pytest.mark.parametrize("builder", ["gce", "ec2"])
def test_startup_scripts_use_the_account_policy_not_a_global_password_override(builder):
    from app.adapters.work_nodes.ec2 import _build_ec2_startup_script
    from app.adapters.work_nodes.gce import _build_startup_script

    build = _build_startup_script if builder == "gce" else _build_ec2_startup_script
    script = build(
        {
            "session_id": 7,
            "session_credentials": {"username": "sci", "password_hash": "$2b$x"},
            "ssh_policy": {"revision": 3, "ranges": ["10.20.0.0/16"]},
            "sync_public_key": "ssh-ed25519 AAAAsync",
        }
    )
    assert POLICY_PATH in script
    assert "Match User sci Address 10.20.0.0/16" in script
    assert "99-bioaf-password-auth.conf" in script  # removed, not written
    assert "printf 'PasswordAuthentication yes\\n' >" not in script
    assert "sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/'" not in script


@pytest.mark.parametrize("template", ["PACKER_VM_TEMPLATE", "PACKER_VM_TEMPLATE_AWS"])
def test_new_work_node_images_carry_no_global_password_override(template):
    """The launch-time account policy owns password login; images keep the provider's own defaults."""
    from app.services import environment_build_service

    text = getattr(environment_build_service, template)
    assert "PasswordAuthentication" not in text
    assert "99-bioaf-password-auth.conf" not in text
    assert "openssh-server" in text


# -- The applied outcome travels with heartbeats ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reported, stored", [("applied", "applied"), ("failed_closed", "failed_closed"), ("bogus", None)]
)
async def test_heartbeat_records_the_nodes_applied_login_policy(
    client, session, comp_bio_user, comp_bio_token, reported, stored
):

    from app.models.notebook_session import ComputeSession

    node = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        heartbeat_token="hb-tok",
        ssh_policy_revision=4,
    )
    session.add(node)
    await session.commit()
    response = await client.post(
        f"/api/v1/work-nodes/sessions/{node.id}/heartbeat",
        headers={"X-Heartbeat-Token": "hb-tok", "X-SSH-Policy": reported},
    )
    assert response.status_code == 200
    detail = await client.get(
        f"/api/v1/work-nodes/sessions/{node.id}", headers={"Authorization": f"Bearer {comp_bio_token}"}
    )
    body = detail.json()
    assert body["ssh_policy_status"] == stored
    assert body["ssh_policy_revision"] == 4


@pytest.mark.parametrize("builder", ["gce", "ec2"])
def test_the_heartbeat_reports_the_policy_status(builder):
    from app.adapters.work_nodes.ec2 import _build_ec2_startup_script
    from app.adapters.work_nodes.gce import _build_startup_script

    build = _build_startup_script if builder == "gce" else _build_ec2_startup_script
    script = build(
        {
            "session_id": 7,
            "session_credentials": {"username": "sci", "password_hash": "$2b$x"},
            "heartbeat_token": "tok",
            "api_base_url": "https://bioaf.example.org",
        }
    )
    cron = next(line for line in script.splitlines() if "heartbeat" in line and "crontab" in line)
    assert "X-SSH-Policy: $(cat /etc/bioaf/ssh-policy-status" in cron
