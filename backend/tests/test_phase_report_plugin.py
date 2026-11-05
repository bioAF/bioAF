"""The phase report records setup/call/teardown timing for every test, serially and under xdist.

The report is the evidence a runtime comparison rests on, so these tests run a
real inner pytest in a subprocess and read the file it leaves behind.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

BACKEND_DIR = Path(__file__).resolve().parent.parent

INNER_TESTS = """
import time
import pytest

@pytest.fixture
def slow_setup():
    time.sleep(0.05)
    yield
    time.sleep(0.05)

def test_passes(slow_setup):
    pass

def test_fails():
    assert False

@pytest.mark.skip(reason="not today")
def test_skipped():
    pass
"""


@pytest.fixture(autouse=True)
def _plugin_importable(monkeypatch):
    """The inner run imports the plugin from this repository's backend package."""
    monkeypatch.setenv("PYTHONPATH", str(BACKEND_DIR))


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run(pytester: pytest.Pytester, *args: str) -> tuple[pytest.RunResult, list[dict]]:
    pytester.makepyfile(test_inner=INNER_TESTS)
    report = pytester.path / "phases.jsonl"
    result = pytester.runpytest_subprocess(
        "-p", "tests.plugins.phase_report", f"--phase-report={report}", "-p", "no:cacheprovider", *args
    )
    return result, _records(report)


def _phases(records: list[dict]) -> list[dict]:
    return [r for r in records if r["type"] == "phase"]


def test_serial_run_records_every_phase_with_outcome_and_duration(pytester):
    result, records = _run(pytester, "-n", "0")
    result.assert_outcomes(passed=1, failed=1, skipped=1)

    phases = _phases(records)
    by_test = {(r["nodeid"].split("::")[-1], r["when"]): r for r in phases}
    assert by_test[("test_passes", "setup")]["duration"] >= 0.05
    assert by_test[("test_passes", "teardown")]["duration"] >= 0.05
    assert by_test[("test_passes", "call")]["outcome"] == "passed"
    assert by_test[("test_fails", "call")]["outcome"] == "failed"
    # A skip is reported at setup; it must stay visible rather than vanish.
    assert by_test[("test_skipped", "setup")]["outcome"] == "skipped"
    assert {r["worker"] for r in phases} == {"master"}

    run_ids = {r["run_id"] for r in records}
    assert len(run_ids) == 1


def test_xdist_run_records_each_report_once_with_its_worker(pytester):
    result, records = _run(pytester, "-n", "2")
    result.assert_outcomes(passed=1, failed=1, skipped=1)

    phases = _phases(records)
    keys = [(r["nodeid"], r["when"]) for r in phases]
    assert len(keys) == len(set(keys)), "a report was recorded twice"
    assert ("test_inner.py::test_passes", "setup") in keys
    assert {r["worker"] for r in phases} <= {"gw0", "gw1"}


def test_report_carries_run_metadata_and_a_closing_summary(pytester):
    _result, records = _run(pytester, "-n", "0")

    start = records[0]
    assert start["type"] == "start"
    meta = start["metadata"]
    for key in ("python", "packages", "commit", "cpu_count", "workers", "args", "bcrypt_rounds"):
        assert key in meta, key
    assert meta["packages"]["pytest"]

    end = records[-1]
    assert end["type"] == "end"
    assert end["exitstatus"] == 1
    assert end["wall_seconds"] > 0


def test_reports_are_written_as_they_happen(pytester):
    """An interrupted run keeps the evidence gathered before the interruption."""
    pytester.makepyfile(
        test_inner="""
import os, signal

def test_first():
    pass

def test_dies():
    os.kill(os.getpid(), signal.SIGKILL)
"""
    )
    report = pytester.path / "phases.jsonl"
    pytester.runpytest_subprocess("-p", "tests.plugins.phase_report", f"--phase-report={report}", "-n", "0")

    phases = _phases(_records(report))
    assert ("test_inner.py::test_first", "call") in {(r["nodeid"], r["when"]) for r in phases}


def test_no_report_is_written_without_the_option(pytester):
    pytester.makepyfile(test_inner=INNER_TESTS)
    pytester.runpytest_subprocess("-p", "tests.plugins.phase_report", "-n", "0")
    assert not list(pytester.path.glob("*.jsonl"))


def test_summary_separates_phase_sums_from_wall_time(pytester, tmp_path):
    from tests.plugins.phase_report import summarize

    _result, records = _run(pytester, "-n", "0")
    path = tmp_path / "r.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records))

    summary = summarize(path)
    assert summary["tests"] == 3
    assert summary["outcomes"] == {"passed": 1, "failed": 1, "skipped": 1}
    assert summary["phase_seconds"]["setup"] >= 0.05
    assert summary["phase_seconds"]["teardown"] >= 0.05
    assert summary["wall_seconds"] > 0
