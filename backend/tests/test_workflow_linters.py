"""The workflow linters CI installs (scripts/ci/install_workflow_linters.py, scripts/ci/workflow_linter_versions.json).

Each tool comes from a fixed release archive in its maintainer's repository, and the installer verifies the committed
SHA-256 before anything is extracted or run. A download or checksum failure fails the job.

Standard library and PyYAML only: these tests run in the focused workflow job without the backend environment.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "install_workflow_linters.py"
MANIFEST = REPO / "scripts" / "ci" / "workflow_linter_versions.json"
MAINTAINERS = {"actionlint": "rhysd/actionlint", "zizmor": "zizmorcore/zizmor", "shellcheck": "koalaman/shellcheck"}


def test_the_manifest_pins_each_linter_to_a_release_archive_and_checksum():
    tools = {t["name"]: t for t in json.loads(MANIFEST.read_text())["tools"]}
    assert set(tools) == set(MAINTAINERS)
    for name, tool in tools.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", tool["version"]), name
        prefix = f"https://github.com/{MAINTAINERS[name]}/releases/download/v{tool['version']}/"
        assert tool["url"].startswith(prefix), name
        assert re.search(r"linux[._-](amd64|x86_64)|x86_64-unknown-linux", tool["url"]), name
        assert tool["url"].endswith((".tar.gz", ".tar.xz")), name
        assert "latest" not in tool["url"], name
        assert re.fullmatch(r"[0-9a-f]{64}", tool["sha256"]), name
        assert tool["binary"] == name


def _archive(tmp_path: Path, name: str, marker: Path, member: str | None = None) -> Path:
    """A release-shaped archive holding a stand-in binary that records being run and prints its version."""
    script = f'#!/bin/sh\necho ran >> "{marker}"\necho "{name} 9.9.9"\n'.encode()
    path = tmp_path / f"{name}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(member or f"{name}-v9.9.9/{name}")
        info.size, info.mode = len(script), 0o755
        tar.addfile(info, io.BytesIO(script))
        readme = tarfile.TarInfo("README.md")
        readme.size = 2
        tar.addfile(readme, io.BytesIO(b"hi"))
    return path


def _manifest(tmp_path: Path, tools: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"tools": tools}))
    return path


def _tool(name: str, archive: Path, sha256: str | None = None) -> dict:
    digest = sha256 or hashlib.sha256(archive.read_bytes()).hexdigest()
    return {
        "name": name,
        "version": "9.9.9",
        "url": archive.as_uri(),
        "sha256": digest,
        "binary": name,
        "version_args": ["--version"],
    }


def _install(tmp_path: Path, manifest: Path) -> tuple[subprocess.CompletedProcess, Path, Path]:
    dest, path_file = tmp_path / "tools", tmp_path / "github_path"
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest), "--dest", str(dest), "--path-file", str(path_file)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, dest, path_file


def test_verified_archives_install_onto_path_and_report_versions(tmp_path):
    marker = tmp_path / "ran"
    tools = [_tool(n, _archive(tmp_path, n, marker)) for n in ("alpha", "beta")]
    done, dest, path_file = _install(tmp_path, _manifest(tmp_path, tools))
    assert done.returncode == 0, done.stdout + done.stderr
    assert (dest / "alpha").is_file() and (dest / "beta").is_file()
    assert path_file.read_text().strip() == str(dest)
    assert "alpha 9.9.9" in done.stdout and "beta 9.9.9" in done.stdout
    assert not (dest / "README.md").exists(), "only the named binary is extracted"


def test_a_checksum_mismatch_fails_before_anything_is_extracted_or_run(tmp_path):
    marker = tmp_path / "ran"
    good = _tool("alpha", _archive(tmp_path, "alpha", marker))
    bad = _tool("beta", _archive(tmp_path, "beta", marker), sha256="0" * 64)
    done, dest, path_file = _install(tmp_path, _manifest(tmp_path, [good, bad]))
    assert done.returncode != 0
    assert "::error::" in done.stdout + done.stderr and "beta" in done.stdout + done.stderr
    assert not (dest / "beta").exists()
    assert not marker.exists(), "no tool runs when any archive fails verification"
    assert not path_file.exists() or path_file.read_text() == ""


def test_a_failed_download_fails_the_install(tmp_path):
    missing = {**_tool("alpha", _archive(tmp_path, "alpha", tmp_path / "ran")), "url": (tmp_path / "nope.tgz").as_uri()}
    done, _, _ = _install(tmp_path, _manifest(tmp_path, [missing]))
    assert done.returncode != 0 and "::error::" in done.stdout + done.stderr


def test_an_archive_without_the_binary_fails(tmp_path):
    archive = _archive(tmp_path, "alpha", tmp_path / "ran", member="other/thing")
    done, _, _ = _install(tmp_path, _manifest(tmp_path, [_tool("alpha", archive)]))
    assert done.returncode != 0 and "::error::" in done.stdout + done.stderr


@pytest.mark.parametrize("url", ["http://example.invalid/x.tar.gz", "https://example.invalid/latest/x.tar.gz"])
def test_the_installer_refuses_an_insecure_or_moving_url(tmp_path, url):
    tool = {**_tool("alpha", _archive(tmp_path, "alpha", tmp_path / "ran")), "url": url}
    done, _, _ = _install(tmp_path, _manifest(tmp_path, [tool]))
    assert done.returncode != 0 and "::error::" in done.stdout + done.stderr
