"""scripts/ci/release_lock_rehearsal.py: a version-only release, rehearsed on a copy (plan 11).

The script replays the release workflow's own version bump and lock refresh on a copy of backend/ and fails unless
the bump made the lock stale, the refresh repaired it, and only the project version moved. CI runs it against the
real package index; these tests drive it with a stand-in `uv` so they need no network and finish in seconds.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "release_lock_rehearsal.py"

# Stands in for uv. `uv lock --check` fails until `uv lock` has run; `uv lock` rewrites the project's version in
# uv.lock from pyproject.toml. FAKE_UV_MODE alters that: "upgrade" also moves a third-party selection, "fresh" makes
# the check pass before any refresh, "hang" never returns, "broken" makes `uv lock` fail.
FAKE_UV = """\
#!{python}
import os, re, sys, time
from pathlib import Path
mode = os.environ.get("FAKE_UV_MODE", "")
args = sys.argv[1:]
refreshed = Path(".refreshed")
if mode == "hang":
    time.sleep(60)
if args == ["lock", "--check"]:
    if mode == "fresh" or refreshed.exists():
        sys.exit(0)
    print("error: The lockfile at `uv.lock` needs to be updated", file=sys.stderr)
    sys.exit(1)
if args == ["lock"]:
    if mode == "broken":
        print("error: Failed to fetch: https://pypi.org/simple/fastapi/", file=sys.stderr)
        sys.exit(2)
    version = re.search(r'^version = "([^"]+)"', Path("pyproject.toml").read_text(), re.M).group(1)
    lock = Path("uv.lock").read_text()
    lock = re.sub(r'(name = "bioaf-backend"\\nversion = ")[^"]+', lambda m: m.group(1) + version, lock)
    if mode == "upgrade":
        lock = lock.replace('name = "alembic"\\nversion = "1.20.1"', 'name = "alembic"\\nversion = "9.9.9"')
    Path("uv.lock").write_text(lock)
    refreshed.touch()
    sys.exit(0)
print("unexpected uv call:", args, file=sys.stderr)
sys.exit(3)
"""

PYPROJECT = textwrap.dedent(
    """\
    [project]
    name = "bioaf-backend"
    version = "2026.11.9"
    dependencies = ["alembic"]
    """
)

LOCK = textwrap.dedent(
    """\
    version = 1
    revision = 5

    [[package]]
    name = "alembic"
    version = "1.20.1"

    [[package]]
    name = "bioaf-backend"
    version = "2026.11.9"
    """
)


@pytest.fixture(scope="module")
def rehearsal():
    spec = importlib.util.spec_from_file_location("release_lock_rehearsal", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A minimal repository: backend/ with a project and its lock, and the real release workflow."""
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "pyproject.toml").write_text(PYPROJECT)
    (root / "backend" / "uv.lock").write_text(LOCK)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "release.yml").write_text(
        (REPO / ".github" / "workflows" / "release.yml").read_text()
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV.format(python=sys.executable))
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin")
    monkeypatch.delenv("FAKE_UV_MODE", raising=False)
    return root


def _run(rehearsal, repo, capsys, *extra: str) -> tuple[int, str]:
    status = rehearsal.main(["--repo", str(repo), "--timeout", "5", *extra])
    out = capsys.readouterr()
    return status, out.out + out.err


def test_a_version_only_refresh_passes(rehearsal, repo, capsys):
    status, out = _run(rehearsal, repo, capsys)
    assert status == 0, out
    assert "only the project version changed" in out


def test_the_rehearsal_leaves_the_repository_untouched(rehearsal, repo, capsys):
    _run(rehearsal, repo, capsys)
    assert (repo / "backend" / "pyproject.toml").read_text() == PYPROJECT
    assert (repo / "backend" / "uv.lock").read_text() == LOCK


def test_a_refresh_that_moves_a_third_party_selection_fails_and_names_it(rehearsal, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_UV_MODE", "upgrade")
    status, out = _run(rehearsal, repo, capsys)
    assert status == 1
    assert "alembic 1.20.1 -> 9.9.9" in out


def test_a_bump_the_lock_check_does_not_notice_fails(rehearsal, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_UV_MODE", "fresh")
    status, out = _run(rehearsal, repo, capsys)
    assert status == 1
    assert "did not make the lock stale" in out


def test_a_failed_refresh_fails_with_uvs_output(rehearsal, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_UV_MODE", "broken")
    status, out = _run(rehearsal, repo, capsys)
    assert status == 1
    assert "Failed to fetch: https://pypi.org/simple/fastapi/" in out


def test_a_command_that_hangs_is_stopped_at_the_timeout(rehearsal, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_UV_MODE", "hang")
    status = rehearsal.main(["--repo", str(repo), "--timeout", "1"])
    out = capsys.readouterr()
    assert status == 1
    assert "timed out after 1s" in out.out + out.err


def test_the_release_workflow_steps_it_replays_must_exist(rehearsal, repo, capsys):
    workflow = repo / ".github" / "workflows" / "release.yml"
    workflow.write_text(workflow.read_text().replace("Refresh the lock for the new version", "Renamed step"))
    status, out = _run(rehearsal, repo, capsys)
    assert status == 1
    assert "Refresh the lock for the new version" in out
