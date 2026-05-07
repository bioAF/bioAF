"""SA hardening invariant: every Python-created compute VM name starts with bioaf-.

bioaf-app's roles/compute.instanceAdmin.v1 binding has the IAM Condition
resource.name.matches("^projects/<PROJECT>/zones/[^/]+/instances/bioaf-").
A non-prefixed VM created from the backend would be unmanageable.

This test scans the backend Python source for compute VM `name = "..."`
assignments inside compute_v1.Instance() construction and asserts the
literal starts with `bioaf-`. The known site is gce.py:_gce_launch_vm
(`bioaf-worknode-{session_id}`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

# Match `instance_name = "..."` or `instance_name = f"..."` -- the variable
# the GCE adapter builds before passing to instances_client.insert(). Matches
# both variable forms (instance_name) and direct attribute (instance.name).
_NAME_ASSIGN_RE = re.compile(
    r"""\b(?:instance\.name|instance_name)\s*=\s*(?P<value>f?["'][^"']*["'])""",
)


def _scan_python_files() -> list[Path]:
    return sorted(_BACKEND_APP.rglob("*.py"))


def _value_starts_with_bioaf(literal: str) -> bool:
    stripped = literal.lstrip("f").strip().strip("\"'")
    return stripped.startswith("bioaf-")


def test_every_compute_instance_name_assignment_starts_with_bioaf():
    failures: list[tuple[Path, str]] = []
    for path in _scan_python_files():
        text = path.read_text()
        if "instance_name" not in text and "instance.name" not in text:
            continue
        for m in _NAME_ASSIGN_RE.finditer(text):
            if not _value_starts_with_bioaf(m.group("value")):
                failures.append((path, m.group(0)))

    assert not failures, "Compute instance names must start with 'bioaf-':\n" + "\n".join(
        f"  {p}: {line}" for p, line in failures
    )


def test_invariant_actually_exercised():
    """Sanity guard: at least one instance name literal starts with bioaf-."""
    for path in _scan_python_files():
        text = path.read_text()
        for m in _NAME_ASSIGN_RE.finditer(text):
            if _value_starts_with_bioaf(m.group("value")):
                return
    pytest.fail(
        "No instance_name = 'bioaf-...' literal found in backend code -- "
        "did the GCE adapter rename its work-node format?"
    )
