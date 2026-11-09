"""Bounded shutdown through a real uvicorn process.

SIGTERM to uvicorn: requests drain, then the lifespan stops owned work (finishing work completes,
cancellable work is cancelled, uncooperative work is reported and abandoned) within the budgets,
and the process exits. Run in a subprocess; the pytest worker is never signalled.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    procs = []

    def start(drain=1.0, cancel=1.0):
        port = _free_port()
        env = {
            **os.environ,
            "SHUTDOWN_MARKERS": str(tmp_path),
            "SHUTDOWN_DRAIN": str(drain),
            "SHUTDOWN_CANCEL": str(cancel),
        }
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.support.shutdown_app:app",
                "--port",
                str(port),
                "--timeout-graceful-shutdown",
                "2",
            ],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(proc)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/ready", timeout=1).status_code == 200:
                    return proc, url
            except httpx.HTTPError:
                time.sleep(0.1)
        proc.kill()
        raise AssertionError(proc.communicate()[0])

    yield start
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _terminate(proc) -> tuple[float, str]:
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    output, _ = proc.communicate(timeout=30)
    return time.monotonic() - started, output


def test_sigterm_lets_finishing_work_complete_and_cancels_the_rest(server, tmp_path):
    proc, url = server(drain=2.0, cancel=1.0)
    httpx.post(f"{url}/jobs/finishing")
    httpx.post(f"{url}/jobs/cooperative")

    elapsed, output = _terminate(proc)

    # uvicorn re-raises the signal it handled once shutdown completes.
    assert proc.returncode in (0, -signal.SIGTERM), output
    assert "Application shutdown complete." in output
    assert (tmp_path / "finishing-done").exists()
    assert (tmp_path / "cooperative-cancelled").exists()
    assert (tmp_path / "lifespan-finished").read_text() == "clean"
    assert elapsed < 2.0 + 1.0 + 2.0, output


def test_uncooperative_work_is_reported_and_the_process_still_exits_within_budget(server, tmp_path):
    proc, url = server(drain=0.5, cancel=0.5)
    httpx.post(f"{url}/jobs/stubborn")

    elapsed, output = _terminate(proc)

    assert (tmp_path / "lifespan-finished").read_text() == "unclean"
    assert "role=stubborn transition=abandoned outcome=uncooperative" in output
    assert elapsed < 0.5 + 0.5 + 3.0, output


def test_a_cancelled_request_operation_finishes_its_cleanup_before_the_lifespan_reports(server, tmp_path):
    import threading

    proc, url = server(drain=1.0, cancel=2.0)
    threading.Thread(target=lambda: httpx.post(f"{url}/deploy", timeout=30), daemon=True).start()
    time.sleep(0.5)

    _, output = _terminate(proc)

    cleaned = tmp_path / "deploy-cleaned"
    finished = tmp_path / "lifespan-finished"
    assert cleaned.exists(), output
    assert finished.read_text() == "clean", output
    assert cleaned.stat().st_mtime_ns <= finished.stat().st_mtime_ns
