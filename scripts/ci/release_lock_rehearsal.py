"""Rehearse a version-only release's lock refresh on a copy of backend/ (plan 11).

Replays the release workflow's own commands (.github/workflows/release.yml): the `sed` that bumps the project
version, then the "Refresh the lock for the new version" step. Fails unless the bump made `uv lock --check` fail, the
refresh succeeded, and the refreshed lock differs from the committed one only in the project's version. The
repository itself is never modified.

Needs uv and the package index, so it runs in CI (backend-lint), not in the pytest suite. Every command has a
timeout; a failure prints the command and its output.

    python ../scripts/ci/release_lock_rehearsal.py --repo ..
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import tempfile
import tomllib
from pathlib import Path

import yaml

PROJECT = "bioaf-backend"
REHEARSAL_VERSION = "2099.1.0"
BUMP_STEP = "Update version in source files"
REFRESH_STEP = "Refresh the lock for the new version"


class RehearsalFailed(Exception):
    pass


def _release_commands(workflow: Path) -> tuple[str, str]:
    """The pyproject bump line and the refresh step's script, as the release workflow runs them."""
    steps = {s.get("name"): s for s in yaml.safe_load(workflow.read_text())["jobs"]["release"]["steps"]}
    missing = [name for name in (BUMP_STEP, REFRESH_STEP) if name not in steps]
    if missing:
        raise RehearsalFailed(f"{workflow} has no step named {', '.join(repr(m) for m in missing)}")
    bump = [line.strip() for line in steps[BUMP_STEP]["run"].splitlines() if "sed -i" in line and "pyproject" in line]
    if len(bump) != 1:
        raise RehearsalFailed(f"expected one sed line bumping backend/pyproject.toml in {BUMP_STEP!r}, found {bump}")
    return bump[0], steps[REFRESH_STEP]["run"]


def _run(
    command: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run in its own process group, so a timeout also stops anything the command started (uv under bash)."""
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        raise RehearsalFailed(f"`{' '.join(command)}` timed out after {timeout:g}s\n{output}") from None
    return subprocess.CompletedProcess(command, process.returncode, output)


def _require(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        raise RehearsalFailed(f"{what} failed (exit {result.returncode}): `{' '.join(result.args)}`\n{result.stdout}")


def _packages(lock: Path) -> dict[str, list[str]]:
    selections: dict[str, list[str]] = {}
    for package in tomllib.loads(lock.read_text())["package"]:
        selections.setdefault(package["name"], []).append(package["version"])
    return {name: sorted(versions) for name, versions in selections.items()}


def rehearse(repo: Path, timeout: float) -> str:
    bump, refresh = _release_commands(repo / ".github" / "workflows" / "release.yml")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        backend = root / "backend"
        backend.mkdir()
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copy(repo / "backend" / name, backend / name)
        # The refresh step ends with `git diff --stat -- uv.lock`; give it the committed files to compare against.
        git = ["git", "-c", "user.name=rehearsal", "-c", "user.email=rehearsal@localhost", "-c", "commit.gpgsign=false"]
        for command in (["git", "init", "-q"], ["git", "add", "-A"], [*git, "commit", "-q", "-m", "committed"]):
            _require(_run(command, root, timeout), "preparing the copy")

        _require(_run(["bash", "-ec", bump], root, timeout, {**os.environ, "VERSION": REHEARSAL_VERSION}), "the bump")
        if _run(["uv", "lock", "--check"], backend, timeout).returncode == 0:
            raise RehearsalFailed("the version bump did not make the lock stale: `uv lock --check` still passes")
        _require(_run(["bash", "-ec", refresh], backend, timeout), f"the {REFRESH_STEP!r} step")

        before, after = _packages(repo / "backend" / "uv.lock"), _packages(backend / "uv.lock")
        if after.get(PROJECT) != [REHEARSAL_VERSION]:
            raise RehearsalFailed(f"the refreshed lock records {PROJECT} {after.get(PROJECT)}, not {REHEARSAL_VERSION}")
        moved = [
            f"{name} {', '.join(before.get(name, ['(absent)']))} -> {', '.join(after.get(name, ['(absent)']))}"
            for name in sorted(set(before) | set(after))
            if name != PROJECT and before.get(name) != after.get(name)
        ]
        if moved:
            raise RehearsalFailed("the refresh changed third-party selections:\n  " + "\n  ".join(moved))
    return f"Release lock rehearsal passed: {len(before) - 1} selections kept, only the project version changed."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository root (default: current directory)")
    parser.add_argument("--timeout", type=float, default=180, help="seconds allowed for each command")
    args = parser.parse_args(argv)
    try:
        print(rehearse(Path(args.repo).resolve(), args.timeout))
    except RehearsalFailed as exc:
        print(f"::error::Release lock rehearsal failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
