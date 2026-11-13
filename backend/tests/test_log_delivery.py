"""One explicit centralized delivery path: ``BIOAF_LOG_DELIVERY=stdout`` (default) or ``direct``.

``stdout``: the application only writes sanitized JSON to stdout; the host agent collects it. No
direct handler, provider, credential load or database read happens for logging. ``direct``: the
retained provider is attached once by the startup step, stdout stays, and the lifespan closes the
handler after owned work has settled. An unavailable direct sink never fails startup and is reported
with its class, not its text. There is no automatic fallback from one path to the other.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from google.cloud.logging_v2.handlers import CloudLoggingHandler
from pydantic import ValidationError

import app.main as main_module
from app import schema_upgrade, startup
from app.config import Settings
from app.settings_contract import describe_settings_error
from tests.support import log_sinks
from tests.support.log_sinks import RecordingTransport


def _direct_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger("bioaf").handlers if isinstance(h, CloudLoggingHandler)]


# --- the setting ----------------------------------------------------------------------------


def test_delivery_defaults_to_stdout(monkeypatch):
    monkeypatch.delenv("BIOAF_LOG_DELIVERY", raising=False)
    assert Settings(compute_mode="local", _env_file=None).log_delivery == "stdout"  # type: ignore[call-arg]


@pytest.mark.parametrize(("raw", "expected"), [("direct", "direct"), (" DIRECT ", "direct"), ("Stdout", "stdout")])
def test_delivery_accepts_its_two_values_ignoring_case_and_whitespace(monkeypatch, raw, expected):
    monkeypatch.setenv("BIOAF_LOG_DELIVERY", raw)
    assert Settings(compute_mode="local", _env_file=None).log_delivery == expected  # type: ignore[call-arg]


@pytest.mark.parametrize("raw", ["agent", "", "direct,stdout", "secret-looking-value-77"])
def test_an_unknown_delivery_is_rejected_without_echoing_it(monkeypatch, raw):
    monkeypatch.setenv("BIOAF_LOG_DELIVERY", raw)
    with pytest.raises(ValidationError) as caught:
        Settings(compute_mode="local", _env_file=None)  # type: ignore[call-arg]
    message = describe_settings_error(caught.value)
    assert "BIOAF_LOG_DELIVERY" in message and "stdout" in message and "direct" in message
    if raw:
        assert raw not in message


# --- the startup step -----------------------------------------------------------------------


def _ctx() -> startup.StartupContext:
    return startup.StartupContext(engine=None, stack=AsyncExitStack(), subscriptions=AsyncExitStack())


@pytest.fixture
def offline(monkeypatch):
    log_sinks.reset()

    def forbidden(*args, **kwargs):
        raise AssertionError("network or metadata access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("google.cloud.logging_v2.handlers.handlers.detect_resource", forbidden)
    logging.getLogger("bioaf").setLevel(logging.INFO)


@pytest.fixture
def platform_config(monkeypatch):
    """The step's platform_config reads, without a database."""
    from app.adapters.credentials import credential_injector
    from app.platform.platform_config_service import PlatformConfigService

    reads: list[str] = []

    @asynccontextmanager
    async def session_factory():
        reads.append("session")
        yield object()

    async def get(session, key):
        return "offline-project" if key == "gcp_project_id" else None

    async def get_many(session, keys):
        reads.append("credentials")
        return {}

    monkeypatch.setattr("app.database.async_session_factory", session_factory)
    monkeypatch.setattr(PlatformConfigService, "get", staticmethod(get))
    monkeypatch.setattr(PlatformConfigService, "get_many", staticmethod(get_many))
    monkeypatch.setattr(credential_injector, "load_gcp_credentials", lambda cfg: None)
    return reads


@pytest.mark.asyncio
async def test_stdout_delivery_builds_nothing_and_reads_nothing(monkeypatch, offline, caplog):
    from app.config import settings

    monkeypatch.setattr(settings, "log_delivery", "stdout")

    def must_not_run(*args, **kwargs):
        raise AssertionError("stdout delivery must not touch the direct path")

    monkeypatch.setattr("app.database.async_session_factory", must_not_run)
    monkeypatch.setattr("app.adapters.observability.create_log_sink_provider", must_not_run)
    caplog.set_level(logging.INFO, logger="bioaf")
    ctx = _ctx()
    async with ctx.stack, ctx.subscriptions:
        await startup.cloud_logging(ctx)
        assert _direct_handlers() == []
    assert "Log delivery: stdout" in caplog.text


@pytest.mark.asyncio
async def test_direct_delivery_attaches_once_and_keeps_stdout(monkeypatch, offline, platform_config):
    from app.config import settings

    monkeypatch.setattr(settings, "log_delivery", "direct")
    handler = log_sinks.real_handler()
    ctx = _ctx()
    with patch("app.adapters.observability.gcp.cloud_logging.Client") as client_cls:
        client_cls.return_value.get_default_handler.return_value = handler
        async with ctx.stack, ctx.subscriptions:
            await startup.cloud_logging(ctx)
            await startup.cloud_logging(ctx)
            assert _direct_handlers() == [handler]
            stdout = [h for h in logging.getLogger("bioaf").handlers if not isinstance(h, CloudLoggingHandler)]
            assert stdout, "stdout stays an application sink in direct mode"
        await ctx.after_work.aclose()
    assert _direct_handlers() == []
    assert RecordingTransport.closed == 1
    assert client_cls.call_count == 1


