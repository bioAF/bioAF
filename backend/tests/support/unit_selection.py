"""The reviewed unit selection (plan 8, step 2): decision tests that need no database, network or cloud.

Run it, from ``backend/``, with any extra pytest arguments::

    python -m tests.support.unit_selection
    python -m tests.support.unit_selection --cov=app --cov-branch --cov-report=json:unit-coverage.json

The selection is an explicit list of files, not a marker or a directory. A file joins only after a
review of its tests and their transitive fixtures: every test proves a decision from explicit inputs,
and none requests ``db_engine``/``session``/``client``. A database-free file is not a unit file by
that fact alone, and a rollback-eligible one is not either. Mixed or unreviewed files stay in the
combined suite only.

The global guards in ``tests/conftest.py`` (``_isolated_state``, ``url_network``,
``_no_accession_manifest_network``) and the adapter registry setup still run for these tests.
``tests/test_unit_selection.py`` proves the command passes without opening a database connection
and keeps those guards.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Paths relative to backend/. Reviewed 2026-11-07: the custom launcher's launchability and variable
# decisions, from SimpleNamespace inputs; no fixtures beyond the autouse guards.
UNIT_TEST_FILES: tuple[str, ...] = ("tests/test_custom_launch_decisions.py",)


def command(extra: list[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", *UNIT_TEST_FILES, *extra]


def main(argv: list[str] | None = None) -> int:
    return subprocess.call(command(list(sys.argv[1:] if argv is None else argv)), cwd=BACKEND_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
