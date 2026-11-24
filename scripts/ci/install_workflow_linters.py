"""Install the workflow linters from fixed release archives, verified before anything is extracted or run.

scripts/ci/workflow_linter_versions.json names each tool's release, archive URL in the maintainer's repository and
the archive's SHA-256. Every archive is downloaded and checked first; only when all match is each named binary
extracted (and nothing else from the archive), its directory written to GITHUB_PATH, and each version printed. A
download failure, checksum mismatch or missing binary fails the job, and no tool runs.

Standard library only:

    python3 scripts/ci/install_workflow_linters.py --manifest scripts/ci/workflow_linter_versions.json \\
        --dest "$RUNNER_TEMP/workflow-linters" --path-file "$GITHUB_PATH"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


class InstallError(Exception):
    pass


def _check_url(name: str, url: str) -> None:
    # file:// serves the tests; a real manifest names an https release download.
    if not url.startswith(("https://", "file://")):
        raise InstallError(f"{name}: the archive URL must use https")
    if "/latest/" in url:
        raise InstallError(f"{name}: the archive URL must name a fixed release, not latest")


def _download(name: str, url: str, target: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
    except OSError as exc:
        raise InstallError(f"{name}: download failed ({type(exc).__name__})") from exc


def _verify(name: str, archive: Path, expected: str) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        raise InstallError(f"{name}: archive checksum {digest} does not match the committed {expected}")


def _extract_binary(name: str, archive: Path, binary: str, dest: Path) -> Path:
    try:
        with tarfile.open(archive) as tar:
            members = [m for m in tar.getmembers() if m.isfile() and posixpath.basename(m.name) == binary]
            if len(members) != 1:
                raise InstallError(f"{name}: the archive does not hold exactly one {binary}")
            source = tar.extractfile(members[0])
            assert source is not None
            target = dest / binary
            target.write_bytes(source.read())
    except tarfile.TarError as exc:
        raise InstallError(f"{name}: the archive could not be read") from exc
    target.chmod(0o755)
    return target


def install(manifest: Path, dest: Path, path_file: Path | None) -> list[str]:
    tools = json.loads(manifest.read_text())["tools"]
    with tempfile.TemporaryDirectory() as tmp:
        archives = []
        for tool in tools:
            _check_url(tool["name"], tool["url"])
            archive = Path(tmp) / f"{tool['name']}.archive"
            _download(tool["name"], tool["url"], archive)
            _verify(tool["name"], archive, tool["sha256"])
            archives.append((tool, archive))
        dest.mkdir(parents=True, exist_ok=True)
        binaries = [(tool, _extract_binary(tool["name"], a, tool["binary"], dest)) for tool, a in archives]
    if path_file is not None:
        with path_file.open("a", encoding="utf-8") as out:
            out.write(f"{dest}\n")
    versions = []
    for tool, binary in binaries:
        done = subprocess.run([str(binary), *tool["version_args"]], capture_output=True, text=True, check=True)
        first = (done.stdout or done.stderr).strip().splitlines()
        versions.append(f"{tool['name']} {tool['version']}: {first[0] if first else '(no output)'}")
    return versions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--path-file", type=Path, help="append the tool directory here (GITHUB_PATH)")
    args = parser.parse_args(argv)
    try:
        versions = install(args.manifest, args.dest, args.path_file)
    except InstallError as exc:
        print(f"::error::Workflow linter install failed: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"::error::Workflow linter install failed: {exc.cmd[0]} did not report its version")
        return 1
    for line in versions:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
