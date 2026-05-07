"""SA hardening invariant: every google_container_cluster name literal starts
with bioaf-.

bioaf-app's roles/container.admin grant is conditioned on
resource.name.extract("/clusters/{name}").startsWith("bioaf-"). The original
plan called for a Resource Manager tag binding, but GKE clusters are
regional resources and google_tags_tag_binding (the global tag API) does
not accept them; google_tags_location_tag_binding has uneven support and
needs the project number rather than ID. Switching to the same name-prefix
pattern we already use for compute is simpler and works without any
Terraform-side wiring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TF_DIRS = [
    _REPO_ROOT / "terraform",
    _REPO_ROOT / "backend" / "terraform" / "modules",
]

# Match a google_container_cluster declaration; capture the resource label
# (used to find the `name = ...` attribute inside its body).
_CLUSTER_BLOCK_RE = re.compile(
    r'resource\s+"google_container_cluster"\s+"[^"]+"\s*\{',
)
_NAME_RE = re.compile(r"^\s*name\s*=\s*(.+?)\s*$", re.MULTILINE)


def _terraform_files() -> list[Path]:
    files: list[Path] = []
    for d in _TF_DIRS:
        if d.exists():
            files.extend(sorted(d.rglob("*.tf")))
    return files


def _cluster_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in _CLUSTER_BLOCK_RE.finditer(text):
        start = match.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        blocks.append(text[start:i])
    return blocks


def _cluster_name_literal(block: str) -> str | None:
    m = _NAME_RE.search(block)
    if not m:
        return None
    return m.group(1).strip()


def test_every_google_container_cluster_name_starts_with_bioaf():
    cluster_blocks: list[tuple[Path, str]] = []
    for path in _terraform_files():
        for block in _cluster_blocks(path.read_text()):
            cluster_blocks.append((path, block))

    if not cluster_blocks:
        pytest.skip("No google_container_cluster resources found in repo")

    failures: list[tuple[Path, str]] = []
    for path, block in cluster_blocks:
        literal = _cluster_name_literal(block)
        if literal is None:
            failures.append((path, "<no name attribute found>"))
            continue
        stripped = literal.strip()
        # Direct quoted literal must start with bioaf-.
        if stripped.startswith('"'):
            inside = stripped[1:-1] if stripped.endswith('"') else stripped[1:]
            if not inside.startswith("bioaf-"):
                failures.append((path, literal))
        # Interpolated expressions: require the literal text "bioaf-" in
        # the expression somewhere. This is a weak check but catches the
        # common case (e.g. "bioaf-${var.org_slug}-${var.stack_uid}").
        elif "bioaf-" not in stripped:
            failures.append((path, literal))

    assert not failures, "google_container_cluster resources whose name does not start with bioaf-:\n" + "\n".join(
        f"  {p}: {n}" for p, n in failures
    )
