"""Ordered startup and its unwinding.

The real lifespan, on this test's database. Startup runs its steps in the existing order with the
existing policies (fatal steps stop startup, optional ones log and continue), and whatever it had
installed is undone on a partial failure, on normal exit, on an exception through the lifespan,
and on cancellation: workers stopped, this lifespan's event subscriptions removed (others kept),
and its task owner shut down. Repeated lifespans do not accumulate subscribers.
"""

import asyncio
import logging

import pytest
from fastapi import FastAPI

import app.main as main_module
from app import schema_upgrade
from app.services.event_bus import event_bus
from app.services.event_types import ENVIRONMENT_BUILD_COMPLETED, PIPELINE_COMPLETED

pytestmark = pytest.mark.asyncio

OPEN = {"compatible": True, "reason": None, "revision": "x", "target": "x"}


def _subscriber_count() -> int:
    return sum(len(v) for v in event_bus._subscribers.values())


@pytest.fixture
def startup(db_engine, monkeypatch):
    """The real lifespan on this test's engine, with an open gate, and a call recorder."""
    from app.config import settings

    monkeypatch.setattr(settings, "jwt_secret_key", "startup-sequence-test-secret-0123456789abcdef")

    async def _open(_engine):
        return OPEN

    monkeypatch.setattr(schema_upgrade, "compatibility", _open)
    monkeypatch.setattr(main_module, "engine", db_engine)
    calls: list[str] = []

    def record(target: str, name: str):
        module_path, attr = target.rsplit(".", 1)
        import importlib

        owner = importlib.import_module(module_path)
        real = getattr(owner, attr)

        async def wrapper(*args, **kwargs):
            calls.append(name)
            return await real(*args, **kwargs)

        monkeypatch.setattr(owner, attr, wrapper)

    for target, name in [
        ("app.services.bootstrap_metadata.persist_cloud_provider", "bootstrap_metadata"),
        ("app.services.email_service.load_persisted_smtp_settings", "smtp_settings"),
        ("app.adapters.registry.initialize_adapters", "adapters"),
        ("app.services.environment_service.ensure_default_work_node_environment", "work_node_environment"),
        ("app.services.bootstrap_environments.seed_builtin_environments", "builtin_environments"),
        ("app.services.environment_service.ensure_default_pipeline_environment", "pipeline_environment"),
        ("app.services.bootstrap_bamtofastq.ensure_bamtofastq_pipeline", "bamtofastq"),
        ("app.services.environment_service.ensure_default_notebook_environment", "notebook_environment"),
        ("app.services.agent_review_job_service.mark_orphaned_on_startup", "hosted_review_orphans"),
        ("app.services.lab_glossary_scan_service.mark_orphaned_on_startup", "glossary_orphans"),
    ]:
        record(target, name)

    from app.services.notification_router import NotificationRouter
    from app.services.upgrade_service import UpgradeService

    real_register = NotificationRouter.register

    def register(self):
        calls.append("notifications")
        real_register(self)

    monkeypatch.setattr(NotificationRouter, "register", register)
    real_resolve = UpgradeService.resolve_pending_upgrades

    async def resolve(session):
        calls.append("pending_upgrades")
        return await real_resolve(session)

    monkeypatch.setattr(UpgradeService, "resolve_pending_upgrades", staticmethod(resolve))
    return calls


async def test_startup_runs_its_steps_in_order_and_starts_owned_workers(startup):
    application = FastAPI()
    before = _subscriber_count()
    async with main_module.lifespan(application):
        owner = application.state.runtime
        assert startup == [
            "bootstrap_metadata",
            "smtp_settings",
            "notifications",
            "adapters",
            "work_node_environment",
            "builtin_environments",
            "pipeline_environment",
            "bamtofastq",
            "notebook_environment",
            "pending_upgrades",
            "hosted_review_orphans",
            "glossary_orphans",
        ]
        roles = owner.roles()
        assert len(roles) == 29
        assert {"pipeline-monitor", "pubsub-listener", "webhook-worker", "postgres-backup"} <= set(roles)
        assert _subscriber_count() > before
        workers = owner.tasks()
    assert all(t.done() for t in workers)
    assert owner.roles() == []
    assert _subscriber_count() == before


async def test_an_optional_step_failure_is_logged_and_startup_continues(startup, monkeypatch, caplog):
    async def broken(session):
        raise RuntimeError("seed exploded")

    monkeypatch.setattr("app.services.bootstrap_bamtofastq.ensure_bamtofastq_pipeline", broken)
    application = FastAPI()
    with caplog.at_level(logging.WARNING, logger="bioaf"):
        async with main_module.lifespan(application):
            assert "notebook_environment" in startup
    assert "10x bamtofastq seed failed: seed exploded" in caplog.text


async def test_a_fatal_step_failure_unwinds_what_startup_installed(startup, monkeypatch):
    async def broken(session, session_factory=None):
        raise RuntimeError("adapters down")

    monkeypatch.setattr("app.adapters.registry.initialize_adapters", broken)
    before = _subscriber_count()
    tasks_before = set(asyncio.all_tasks())
    application = FastAPI()
    with pytest.raises(RuntimeError, match="adapters down"):
        async with main_module.lifespan(application):
            pytest.fail("startup should not have completed")
    assert _subscriber_count() == before
    await asyncio.sleep(0)
    assert [t for t in set(asyncio.all_tasks()) - tasks_before if not t.done()] == []
    assert application.state.runtime.roles() == []


async def test_an_exception_through_the_lifespan_still_stops_everything(startup):
    before = _subscriber_count()
    application = FastAPI()
    with pytest.raises(LookupError):
        async with main_module.lifespan(application):
            workers = application.state.runtime.tasks()
            raise LookupError("request handling blew up")
    assert all(t.done() for t in workers)
    assert _subscriber_count() == before


async def test_cancelling_startup_unwinds_it(startup, monkeypatch):
    blocked = asyncio.Event()

    async def hang(session):
        blocked.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.services.bootstrap_bamtofastq.ensure_bamtofastq_pipeline", hang)
    before = _subscriber_count()
    application = FastAPI()

    async def run():
        async with main_module.lifespan(application):
            pytest.fail("startup should not have completed")

    task = asyncio.create_task(run())
    await blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _subscriber_count() == before


async def test_repeated_lifespans_deliver_each_event_once(startup, monkeypatch):
    from app.services.custom_pipeline_service import CustomPipelineService

    delivered = []

    async def cascade(payload):
        delivered.append(payload)

    monkeypatch.setattr(CustomPipelineService, "handle_environment_build_completed", cascade)
    unrelated = []

    async def other(payload):
        unrelated.append(payload)

    event_bus.subscribe(PIPELINE_COMPLETED, other)

    for _ in range(2):
        async with main_module.lifespan(FastAPI()):
            pass
    async with main_module.lifespan(FastAPI()):
        await event_bus.emit(ENVIRONMENT_BUILD_COMPLETED, {"event_type": ENVIRONMENT_BUILD_COMPLETED})
        await event_bus.emit(PIPELINE_COMPLETED, {"event_type": PIPELINE_COMPLETED})

    assert len(delivered) == 1
    assert len(unrelated) == 1
    assert other in event_bus._subscribers[PIPELINE_COMPLETED]


async def test_source_pacing_state_does_not_survive_into_the_next_lifespan(startup):
    from app.services import request_pacing

    async with main_module.lifespan(FastAPI()):
        request_pacing.registry().pacer(("semanticscholar", "service")).cool_down(600)
    async with main_module.lifespan(FastAPI()):
        assert request_pacing.registry().pacer(("semanticscholar", "service")).cooldown_remaining() == 0
