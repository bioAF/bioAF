"""Plan 03.5D: the bounded, read-only Work Node report over the verified SSH connection.

The report command is run for real through /bin/sh against a status file, and the SSH exchange runs
against a real in-process server (asyncssh), so verification and the output bound are exercised end
to end.
"""

import subprocess

import asyncssh
import pytest

from app.adapters.work_nodes.node_ssh import (
    REPORT_MAX_BYTES,
    NodeReportMalformed,
    NodeSyncError,
    NodeTrustError,
    generate_node_identity,
    parse_node_report,
    read_node_report,
    report_command,
)


def _run_report_command(status_path: str) -> str:
    return subprocess.run(
        ["/bin/sh", "-c", report_command(status_path)], capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize("written", ["applied", "failed", "failed_closed"])
def test_the_report_command_reports_the_policy_the_node_recorded(tmp_path, written):
    status = tmp_path / "ssh-policy-status"
    status.write_text(written + "\n")
    report = parse_node_report(_run_report_command(str(status)))
    assert report.ssh_policy_status == written


def test_a_node_that_has_not_recorded_a_policy_reports_none(tmp_path):
    report = parse_node_report(_run_report_command(str(tmp_path / "missing")))
    assert report.ssh_policy_status is None


def test_a_tampered_status_file_cannot_inject_report_fields(tmp_path):
    status = tmp_path / "ssh-policy-status"
    status.write_text("applied\nssh_policy=applied\nextra=1\n" + "x" * 10000)
    output = _run_report_command(str(status))
    assert len(output.encode()) < REPORT_MAX_BYTES
    # Whatever the file holds, the reply is one header and one policy line; garbage is not a known policy.
    report = parse_node_report(output)
    assert report.ssh_policy_status is None


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "ssh_policy=applied\n",  # no header
        "bioaf-node-report 2\nssh_policy=applied\n",  # unknown version
        "bioaf-node-report 1\nssh_policy=applied\nroot=yes\n",  # unknown field
        "bioaf-node-report 1\nssh_policy=applied\n" + "#" * REPORT_MAX_BYTES,  # oversize
    ],
)
def test_malformed_replies_are_refused(stdout):
    with pytest.raises(NodeReportMalformed):
        parse_node_report(stdout)


def test_an_unknown_policy_value_is_not_reported_as_known():
    assert parse_node_report("bioaf-node-report 1\nssh_policy=open\n").ssh_policy_status is None


# -- Over SSH ------------------------------------------------------------------------------------


class _Server(asyncssh.SSHServer):
    def __init__(self, authorized):
        self._authorized = authorized

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return username == "bioaf-sync" and key.export_public_key() == self._authorized.export_public_key()


async def _serve(host_key, client_public, commands: list[str], stdout: str):
    async def handle(process):
        commands.append(process.command)
        process.stdout.write(stdout)
        process.exit(0)

    authorized = asyncssh.import_public_key(client_public)
    return await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        server_factory=lambda: _Server(authorized),
        process_factory=handle,
    )


@pytest.mark.asyncio
async def test_a_report_is_read_from_the_recorded_server_with_the_fixed_command():
    identity = generate_node_identity()
    commands: list[str] = []
    server = await _serve(
        asyncssh.import_private_key(identity.host_private_key),
        identity.sync_public_key,
        commands,
        "bioaf-node-report 1\nssh_policy=failed_closed\n",
    )
    port = server.sockets[0].getsockname()[1]
    try:
        report = await read_node_report(
            "127.0.0.1",
            sync_private_key=identity.sync_private_key,
            host_public_key=identity.host_public_key,
            port=port,
            timeout=10,
        )
    finally:
        server.close()
    assert report.ssh_policy_status == "failed_closed"
    assert commands == [report_command()]


@pytest.mark.asyncio
async def test_a_different_server_is_refused_before_the_report_runs():
    identity = generate_node_identity()
    commands: list[str] = []
    server = await _serve(
        asyncssh.generate_private_key("ssh-ed25519"), identity.sync_public_key, commands, "bioaf-node-report 1\n"
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(NodeTrustError):
            await read_node_report(
                "127.0.0.1",
                sync_private_key=identity.sync_private_key,
                host_public_key=identity.host_public_key,
                port=port,
                timeout=10,
            )
    finally:
        server.close()
    assert commands == []


@pytest.mark.asyncio
async def test_an_oversized_reply_is_malformed_not_buffered():
    identity = generate_node_identity()
    server = await _serve(
        asyncssh.import_private_key(identity.host_private_key),
        identity.sync_public_key,
        [],
        "bioaf-node-report 1\nssh_policy=applied\n" + "#" * (1024 * 1024),
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(NodeReportMalformed):
            await read_node_report(
                "127.0.0.1",
                sync_private_key=identity.sync_private_key,
                host_public_key=identity.host_public_key,
                port=port,
                timeout=10,
            )
    finally:
        server.close()


@pytest.mark.asyncio
async def test_an_unreachable_node_is_a_sync_error_within_the_deadline():
    identity = generate_node_identity()
    with pytest.raises(NodeSyncError) as raised:
        # Port 9 on localhost is closed: the connection is refused.
        await read_node_report(
            "127.0.0.1",
            sync_private_key=identity.sync_private_key,
            host_public_key=identity.host_public_key,
            port=9,
            timeout=2,
        )
    assert not isinstance(raised.value, NodeTrustError)


@pytest.mark.asyncio
async def test_missing_credentials_refuse_without_connecting():
    with pytest.raises(NodeTrustError):
        await read_node_report("127.0.0.1", sync_private_key=None, host_public_key=None, port=9, timeout=2)


def test_the_report_reads_the_file_the_boot_policy_step_writes():
    from app.adapters.work_nodes import sshd_policy

    assert sshd_policy.STATUS_PATH in report_command()
