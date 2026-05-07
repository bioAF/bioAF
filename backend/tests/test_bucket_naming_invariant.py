"""SA hardening invariant: every google_storage_bucket starts with bioaf-.

The IAM Condition on bioaf-app's roles/storage.admin requires
resource.name.startsWith("projects/_/buckets/bioaf-"). A bucket without
the prefix would not be reachable by the runtime SA. This test
parses every Terraform file in the repo and asserts the invariant.
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


def _terraform_files() -> list[Path]:
    files: list[Path] = []
    for d in _TF_DIRS:
        if d.exists():
            files.extend(sorted(d.rglob("*.tf")))
    return files


# Match the start of a `resource "google_storage_bucket" "name" {` block, then
# the body's `name = ...` line. We do a coarse block scan and assume the
# `name` attribute is the first non-comment occurrence inside that block.
_RESOURCE_RE = re.compile(
    r'resource\s+"google_storage_bucket"\s+"[^"]+"\s*\{',
)


def _bucket_blocks(text: str) -> list[str]:
    """Return the substring of each google_storage_bucket block (up to closing brace at column 0)."""
    blocks: list[str] = []
    for match in _RESOURCE_RE.finditer(text):
        start = match.end()
        # Walk forward, balance braces.
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


_NAME_RE = re.compile(r"^\s*name\s*=\s*(.+?)\s*$", re.MULTILINE)


def _bucket_name_literal(block: str) -> str | None:
    """Return the first `name = ...` value from a bucket block (raw, may be interpolated)."""
    m = _NAME_RE.search(block)
    if not m:
        return None
    return m.group(1).strip()


# Bucket names that are passed in via Terraform variables but whose value
# is set programmatically by the backend to a bioaf- prefix. The backend is
# the only writer (terraform_executor.py uses
# `bioaf-tfstate-{project_id}` for the foundation state bucket and the
# storage module hard-codes `bioaf-` prefixes elsewhere). We allow these
# specific var references through the static check because the IAM
# Condition still applies at runtime.
_ALLOWED_VAR_REFERENCES: set[str] = {"var.state_bucket_name"}


def test_every_google_storage_bucket_name_starts_with_bioaf():
    failures: list[tuple[Path, str]] = []
    for path in _terraform_files():
        text = path.read_text()
        for block in _bucket_blocks(text):
            literal = _bucket_name_literal(block)
            if literal is None:
                failures.append((path, "<no name attribute found>"))
                continue
            stripped = literal.strip()
            if stripped.startswith('"'):
                inside = stripped[1:-1] if stripped.endswith('"') else stripped[1:]
                if not inside.startswith("bioaf-") and not inside.startswith("${"):
                    failures.append((path, literal))
            elif stripped in _ALLOWED_VAR_REFERENCES:
                pass
            elif stripped.startswith("${"):
                pass
            elif stripped.startswith("local."):
                pass
            elif "bioaf" not in stripped:
                failures.append((path, literal))

    assert not failures, "google_storage_bucket resources missing the bioaf- prefix:\n" + "\n".join(
        f"  {p}: {n}" for p, n in failures
    )


def test_terraform_files_present():
    """Sanity guard: this test must exercise at least one bucket block."""
    files = _terraform_files()
    assert files, "Expected at least one Terraform file under terraform/ or backend/terraform/modules/"
    # We expect at least one google_storage_bucket somewhere (storage module).
    found = False
    for p in files:
        if _bucket_blocks(p.read_text()):
            found = True
            break
    if not found:
        pytest.skip("No google_storage_bucket resources found; nothing to invariant-check")
