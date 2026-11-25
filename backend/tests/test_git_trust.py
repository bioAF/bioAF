"""Plan 03C: Git startup verifies the server with shipped GitHub keys and approved custom-host keys.

No startup path scans a host key or disables checking for a host with approved material. A custom
host without approved material keeps its previous work-node behavior and is reported, never widened.
"""

import base64
import hashlib

import asyncssh
import pytest

from app.platform import git_trust
from app.platform.git_trust import KnownHostsError

GITHUB_FINGERPRINTS = {
    "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
    "SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
    "SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s",
}


def _fingerprint(line: str) -> str:
    blob = base64.b64decode(line.split()[2])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _pub(algorithm="ssh-ed25519"):
    return asyncssh.generate_private_key(algorithm).export_public_key().decode().strip()


def test_shipped_github_keys_match_githubs_published_fingerprints():
    lines = git_trust.github_known_hosts().splitlines()
    assert len(lines) == 3
    assert all(line.startswith("github.com ") for line in lines)
    assert {_fingerprint(line) for line in lines} == GITHUB_FINGERPRINTS


@pytest.mark.parametrize(
    "url, expected",
    [
        ("git@github.com:org/repo.git", ("github.com", 22)),
        ("ssh://git@git.example.org:2222/org/repo.git", ("git.example.org", 2222)),
        ("ssh://git.example.org/org/repo.git", ("git.example.org", 22)),
        ("git@gitlab.example.org:org/repo.git", ("gitlab.example.org", 22)),
        ("https://github.com/org/repo.git", None),
    ],
)
def test_ssh_host_of_a_repository_url(url, expected):
    assert git_trust.ssh_host(url) == expected


def test_valid_custom_entries_parse_with_fingerprints():
    key = _pub()
    entries = git_trust.parse_known_hosts(f"git.example.org {key}\n# comment\n\n[git.example.org]:2222 {key}\n")
    assert [(e.host, e.port) for e in entries] == [("git.example.org", 22), ("git.example.org", 2222)]
    assert entries[0].fingerprint.startswith("SHA256:")
    assert entries[1].line == f"[git.example.org]:2222 {key}"


@pytest.mark.parametrize(
    "text",
    [
        "* ssh-ed25519 AAAA",
        "!git.example.org ssh-ed25519 AAAA",
        "git.example.org,other.org {key}",
        "@cert-authority git.example.org {key}",
        "@revoked git.example.org {key}",
        "|1|abc=|def= {key}",
        "git.example.org ssh-dss AAAAB3NzaC1kc3M=",
        "git.example.org ssh-ed25519 not-base64!",
        "git.example.org ssh-ed25519",
        "git.example.org {key} trailing option",
        "git.example.org;rm -rf / {key}",
        "git.example.org {rsa1024}",
        "x" * 300 + " {key}",
    ],
)
def test_unsafe_or_malformed_entries_are_refused(text):
    key = _pub()
    rsa1024 = asyncssh.generate_private_key("ssh-rsa", key_size=1024).export_public_key().decode().strip()
    with pytest.raises(KnownHostsError):
        git_trust.parse_known_hosts(text.format(key=key, rsa1024=rsa1024))


def test_too_many_entries_are_refused():
    key = _pub()
    with pytest.raises(KnownHostsError):
        git_trust.parse_known_hosts("\n".join(f"h{i}.example.org {key}" for i in range(git_trust.MAX_ENTRIES + 1)))


def test_trust_for_repositories_reports_hosts_without_approved_material():
    key = _pub()
    custom = git_trust.parse_known_hosts(f"git.example.org {key}")
    trust = git_trust.trust_for(
        ["git@github.com:o/a.git", "git@git.example.org:o/b.git", "git@other.example.org:o/c.git"], custom
    )
    assert "github.com ssh-ed25519" in trust.known_hosts
    assert f"git.example.org {key}" in trust.known_hosts
    assert trust.untrusted_hosts == {("other.example.org", 22)}


@pytest.mark.asyncio
async def test_approved_custom_entries_are_stored_with_provenance_and_read_back(session):
    key = _pub()
    stored = await git_trust.import_custom_known_hosts(
        session, f"git.example.org {key}", source="operator import from /tmp/hosts"
    )
    await session.commit()
    assert stored[0]["fingerprint"].startswith("SHA256:")
    loaded = await git_trust.load_custom_entries(session)
    assert [(e.host, e.port, e.line) for e in loaded] == [("git.example.org", 22, f"git.example.org {key}")]


@pytest.mark.asyncio
async def test_an_invalid_import_changes_nothing(session):
    key = _pub()
    await git_trust.import_custom_known_hosts(session, f"git.example.org {key}", source="first")
    await session.commit()
    with pytest.raises(KnownHostsError):
        await git_trust.import_custom_known_hosts(session, "* ssh-ed25519 AAAA", source="bad")
    assert len(await git_trust.load_custom_entries(session)) == 1


