"""Opt-in seeded shuffling of collection order, recorded so an order-dependent failure can be replayed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

BACKEND_DIR = Path(__file__).resolve().parent.parent

INNER = "\n".join(f"def test_{i:02d}():\n    pass\n" for i in range(30))


@pytest.fixture(autouse=True)
def _plugin_importable(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(BACKEND_DIR))
    monkeypatch.delenv("BIOAF_TEST_SHUFFLE_SEED", raising=False)


def _run(pytester, *args) -> tuple[pytest.RunResult, dict | None]:
    pytester.makepyfile(test_inner=INNER)
    record = pytester.path / "order.json"
    result = pytester.runpytest_subprocess(
        "-p", "tests.plugins.shuffle", f"--shuffle-record={record}", "-p", "no:cacheprovider", "-v", *args
    )
    return result, (json.loads(record.read_text()) if record.exists() else None)


def _executed(result: pytest.RunResult) -> list[str]:
    return [line.split(" ")[0].split("::")[-1] for line in result.outlines if "::test_" in line and "PASSED" in line]


def test_without_a_seed_the_order_is_unchanged(pytester):
    result, record = _run(pytester, "-n", "0")
    assert _executed(result) == [f"test_{i:02d}" for i in range(30)]
    assert record is None


def test_a_seed_shuffles_reproducibly_and_records_the_order(pytester):
    first, record = _run(pytester, "-n", "0", "--shuffle-seed=1234")
    again, record_again = _run(pytester, "-n", "0", "--shuffle-seed=1234")
    other, _ = _run(pytester, "-n", "0", "--shuffle-seed=99")

    assert _executed(first) != [f"test_{i:02d}" for i in range(30)]
    assert _executed(first) == _executed(again)
    assert _executed(first) != _executed(other)
    assert record is not None and record_again is not None
    assert record["seed"] == 1234
    assert [n.split("::")[-1] for n in record["collected"]] == _executed(first)


def test_random_picks_a_seed_and_records_it(pytester):
    result, record = _run(pytester, "-n", "0", "--shuffle-seed=random")
    assert record is not None
    replay, _ = _run(pytester, "-n", "0", f"--shuffle-seed={record['seed']}")
    assert _executed(result) == _executed(replay)


def test_every_xdist_worker_collects_the_same_shuffled_order(pytester):
    result, record = _run(pytester, "-n", "3", "--shuffle-seed=random")
    result.assert_outcomes(passed=30)
    assert record is not None
    assert record["workers"] == 3
    assert record["dist"]
    assert len(record["collected"]) == 30
