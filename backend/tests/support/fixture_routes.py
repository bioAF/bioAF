"""Which database fixture route each test file takes (plan 4, stages 5 and 6).

- ``existing``: a fresh create_all schema per test (the default for every file not listed).
- ``migrated_reset``: the worker's migration-built schema, reset to the empty-upgrade baseline
  before the test. Real commits stay real.
- ``rollback``: the worker's migration-built schema inside one outer transaction that is rolled
  back after the test. Only for files the isolation inventory classifies ``single_connection``.

Files join a pilot only after they pass on it; the reasons a file stays off are recorded in
local/oct_refactor/4_test_runtime_and_fixtures/status.md. ``BIOAF_FIXTURE_ROUTES=existing``
forces every file onto the existing route, so one commit can be measured both ways.

``BIOAF_FIXTURE_TRIAL="rollback=single_connection;migrated_reset=real_commit"`` routes whole
isolation-inventory categories for a characterization run, without editing the pilot lists.
The rollback route only ever accepts ``single_connection`` files; ``schema`` and
``unclassified`` files are never routed.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_INVENTORY = Path(__file__).resolve().parent.parent / "isolation_inventory.json"
_ALLOWED = {"rollback": {"single_connection"}, "migrated_reset": {"single_connection", "real_commit"}}


@lru_cache(maxsize=1)
def _categories() -> dict[str, str]:
    return {name: e["category"] for name, e in json.loads(_INVENTORY.read_text())["files"].items()}


def _trial(filename: str) -> str | None:
    spec = os.environ.get("BIOAF_FIXTURE_TRIAL")
    if not spec:
        return None
    category = _categories().get(filename)
    for part in spec.split(";"):
        route, _, wanted = part.partition("=")
        wanted_set = set(wanted.split(","))
        if not wanted_set <= _ALLOWED.get(route, set()):
            raise ValueError(f"BIOAF_FIXTURE_TRIAL cannot route {sorted(wanted_set)} through {route}")
        if category in wanted_set:
            return route
    return None


MIGRATED_RESET_PILOT: frozenset[str] = frozenset()

ROLLBACK_PILOT: frozenset[str] = frozenset()


def route_for(filename: str) -> str:
    if os.environ.get("BIOAF_FIXTURE_ROUTES") == "existing":
        return "existing"
    trial = _trial(filename)
    if trial:
        return trial
    if filename in ROLLBACK_PILOT:
        return "rollback"
    if filename in MIGRATED_RESET_PILOT:
        return "migrated_reset"
    return "existing"
