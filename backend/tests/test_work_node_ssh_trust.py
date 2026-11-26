"""Work node SSH trust: the backend reaches a node only as the server identity it recorded at launch.

These run a real SSH server in-process (asyncssh) so verification is exercised end to end: a
matching host key lets the sync command run, any other key refuses before a command runs.
"""

import asyncssh
import pytest

from app.adapters.work_nodes.node_ssh import (
    NodeIdentity,
    NodeTrustError,
    generate_node_identity,
    host_key_install_lines,
    run_sync_command,
)


class _Server(asyncssh.SSHServer):
    def __init__(self, authorized):
        self._authorized = authorized

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return username == "bioaf-sync" and key.export_public_key() == self._authorized.export_public_key()


async def _serve(host_key, client_public, commands: list[str], exit_status: int = 0):
    async def handle(process):
        commands.append(process.command)
        process.exit(exit_status)

    authorized = asyncssh.import_public_key(client_public)
    return await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        server_factory=lambda: _Server(authorized),
        process_factory=handle,
    )


def test_identity_has_distinct_sync_and_host_keys():
    identity = generate_node_identity()
    assert isinstance(identity, NodeIdentity)
    assert identity.sync_public_key.startswith("ssh-ed25519 ")
    assert identity.host_public_key.startswith("ssh-ed25519 ")
    assert identity.sync_public_key != identity.host_public_key
    assert "PRIVATE KEY" in identity.sync_private_key
    assert "PRIVATE KEY" in identity.host_private_key
    # Two launches never share keys.
    assert generate_node_identity().host_public_key != identity.host_public_key


@pytest.mark.asyncio
async def test_sync_runs_against_the_recorded_host_identity():
    identity = generate_node_identity()
    commands: list[str] = []
    server = await _serve(
        asyncssh.import_private_key(identity.host_private_key), identity.sync_public_key, commands, exit_status=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        status = await run_sync_command(
            "127.0.0.1",
            sync_private_key=identity.sync_private_key,
            host_public_key=identity.host_public_key,
            command="echo saved",
            port=port,
            timeout=10,
        )
    finally:
        server.close()
    assert status == 0
    assert commands == ["echo saved"]


@pytest.mark.asyncio
async def test_a_different_server_key_is_refused_before_any_command():
    identity = generate_node_identity()
    impostor = asyncssh.generate_private_key("ssh-ed25519")
    commands: list[str] = []
    server = await _serve(impostor, identity.sync_public_key, commands)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(NodeTrustError):
            await run_sync_command(
                "127.0.0.1",
                sync_private_key=identity.sync_private_key,
                host_public_key=identity.host_public_key,
                command="echo saved",
                port=port,
                timeout=10,
            )
    finally:
        server.close()
    assert commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["sync_private_key", "host_public_key"])
async def test_missing_credentials_refuse_without_connecting(missing):
    identity = generate_node_identity()
    kwargs = {"sync_private_key": identity.sync_private_key, "host_public_key": identity.host_public_key}
    kwargs[missing] = None
    with pytest.raises(NodeTrustError):
        # Port 9 (discard) is never contacted: the refusal happens first.
        await run_sync_command("127.0.0.1", command="true", port=9, timeout=2, **kwargs)


@pytest.mark.asyncio
async def test_nonzero_sync_status_is_reported_not_hidden():
    identity = generate_node_identity()
    commands: list[str] = []
    server = await _serve(asyncssh.import_private_key(identity.host_private_key), identity.sync_public_key, commands, 3)
    port = server.sockets[0].getsockname()[1]
    try:
        status = await run_sync_command(
            "127.0.0.1",
            sync_private_key=identity.sync_private_key,
            host_public_key=identity.host_public_key,
            command="false",
            port=port,
            timeout=10,
        )
    finally:
        server.close()
    assert status == 3


def test_host_key_install_writes_only_the_launch_key_with_root_permissions():
    identity = generate_node_identity()
    script = "\n".join(host_key_install_lines(identity.host_private_key, identity.host_public_key))
    # The image's shared host keys are removed so another node from the same image cannot pose as this one.
    assert "rm -f /etc/ssh/ssh_host_*" in script
    assert "/etc/ssh/bioaf_host_ed25519_key" in script
    assert "chmod 600 /etc/ssh/bioaf_host_ed25519_key" in script
    assert "chown root:root /etc/ssh/bioaf_host_ed25519_key" in script
    assert "HostKey /etc/ssh/bioaf_host_ed25519_key" in script
    # sshd's configuration is validated before it is reloaded.
    assert "sshd -t" in script
    assert identity.host_public_key in script