@pytest.mark.asyncio
async def test_an_unavailable_direct_sink_is_reported_safely_and_startup_continues(
    monkeypatch, offline, platform_config, caplog
):
    from app.config import settings

    monkeypatch.setattr(settings, "log_delivery", "direct")
    caplog.set_level(logging.INFO, logger="bioaf")
    marker = "provider-error-marker-41"

    def broken(*args, **kwargs):
        raise PermissionError(f"denied for {marker}")

    monkeypatch.setattr("app.adapters.observability.create_log_sink_provider", broken)
    ctx = _ctx()
    async with ctx.stack, ctx.subscriptions:
        await startup.run_steps([s for s in startup.NORMAL_STEPS if s.name == "cloud_logging"], ctx)
        assert _direct_handlers() == []
    assert marker not in caplog.text
    assert "PermissionError" in caplog.text and "stdout only" in caplog.text


# --- the lifespan ---------------------------------------------------------------------------


@pytest.fixture
def lean_lifespan(monkeypatch):
    """The real lifespan with only the cloud_logging step and no workers or database."""
    open_gate = {"compatible": True, "reason": None, "revision": "x", "target": "x"}

    async def compatible(_engine):
        return open_gate

    class Engine:
        async def dispose(self):
            return None

    events: list[str] = []

    async def start_workers(owner, ctx):
        events.append("workers started")

    real_stop = main_module._stop_owned_work

    async def stop_owned_work(owner):
        events.append(f"stop owned work, direct attached={bool(_direct_handlers())}")
        await real_stop(owner)

    monkeypatch.setattr(schema_upgrade, "compatibility", compatible)
    monkeypatch.setattr(main_module, "engine", Engine())
    monkeypatch.setattr(main_module, "_start_workers", start_workers)
    monkeypatch.setattr(main_module, "_stop_owned_work", stop_owned_work)
    monkeypatch.setattr(startup, "PRE_GATE_STEPS", [])
    monkeypatch.setattr(startup, "NORMAL_STEPS", [s for s in startup.NORMAL_STEPS if s.name == "cloud_logging"])
    return events


@pytest.mark.asyncio
async def test_the_lifespan_closes_the_direct_sink_after_owned_work_settles(
    monkeypatch, offline, platform_config, lean_lifespan
):
    from app.config import settings

    monkeypatch.setattr(settings, "log_delivery", "direct")
    with patch("app.adapters.observability.gcp.cloud_logging.Client") as client_cls:
        client_cls.return_value.get_default_handler.side_effect = lambda: log_sinks.real_handler()
        for _ in range(2):
            async with main_module.lifespan(FastAPI()):
                assert len(_direct_handlers()) == 1
            assert _direct_handlers() == []
    assert lean_lifespan.count("stop owned work, direct attached=True") == 2
    assert RecordingTransport.closed == 2


@pytest.mark.asyncio
async def test_a_cancelled_lifespan_still_closes_the_direct_sink(monkeypatch, offline, platform_config, lean_lifespan):
    from app.config import settings

    monkeypatch.setattr(settings, "log_delivery", "direct")
    with patch("app.adapters.observability.gcp.cloud_logging.Client") as client_cls:
        client_cls.return_value.get_default_handler.side_effect = lambda: log_sinks.real_handler()

        async def run():
            async with main_module.lifespan(FastAPI()):
                await asyncio.sleep(3600)

        task = asyncio.create_task(run())
        while not _direct_handlers():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert _direct_handlers() == []
    assert RecordingTransport.closed == 1


@pytest.mark.asyncio
async def test_a_hanging_close_is_abandoned_within_its_budget(monkeypatch, offline, platform_config, lean_lifespan):
    from app.config import settings

    monkeypatch.setattr(settings, "log_delivery", "direct")
    monkeypatch.setattr(startup, "LOG_SINK_SECONDS", 0.2)
    release = SimpleNamespace(flag=False)

    def hang(self):
        while not release.flag:
            import time

            time.sleep(0.01)

    monkeypatch.setattr(RecordingTransport, "close", hang)
    loop = asyncio.get_running_loop()
    with patch("app.adapters.observability.gcp.cloud_logging.Client") as client_cls:
        client_cls.return_value.get_default_handler.side_effect = lambda: log_sinks.real_handler()
        started = loop.time()
        async with main_module.lifespan(FastAPI()):
            pass
        elapsed = loop.time() - started
    release.flag = True
    assert elapsed < 2.0
    assert _direct_handlers() == []


def test_the_log_sink_close_fits_the_container_stop_grace():
    """The direct sink's flush is one more bounded phase; the whole shutdown still leaves margin."""
    import re
    from pathlib import Path

    import yaml

    docker = Path(__file__).resolve().parents[2] / "docker"
    stop_grace = int(
        yaml.safe_load((docker / "docker-compose.yml").read_text())["services"]["backend"]["stop_grace_period"][:-1]
    )
    request_grace = int(
        re.search(r'"--timeout-graceful-shutdown", "(\d+)"', (docker / "Dockerfile.backend").read_text()).group(1)
    )
    total = (
        request_grace
        + startup.DRAIN_SECONDS
        + startup.CANCEL_SECONDS
        + startup.LOG_SINK_SECONDS
        + startup.RESOURCE_SECONDS
    )
    assert total <= stop_grace - 10
