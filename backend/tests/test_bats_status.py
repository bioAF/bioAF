"""The Shell Tests gate (scripts/ci/bats_status.py).

A failed test, a skip other than the network guard's, and a run that executed no tests all fail. Bats exits
0 when it finds no tests, so without the last rule an emptied or moved tests/shell would pass the merge gate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "bats_status.py"

PASSING = "1..2\nok 1 bioaf prints usage\nok 2 installer checks prerequisites\n"
NETWORK_SKIP = "ok 3 pulls a published tag # skip ghcr.io unreachable\n"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("bats_status", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(gate, tmp_path: Path, tap: str) -> tuple[int, str]:
    output = tmp_path / "bats-output.tap"
    output.write_text(tap)
    summary = tmp_path / "summary.md"
    status = gate.main([str(output), "--summary", str(summary)])
    return status, summary.read_text()


def test_passing_tests_pass(gate, tmp_path):
    status, summary = _run(gate, tmp_path, PASSING)
    assert status == 0
    assert "- total: 2" in summary
    assert "- executed: 2" in summary


def test_a_failed_test_fails(gate, tmp_path):
    status, summary = _run(gate, tmp_path, PASSING + "not ok 3 update agent rolls back\n")
    assert status == 1
    assert "- failed: 1" in summary


def test_the_network_skip_is_accepted(gate, tmp_path):
    status, summary = _run(gate, tmp_path, PASSING + NETWORK_SKIP)
    assert status == 0
    assert "ghcr.io unreachable" in summary


def test_any_other_skip_fails(gate, tmp_path):
    status, summary = _run(gate, tmp_path, PASSING + "ok 3 encrypts the backup # skip openssl missing\n")
    assert status == 1
    assert "openssl missing" in summary


@pytest.mark.parametrize("tap", ["1..0\n", ""], ids=["no-tests", "no-output"])
def test_no_tests_fails(gate, tmp_path, tap):
    status, summary = _run(gate, tmp_path, tap)
    assert status == 1
    assert "- total: 0" in summary


def test_only_skipped_tests_fails(gate, tmp_path):
    status, summary = _run(gate, tmp_path, "1..1\n" + NETWORK_SKIP)
    assert status == 1
    assert "- executed: 0" in summary


def test_a_missing_output_file_fails(gate, tmp_path):
    assert gate.main([str(tmp_path / "absent.tap")]) == 1
