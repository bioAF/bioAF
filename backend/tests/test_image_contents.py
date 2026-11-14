"""Image build inputs exclude local secrets and state (scripts/ci/check_image_contents.py).

``.gitignore`` is not a Docker boundary: a build sends its context to the builder, and ``COPY`` copies whatever
the context holds. These tests plant synthetic sentinels at denied paths and prove they never reach what a build
can see, while the inputs the images need still do. The scanner reports paths only, never contents.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "check_image_contents.py"
# Built at run time so no source file in a build context contains it.
TOKEN = f"bioaf-sentinel-{secrets.token_hex(8)}"


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location("check_image_contents", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# --- the scanner on synthetic archives -----------------------------------------------------------------


def test_denied_paths_are_reported_by_rule(checker):
    tar = _tar(
        {
            "app/.env": b"x",
            "app/.env.local": b"x",
            "app/.env.example": b"x",
            "app/terraform/aws/modules/storage/.terraform/terraform.tfstate": b"x",
            "app/terraform/aws/modules/storage/.terraform.lock.hcl": b"x",
            "app/terraform/state.tfstate.backup": b"x",
            "app/terraform/state.tfstate.1700000000.backup": b"x",
            "app/src/page.tfstate.test.tsx": b"x",
            "app/backups/pgdump-20260101-000000.dump": b"x",
            "app/server.js": b"x",
        }
    )
    found = checker.scan_tar(io.BytesIO(tar), token=None, root="app")
    paths = {f.path for f in found}
    assert paths == {
        "app/.env",
        "app/.env.local",
        "app/terraform/aws/modules/storage/.terraform/terraform.tfstate",
        "app/terraform/state.tfstate.backup",
        "app/terraform/state.tfstate.1700000000.backup",
        "app/backups/pgdump-20260101-000000.dump",
    }


def test_paths_outside_the_root_are_not_judged(checker):
    """Base-image files (system certificates, a distribution's own .env) are not the build's inputs."""
    tar = _tar({"etc/ssl/.env": b"x", "usr/lib/thing.dump": b"x"})
    assert checker.scan_tar(io.BytesIO(tar), token=None, root="app") == []


def test_a_sentinel_token_is_found_in_any_file_without_reporting_its_contents(checker, capsys):
    secret_line = f"API_KEY=super-secret {TOKEN}".encode()
    tar = _tar({"app/innocent.txt": b"a" * 100_000 + secret_line + b"b" * 10})
    found = checker.scan_tar(io.BytesIO(tar), token=TOKEN, root="app")
    assert [(f.path, f.rule) for f in found] == [("app/innocent.txt", "sentinel token")]
    checker.report(found, label="synthetic")
    out = capsys.readouterr()
    assert "super-secret" not in out.out + out.err
    assert "app/innocent.txt" in out.out + out.err


def test_a_token_split_across_read_blocks_is_still_found(checker):
    data = b"z" * (checker.BLOCK - 5) + TOKEN.encode() + b"z" * 10
    found = checker.scan_tar(io.BytesIO(_tar({"app/big.bin": data})), token=TOKEN, root="app")
    assert [f.path for f in found] == ["app/big.bin"]


def test_every_layer_of_a_saved_image_is_scanned(checker, tmp_path):
    """A file deleted by a later layer still ships in the earlier one."""
    clean = _tar({"app/server.js": b"ok"})
    leaky = _tar({"app/.env": b"x"})
    whiteout = _tar({"app/.wh..env": b""})
    image = io.BytesIO()
    with tarfile.open(fileobj=image, mode="w") as tar:
        layers = []
        for i, data in enumerate((clean, leaky, whiteout)):
            name = f"blobs/sha256/layer{i}"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            layers.append(name)
        manifest = json.dumps([{"Layers": layers}]).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    path = tmp_path / "image.tar"
    path.write_bytes(image.getvalue())
    found = checker.scan_saved_image(path, token=None, root="app")
    assert [f.path for f in found] == ["app/.env"]


def test_the_command_fails_on_findings_and_on_an_unreadable_archive(checker, tmp_path):
    good = tmp_path / "good.tar"
    good.write_bytes(_tar({"app/server.js": b"ok"}))
    bad = tmp_path / "bad.tar"
    bad.write_bytes(_tar({"app/.env": b"x"}))
    broken = tmp_path / "broken.tar"
    broken.write_bytes(b"not a tar")
    assert checker.main(["tar", str(good), "--root", "app"]) == 0
    assert checker.main(["tar", str(bad), "--root", "app"]) == 1
    assert checker.main(["tar", str(broken), "--root", "app"]) == 2


# --- the real build contexts, through Docker --------------------------------------------------------------

_DOCKER = shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _require_docker() -> None:
    if not _DOCKER:
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail("Docker is required in CI to prove the build-context exclusions")
        pytest.skip("Docker daemon not available")


def _tracked_copy(src: Path, dest: Path) -> None:
    """The context as a checkout has it: files Git tracks or would add, never what .gitignore keeps local."""
    names = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
        cwd=src,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for raw in filter(None, names):
        rel = raw.decode()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if (src / rel).is_file():
            shutil.copy2(src / rel, target)


@pytest.mark.parametrize(
    ("context_dir", "dockerfile", "kind", "required"),
    [
        (
            "frontend",
            "docker/Dockerfile.frontend",
            "frontend",
            ["package.json", "package-lock.json", "next.config.js", "src", "public", "tsconfig.json"],
        ),
        (
            "backend",
            "docker/Dockerfile.backend",
            "backend",
            [
                "pyproject.toml",
                "uv.lock",
                "entrypoint.sh",
                "app/main.py",
                "alembic",
                "terraform/aws/modules/storage/main.tf",
                "terraform/aws/modules/storage/.terraform.lock.hcl",
            ],
        ),
    ],
)
def test_the_build_context_excludes_planted_secrets_and_keeps_required_inputs(
    checker, tmp_path, context_dir, dockerfile, kind, required
):
    _require_docker()
    context = tmp_path / "context"
    _tracked_copy(REPO / context_dir, context)
    planted = checker.plant(context, kind, TOKEN)
    assert len(planted) == len(checker.SENTINELS[kind])
    probe_dir = tmp_path / "docker"
    probe_dir.mkdir()

    names, found = checker.scan_context(context, REPO / dockerfile, token=TOKEN, workdir=probe_dir)

    assert found == [], [f"{f.path}: {f.rule}" for f in found]
    for rel in required:
        assert any(n == rel or n.startswith(rel + "/") for n in names), f"{rel} missing from the build context"