# -- The four startup paths -------------------------------------------------------------------


def _work_node_spec(repos, **extra):
    trust = git_trust.trust_for([r["git_ssh_url"] for r in repos], [])
    return {
        "session_id": 7,
        "session_credentials": {"username": "sci", "password_hash": "$2b$x"},
        "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nk\n-----END OPENSSH PRIVATE KEY-----",
        "ssh_public_key": "ssh-ed25519 AAAAuser",
        "github_repos": repos,
        "git_known_hosts": trust.known_hosts,
        "git_untrusted_hosts": sorted(f"{h}:{p}" for h, p in trust.untrusted_hosts),
        **extra,
    }


@pytest.mark.parametrize("builder", ["gce", "ec2"])
def test_work_node_startup_clones_github_with_strict_checking(builder):
    from app.adapters.work_nodes.ec2 import _build_ec2_startup_script
    from app.adapters.work_nodes.gce import _build_startup_script

    build = _build_startup_script if builder == "gce" else _build_ec2_startup_script
    script = build(_work_node_spec([{"git_ssh_url": "git@github.com:o/a.git", "display_name": "a"}]))
    assert "ssh-keyscan" not in script
    assert "StrictHostKeyChecking=no" not in script
    assert "StrictHostKeyChecking=yes" in script
    assert "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" in script


@pytest.mark.parametrize("builder", ["gce", "ec2"])
def test_a_custom_host_without_material_keeps_its_previous_work_node_behavior_only(builder):
    from app.adapters.work_nodes.ec2 import _build_ec2_startup_script
    from app.adapters.work_nodes.gce import _build_startup_script

    build = _build_startup_script if builder == "gce" else _build_ec2_startup_script
    script = build(
        _work_node_spec(
            [
                {"git_ssh_url": "git@github.com:o/a.git", "display_name": "a"},
                {"git_ssh_url": "git@legacy.example.org:o/b.git", "display_name": "b"},
            ]
        )
    )
    github_clone = next(line for line in script.splitlines() if "git@github.com:o/a.git" in line)
    legacy_clone = next(line for line in script.splitlines() if "git@legacy.example.org:o/b.git" in line)
    assert "StrictHostKeyChecking=yes" in github_clone
    assert "StrictHostKeyChecking=no" in legacy_clone
    assert "ssh-keyscan" not in script


def test_rstudio_startup_writes_shipped_trust_and_never_scans():
    from app.adapters.notebooks.kubernetes import rstudio_git_ssh_setup

    setup = rstudio_git_ssh_setup(
        home_dir="/home/rstudio",
        username="sci",
        ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nk\n",
        known_hosts=git_trust.github_known_hosts(),
    )
    assert "ssh-keyscan" not in setup
    assert "github.com ssh-ed25519" in setup
    assert "StrictHostKeyChecking yes" in setup


def test_custom_pipeline_clone_verifies_the_server():
    from types import SimpleNamespace

    from app.services.custom_pipeline_service import CustomPipelineService

    step = CustomPipelineService._build_git_clone_init(
        SimpleNamespace(git_ssh_url="git@github.com:o/a.git", display_name="a"),
        known_hosts=git_trust.github_known_hosts(),
    )
    script = step.command[-1]
    assert "ssh-keyscan" not in script
    assert "StrictHostKeyChecking=yes" in script
    assert "github.com ssh-ed25519" in script


# -- Operator import and release maintenance commands ------------------------------------------


@pytest.mark.asyncio
async def test_import_command_stores_approved_material_and_prints_fingerprints(tmp_path, db_engine, capsys):
    from app.cli import import_git_known_hosts as cli

    key = _pub()
    path = tmp_path / "hosts"
    path.write_text(f"git.example.org {key}\n")
    assert await cli.run(str(path), source="vendor portal, checked by phone", engine=db_engine) == 0
    out = capsys.readouterr().out
    # Exact fields of the printed row "  <host>  <fingerprint>", not a substring match on the host.
    rows = [line.split() for line in out.splitlines()[1:]]
    assert [row[0] for row in rows] == ["git.example.org"]
    assert rows[0][1].startswith("SHA256:")
    assert key not in out


@pytest.mark.asyncio
async def test_import_command_refuses_invalid_material(tmp_path, db_engine, capsys):
    from app.cli import import_git_known_hosts as cli

    path = tmp_path / "hosts"
    path.write_text("* ssh-ed25519 AAAA\n")
    assert await cli.run(str(path), source="x", engine=db_engine) == 2


def test_github_maintenance_check_compares_with_live_metadata():
    from app.cli import check_github_known_hosts as cli

    shipped = [line.split(" ", 1)[1] for line in git_trust.github_known_hosts().splitlines()]
    assert cli.compare(shipped) == 0
    assert cli.compare(shipped[:2]) == 1
    assert cli.compare([*shipped, _pub()]) == 1
