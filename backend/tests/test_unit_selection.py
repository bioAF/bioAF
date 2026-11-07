"""The reviewed unit selection runs without PostgreSQL and keeps every global guard (plan 8, step 2).

The selection is run the documented way, in a subprocess, with the database URL pointed at a local
listener that counts connections. A fixture, plugin or reporting hook that opens a database
connection shows up as a connection, even when it swallows the failure.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

from tests.support import unit_selection

BACKEND_DIR = Path(__file__).resolve().parent.parent


class _CountingListener:
    """A TCP port that accepts and immediately closes connections, counting them."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        self.sock.close()


def _run_selection(*args: str, port: int, tmp_path: Path) -> subprocess.CompletedProcess:
    url = f"postgresql+asyncpg://nobody:nothing@127.0.0.1:{port}/no_database"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_"))}
    env.update(
        BIOAF_TEST_DATABASE_URL=url,
        BIOAF_DATABASE_URL=url,
        BIOAF_TEST_MANIFEST_DIR=str(tmp_path / "manifests"),
        COVERAGE_FILE=str(tmp_path / ".coverage"),  # never in backend/, where the enclosing run combines
    )
    env.pop("BIOAF_TEST_INVOCATION_ID", None)  # the outer run's id; the selection is its own invocation
    return subprocess.run(
        [sys.executable, "-m", "tests.support.unit_selection", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_the_selection_names_existing_reviewed_files():
    assert unit_selection.UNIT_TEST_FILES, "the unit selection is empty"
    for path in unit_selection.UNIT_TEST_FILES:
        assert (BACKEND_DIR / path).is_file(), f"unit selection lists a missing file: {path}"
    assert len(set(unit_selection.UNIT_TEST_FILES)) == len(unit_selection.UNIT_TEST_FILES)


def test_the_selection_passes_without_opening_a_database_connection(tmp_path):
    listener = _CountingListener()
    try:
        result = _run_selection("-q", "-p", "no:cacheprovider", "-n", "0", port=listener.port, tmp_path=tmp_path)
    finally:
        listener.close()
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-4000:]
    assert re.search(r"\b\d+ passed\b", output), output[-2000:]
    assert " failed" not in output and " error" not in output, output[-2000:]
    assert listener.connections == 0, f"the unit selection opened {listener.connections} database connection(s)"


def test_the_selection_with_coverage_and_workers_still_opens_no_connection(tmp_path):
    """The measured command (coverage, xdist) adds plugins; none of them may reach the database."""
    listener = _CountingListener()
    report = tmp_path / "unit-coverage.json"
    before = set(BACKEND_DIR.glob(".coverage*"))
    try:
        result = _run_selection(
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            "--cov=app",
            f"--cov-report=json:{report}",
            port=listener.port,
            tmp_path=tmp_path,
        )
    finally:
        listener.close()
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    assert report.is_file()
    # Data files left in backend/ are combined into the enclosing suite's measurement, and statement
    # data cannot combine with branch data: the outer run fails after every test passed.
    assert set(BACKEND_DIR.glob(".coverage*")) == before, "the unit run left coverage data in backend/"
    assert listener.connections == 0, f"the measured unit command opened {listener.connections} connection(s)"


def test_the_global_guards_still_apply_to_the_selection(tmp_path):
    """Network refusal and state restoration are not a property of the database; units keep them."""
    listener = _CountingListener()
    try:
        result = _run_selection(
            "-q", "-p", "no:cacheprovider", "-n", "0", "--setup-plan", port=listener.port, tmp_path=tmp_path
        )
    finally:
        listener.close()
    output = result.stdout
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    for guard in ("_isolated_state", "url_network", "_no_accession_manifest_network"):
        assert re.search(rf"SETUP\s+F {guard}\b", output), f"{guard} is not set up for the unit selection"
    assert "db_engine" not in output, "a unit test depends on the database fixture"
