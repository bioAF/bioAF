"""How work finds its task owner (plan 14, delivery B3 and the first migrated cohort).

- Requests (the mounted integration app included) run with the application's owner bound.
- A session opened for an owner hands its post-commit events to that owner; an unbound session
  keeps the legacy path, and a commit never fails because the owner stopped admitting events.
- Direct event emissions (the first migrated cohort) are owned by the bound owner.
"""

import ast
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import runtime
from app.runtime import TaskOwner
from app.services import post_commit_events
from app.services.event_bus import event_bus

pytestmark = pytest.mark.asyncio

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _blocking_subscriber(event_type):
    seen: list[dict] = []
    release = asyncio.Event()

    async def subscriber(payload):
        seen.append(payload)
        await release.wait()

    event_bus.subscribe(event_type, subscriber)
    return seen, release


async def test_requests_run_with_the_application_owner_bound():
    from app.middleware.task_owner import TaskOwnerMiddleware

    observed = {}
    outer = FastAPI()
    inner = FastAPI()

    @outer.get("/outer")
    async def outer_route():
        observed["outer"] = runtime.current_owner()
        return {}

    @inner.get("/inner")
    async def inner_route():
        observed["inner"] = runtime.current_owner()
        return {}

    outer.mount("/mounted", inner)
    outer.add_middleware(TaskOwnerMiddleware)
    owner = TaskOwner()
    outer.state.runtime = owner

    async with AsyncClient(transport=ASGITransport(app=outer), base_url="http://test") as c:
        assert (await c.get("/outer")).status_code == 200
        assert (await c.get("/mounted/inner")).status_code == 200
    assert observed == {"outer": owner, "inner": owner}
    assert runtime.current_owner() is None


async def test_an_application_without_an_owner_serves_requests_unbound():
    from app.middleware.task_owner import TaskOwnerMiddleware

    observed = {}
    application = FastAPI()

    @application.get("/x")
    async def route():
        observed["owner"] = runtime.current_owner()
        return {}

    application.add_middleware(TaskOwnerMiddleware)
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as c:
        assert (await c.get("/x")).status_code == 200
    assert observed == {"owner": None}


async def test_the_real_application_binds_its_owner(client, admin_token, monkeypatch):
    import app.main as main_module
    from app.services.backup_service import RestoreService

    observed = {}

    def status():
        observed["owner"] = runtime.current_owner()
        return {"active": False}

    monkeypatch.setattr(RestoreService, "get_status", staticmethod(status))
    owner = TaskOwner()
    main_module.app.state.runtime = owner
    try:
        resp = await client.get("/api/backups/restore/status", headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        del main_module.app.state.runtime
    assert resp.status_code == 200
    assert observed == {"owner": owner}


async def test_a_post_commit_event_goes_to_its_sessions_owner(db_engine):
    owner = TaskOwner()
    seen, release = _blocking_subscriber("test.committed")
    factory = runtime.owned_sessions(async_sessionmaker(db_engine, class_=AsyncSession), owner)

    async with factory() as session:
        post_commit_events.defer_event(session, "test.committed", {"n": 1})
        await session.commit()

    await asyncio.sleep(0)
    assert owner.roles() == ["event:test.committed"]
    assert seen == [{"n": 1}]
    release.set()
    assert await owner.drain(1)


async def test_a_bound_owner_receives_events_from_an_unbound_session(db_engine):
    owner = TaskOwner()
    seen, release = _blocking_subscriber("test.committed")

    with runtime.bound(owner):
        async with async_sessionmaker(db_engine, class_=AsyncSession)() as session:
            post_commit_events.defer_event(session, "test.committed", {"n": 2})
            await session.commit()

    await asyncio.sleep(0)
    assert owner.roles() == ["event:test.committed"]
    release.set()
    await owner.drain(1)


async def test_an_unowned_session_keeps_the_legacy_wait(db_engine):
    seen, release = _blocking_subscriber("test.committed")
    async with async_sessionmaker(db_engine, class_=AsyncSession)() as session:
        post_commit_events.defer_event(session, "test.committed", {"n": 3})
        await session.commit()
    release.set()
    await asyncio.wait_for(post_commit_events.wait_for_deferred_events(), 1)
    assert seen == [{"n": 3}]


async def test_a_commit_succeeds_when_the_owner_no_longer_admits_events(db_engine):
    owner = TaskOwner()
    owner.close_admission()
    owner.close_event_admission()
    seen, release = _blocking_subscriber("test.committed")
    factory = runtime.owned_sessions(async_sessionmaker(db_engine, class_=AsyncSession), owner)

    async with factory() as session:
        post_commit_events.defer_event(session, "test.committed", {"n": 4})
        await session.commit()

    await asyncio.sleep(0.01)
    assert seen == [] and owner.roles() == []
    release.set()


async def test_a_direct_emission_is_owned_by_the_bound_owner(session):
    from app.services.quota_service import QuotaService  # noqa: F401 -- one of the migrated emitters
    from app.services.event_types import QUOTA_WARNING

    owner = TaskOwner()
    seen, release = _blocking_subscriber(QUOTA_WARNING)
    with runtime.bound(owner):
        runtime.spawn_event(event_bus.emit(QUOTA_WARNING, {"k": "v"}), event_type=QUOTA_WARNING)
    await asyncio.sleep(0)
    assert owner.roles() == [f"event:{QUOTA_WARNING}"]
    release.set()
    await owner.drain(1)


def test_no_direct_emission_bypasses_the_owner():
    """Every direct ``event_bus.emit`` that is not awaited in place goes through ``spawn_event``."""
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("create_task", "ensure_future") or not node.args:
                continue
            arg = node.args[0]
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "emit"
                and path.name != "post_commit_events.py"
            ):
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")
    assert offenders == []
