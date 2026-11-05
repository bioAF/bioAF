"""Per-phase test timing, recorded as JSON lines for runtime comparisons.

Enable with ``--phase-report=PATH`` (or ``BIOAF_PHASE_REPORT=PATH``). Every
setup/call/teardown report is written as one line when it arrives, so a run that
is interrupted keeps what it gathered. Under xdist the workers forward their
reports to the controller, so only the controller writes; a serial run writes
from the one process it has.

The first line describes the run (versions, commit, workers, database and its
durability settings, effective bcrypt rounds); the last line, when the run ends
normally, carries the exit status and wall time.

``python -m tests.plugins.phase_report SUMMARY.jsonl [...]`` prints phase sums,
outcome counts and wall time for one or more reports.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import uuid
from collections import Counter
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import IO, Any

import pytest

from tests.plugins.db_url import database_url, describe

PACKAGES = ("pytest", "pytest-xdist", "pytest-asyncio", "pytest-cov", "sqlalchemy", "asyncpg", "alembic", "bcrypt")
DURABILITY_SETTINGS = ("fsync", "synchronous_commit", "full_page_writes")

_OPEN: dict[str, dict] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--phase-report",
        default=os.environ.get("BIOAF_PHASE_REPORT") or None,
        help="write setup/call/teardown timing for every test to this JSON-lines file",
    )


def _is_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, cwd=Path(__file__).parent
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _database_settings(url: str) -> dict[str, Any]:
    """Server version and durability settings, read from a fresh connection."""
    info: dict[str, Any] = {"target": describe(url)}
    try:
        import asyncio

        import asyncpg

        async def _read() -> dict[str, Any]:
            conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"), timeout=5)
            try:
                found = {"server_version": await conn.fetchval("SHOW server_version")}
                for name in DURABILITY_SETTINGS:
                    found[name] = await conn.fetchval(f"SHOW {name}")
                return found
            finally:
                await conn.close()

        info.update(asyncio.run(_read()))
    except Exception as exc:  # evidence gathering must never fail the run
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _run_metadata(config: pytest.Config) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": _package_versions(),
        "commit": _commit(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": _memory_bytes(),
        "workers": getattr(config.option, "numprocesses", None),
        "dist": getattr(config.option, "dist", None),
        "args": list(config.invocation_params.args),
        "cwd": str(config.invocation_params.dir),
        "cov_source": getattr(config.option, "cov_source", None),
        "bcrypt_rounds": os.environ.get("BIOAF_BCRYPT_ROUNDS"),
        "test_bcrypt_rounds": os.environ.get("BIOAF_TEST_BCRYPT_ROUNDS"),
        "invocation_id": os.environ.get("BIOAF_TEST_INVOCATION_ID"),
        "database": _database_settings(database_url()),
    }


def _write(stream: IO[str], record: dict[str, Any]) -> None:
    stream.write(json.dumps(record, default=str) + "\n")
    stream.flush()


def pytest_configure(config: pytest.Config) -> None:
    path = config.getoption("--phase-report")
    if not path or _is_worker(config):
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stream = target.open("a", encoding="utf-8")
    state = {"stream": stream, "run_id": uuid.uuid4().hex, "started": time.monotonic()}
    _OPEN["state"] = state
    _write(
        stream,
        {"type": "start", "run_id": state["run_id"], "at": time.time(), "metadata": _run_metadata(config)},
    )


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # A report does not carry its config; the one open report is module state.
    state = _OPEN.get("state")
    if state is None:
        return
    node = getattr(report, "node", None)  # set by xdist on reports forwarded from a worker
    gateway = getattr(node, "gateway", None)
    _write(
        state["stream"],
        {
            "type": "phase",
            "run_id": state["run_id"],
            "nodeid": report.nodeid,
            "when": report.when,
            "duration": report.duration,
            "outcome": report.outcome,
            "worker": getattr(gateway, "id", None) or "master",
        },
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = _OPEN.get("state")
    if state is None:
        return
    _write(
        state["stream"],
        {
            "type": "end",
            "run_id": state["run_id"],
            "exitstatus": int(exitstatus),
            "wall_seconds": time.monotonic() - state["started"],
        },
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    state = _OPEN.pop("state", None)
    if state is not None:
        state["stream"].close()


def summarize(path: Path) -> dict[str, Any]:
    """Phase sums, per-test outcomes and wall time for one report file."""
    records = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    phases = [r for r in records if r["type"] == "phase"]
    sums: Counter[str] = Counter()
    outcome_by_test: dict[str, str] = {}
    for r in phases:
        sums[r["when"]] += r["duration"]
        # A test's outcome is its worst phase: a failed teardown fails a passed call.
        previous = outcome_by_test.get(r["nodeid"])
        if r["when"] == "call" or previous is None or r["outcome"] != "passed":
            if not (previous in ("failed", "skipped") and r["outcome"] == "passed"):
                outcome_by_test[r["nodeid"]] = r["outcome"]
    end = next((r for r in reversed(records) if r["type"] == "end"), None)
    start = next((r for r in records if r["type"] == "start"), None)
    return {
        "tests": len(outcome_by_test),
        "outcomes": dict(Counter(outcome_by_test.values())),
        "phase_seconds": {k: round(v, 3) for k, v in sums.items()},
        "wall_seconds": round(end["wall_seconds"], 3) if end else None,
        "exitstatus": end["exitstatus"] if end else None,
        "metadata": start["metadata"] if start else None,
    }


def main(argv: list[str]) -> int:
    for arg in argv:
        summary = summarize(Path(arg))
        meta = summary.pop("metadata") or {}
        print(f"== {arg}")
        print(f"   commit={meta.get('commit')} workers={meta.get('workers')} bcrypt_rounds={meta.get('bcrypt_rounds')}")
        print(f"   database={json.dumps(meta.get('database'))}")
        for key, value in summary.items():
            print(f"   {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
