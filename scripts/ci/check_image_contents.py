"""Check that image build inputs and image layers hold no local secrets or state.

``.gitignore`` is not a Docker boundary. A build sends its context to the builder and ``COPY`` copies whatever the
context holds, so a developer's ``.env``, Terraform state or database dump can end up in an image layer. The
``.dockerignore`` next to each context excludes them; this script proves the exclusion on what a build actually sees:

- ``context``: the effective build context of a Dockerfile, as BuildKit transmits it after ``.dockerignore``
  (including a Dockerfile-specific ``<Dockerfile>.dockerignore``, which replaces the context's own);
- ``image``: every layer of a built image (``docker save``), so a file a later layer deletes is still found;
- ``tar``: a tar archive already on disk (what the other two scan).

It is not a secret scanner. It reports files at known denied paths (``.env`` files, Terraform state and provider
caches, database dumps) and, with ``--token``, files containing a synthetic sentinel planted by a test. It prints
paths and rule names only, never file contents.

Standard library and the Docker CLI only::

    python3 scripts/ci/check_image_contents.py context frontend --dockerfile docker/Dockerfile.frontend --token T
    python3 scripts/ci/check_image_contents.py image bioaf/bioaf-frontend:pr-1 --root app --token T

Exit status: 0 nothing found, 1 findings, 2 the input could not be read (never treated as clean).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

BLOCK = 1 << 20
# terraform.tfstate and its backups (terraform.tfstate.backup, terraform.tfstate.1700000000.backup).
_TFSTATE = re.compile(r"\.tfstate(\.\d+)?(\.backup)?$")

# Probe that copies the whole effective context into an empty image: its export is exactly what the builder saw.
_PROBE = "FROM scratch\nCOPY . /\n"


# Synthetic files planted at paths each context must exclude, by context. Each holds only the sentinel token.
SENTINELS: dict[str, list[str]] = {
    "frontend": [
        ".env",
        ".env.local",
        ".env.production",
        "src/.env",
        "node_modules/leak/index.js",
        ".next/cache/leak",
        ".swc/leak",
        "coverage/leak.json",
        "test-results/leak.txt",
        "playwright-report/leak.html",
        "credentials.json",
        "service-account.json",
        "db.dump",
    ],
    "backend": [
        ".env",
        ".env.local",
        ".venv/lib/leak.py",
        ".pytest_cache/leak",
        ".mypy_cache/leak",
        ".ruff_cache/leak",
        "app/__pycache__/leak.pyc",
        ".coverage",
        "test-artifacts/leak.txt",
        "credentials.json",
        "pgdump-20260101-000000.dump",
        "terraform/aws/modules/storage/.terraform/terraform.tfstate",
        "terraform/aws/modules/storage/.terraform/providers/leak",
        "terraform/modules/storage/terraform.tfstate",
        "terraform/modules/storage/terraform.tfstate.backup",
    ],
}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str


class Unreadable(Exception):
    """The archive or image could not be read, so nothing was verified."""


def denied_rule(path: str) -> str | None:
    """The rule a path breaks, or None. Paths are archive member names (no leading slash)."""
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    if name.startswith(".wh."):
        return None  # an overlay whiteout marks a deletion; it carries no content
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file"
    if ".terraform" in parts[:-1]:
        return "terraform working directory"
    if _TFSTATE.search(name):
        return "terraform state"
    if name.endswith(".dump"):
        return "database dump"
    return None


def _clean(name: str) -> str:
    """An archive member name without a leading ``./`` or ``/`` (a dotfile keeps its dot)."""
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _under(path: str, root: str | None) -> bool:
    return not root or path == root or path.startswith(root.rstrip("/") + "/")


def _contains(stream: IO[bytes], token: bytes) -> bool:
    """Whether the stream holds ``token``, read in bounded blocks (a token may straddle two)."""
    tail = b""
    while True:
        block = stream.read(BLOCK)
        if not block:
            return False
        if token in tail + block:
            return True
        tail = block[-(len(token) - 1) :] if len(token) > 1 else b""


def plant(context: Path, kind: str, token: str) -> list[Path]:
    """Write the synthetic sentinels for ``kind`` into ``context``; refuses to overwrite an existing file."""
    planted = []
    for rel in SENTINELS[kind]:
        target = context / rel
        if target.exists():
            raise FileExistsError(f"{target} exists; sentinels are planted only into disposable copies")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{token}\n")
        planted.append(target)
    return planted


def scan_tar(fileobj: IO[bytes], *, token: str | None, root: str | None) -> list[Finding]:
    """Findings for every regular file in the archive under ``root`` (all files when root is empty)."""
    found: list[Finding] = []
    needle = token.encode() if token else None
    try:
        with tarfile.open(fileobj=fileobj, mode="r|*") as tar:
            for member in tar:
                name = _clean(member.name)
                if not member.isfile() or not _under(name, root):
                    continue
                rule = denied_rule(name)
                if rule:
                    found.append(Finding(name, rule))
                    continue
                if needle:
                    data = tar.extractfile(member)
                    if data is not None and _contains(data, needle):
                        found.append(Finding(name, "sentinel token"))
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise Unreadable(f"archive could not be read: {type(exc).__name__}") from exc
    return found


def scan_saved_image(path: Path, *, token: str | None, root: str | None) -> list[Finding]:
    """Findings in every layer of a ``docker save`` archive."""
    try:
        with tarfile.open(path, mode="r:*") as image:
            manifest = json.load(image.extractfile("manifest.json") or _missing("manifest.json"))
            layers = [layer for entry in manifest for layer in entry.get("Layers", [])]
            if not layers:
                raise Unreadable("the image archive lists no layers")
            found: list[Finding] = []
            for layer in layers:
                blob = image.extractfile(layer) or _missing(layer)
                found.extend(scan_tar(blob, token=token, root=root))
            return found
    except (KeyError, ValueError, tarfile.TarError, OSError) as exc:
        raise Unreadable(f"image archive could not be read: {type(exc).__name__}") from exc


def _missing(name: str):
    raise Unreadable(f"{name} is missing from the image archive")


def _docker(args: list[str]) -> None:
    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    if done.returncode != 0:
        # The build's own log can echo context paths but not file contents; keep only its tail.
        raise Unreadable(f"docker {args[0]} exited {done.returncode}: {done.stderr.strip()[-400:]}")


def scan_context(
    context: Path, dockerfile: Path, *, token: str | None, workdir: Path | None = None
) -> tuple[list[str], list[Finding]]:
    """The effective build context's file names, and findings in it.

    The probe Dockerfile gets the real Dockerfile's specific ignore file, if any, so the same rules apply.
    """
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmpdir = Path(tmp)
        probe = tmpdir / "Dockerfile.probe"
        probe.write_text(_PROBE)
        specific = dockerfile.with_name(dockerfile.name + ".dockerignore")
        if specific.exists():
            shutil.copyfile(specific, tmpdir / "Dockerfile.probe.dockerignore")
        out = tmpdir / "context.tar"
        _docker(["build", "--quiet", "-f", str(probe), "--output", f"type=tar,dest={out}", str(context)])
        with out.open("rb") as f:
            found = scan_tar(f, token=token, root=None)
        with tarfile.open(out) as tar:
            names = [_clean(m.name) for m in tar if m.isfile()]
    return names, found


def scan_image(image: str, *, token: str | None, root: str | None, workdir: Path | None = None) -> list[Finding]:
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        out = Path(tmp) / "image.tar"
        _docker(["save", "-o", str(out), image])
        return scan_saved_image(out, token=token, root=root)


def report(found: list[Finding], *, label: str, summary: str | None = None) -> None:
    for f in found:
        print(f"::error::{label}: {f.path} ({f.rule})")
    if not found:
        print(f"{label}: no denied paths or sentinels found")
    if summary:
        rows = "\n".join(f"| `{f.path}` | {f.rule} |" for f in found) or "| - | none found |"
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"\n### Image contents: {label}\n\n| Path | Rule |\n| --- | --- |\n{rows}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    ctx = sub.add_parser("context", help="the effective build context of a Dockerfile")
    ctx.add_argument("path", type=Path)
    ctx.add_argument("--dockerfile", type=Path, required=True)
    img = sub.add_parser("image", help="every layer of a built image")
    img.add_argument("image")
    arc = sub.add_parser("tar", help="a tar archive on disk")
    arc.add_argument("path", type=Path)
    pl = sub.add_parser("plant", help="write synthetic sentinels into a disposable context")
    pl.add_argument("path", type=Path)
    pl.add_argument("--kind", choices=sorted(SENTINELS), required=True)
    pl.add_argument("--token", required=True)
    for p in (img, arc):
        p.add_argument("--root", default="", help="only judge files under this directory (e.g. app)")
    for p in (ctx, img, arc):
        p.add_argument("--token", help="a synthetic sentinel string to look for")
        p.add_argument("--summary", help="append a markdown table here (the job summary)")
    args = parser.parse_args(argv)

    if args.mode == "plant":
        print(f"planted {len(plant(args.path, args.kind, args.token))} sentinels in {args.path}")
        return 0
    label = f"{args.mode} {getattr(args, 'image', None) or args.path}"
    try:
        if args.mode == "context":
            _, found = scan_context(args.path, args.dockerfile, token=args.token)
        elif args.mode == "image":
            found = scan_image(args.image, token=args.token, root=args.root.strip("/"))
        else:
            with args.path.open("rb") as f:
                found = scan_tar(f, token=args.token, root=args.root.strip("/"))
    except (Unreadable, OSError) as exc:
        print(f"::error::{label}: nothing was verified ({exc})")
        return 2
    report(found, label=label, summary=args.summary)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
