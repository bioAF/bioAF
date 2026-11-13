"""An abandoned direct-sink flush cannot hold up process exit.

The lifespan gives the direct sink ``LOG_SINK_SECONDS`` to flush. Returning from that wait is not
enough: Python's ``logging.shutdown`` (run at interpreter exit) flushes and closes every live handler
again. These tests run a real interpreter to its normal exit with a handler whose flush and close
block, so they prove the exit itself, not just the coroutine's elapsed time.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

PROBE = textwrap.dedent(
    """
    import asyncio, logging, sys, threading, time
    from unittest.mock import patch

    from app import logging_config, startup

    release = threading.Event()

    class Blocking(logging.Handler):
        def emit(self, record):
            pass

        def flush(self):
            release.wait()

        def close(self):
            release.wait()
            super().close()

    class Provider:
        def build_handler(self, debug=False):
            return Blocking()

    logging.getLogger("bioaf").setLevel(logging.INFO)
    with patch("app.adapters.observability.create_log_sink_provider", lambda *a, **k: Provider()):
        cleanup = logging_config.attach_cloud_logging("probe-project", None)
    startup.LOG_SINK_SECONDS = 0.05
    asyncio.run(startup._close_log_sink(cleanup))
    print("budget returned", flush=True)
    if sys.argv[1] == "late":
        # The abandoned flush finishes after the event loop has closed.
        release.set()
        time.sleep(0.5)
    """
)


@pytest.mark.parametrize("scenario", ["blocked", "late"])
def test_the_process_exits_after_an_abandoned_flush(scenario):
    try:
        result = subprocess.run(
            [sys.executable, "-c", PROBE, scenario],
            cwd=BACKEND,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the interpreter did not exit: logging shutdown is still waiting on the abandoned sink")
    assert "budget returned" in result.stdout
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr and "Exception in thread" not in result.stderr, result.stderr
