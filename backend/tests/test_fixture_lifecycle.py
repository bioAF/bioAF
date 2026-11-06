"""What the database fixture promises about routing, background tasks, and per-test state.

Every way application code reaches the database during a test lands in that
test's schema; tasks a test started are finished or cancelled before its schema
goes away; a failing cleanup step cannot skip the ones after it; and in-process
caches and registries start each test as they were after import.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Imported at collection, before any database fixture runs, as the application does.
import app.api.agent_reviews  # noqa: F401
import app.api.lab_documents  # noqa: F401
import app.api.lab_glossary  # noqa: F401
import app.api.stack_deploy  # noqa: F401
from tests.support import lifecycle

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Modules that bind the session factory at import with `from app.database import async_session_factory`.
ALIASING_MODULES = ["app.api.stack_deploy", "app.api.lab_documents", "app.api.lab_glossary", "app.api.agent_reviews"]


# --- routing -----------------------------------------------------------------


@pytest.mark.parametrize("module_name", ALIASING_MODULES)
def test_module_imported_aliases_point_at_the_test_engine(db_engine, module_name):
    module = importlib.import_module(module_name)
    assert module.async_session_factory.kw["bind"] is db_engine


@pytest.mark.asyncio
async def test_a_background_write_through_an_alias_is_visible_to_another_session(db_engine, session, admin_user):
    from app.api import stack_deploy

    async with stack_deploy.async_session_factory() as bg:
        await bg.execute(
            text("UPDATE users SET name = 'written in the background' WHERE id = :id"), {"id": admin_user.id}
        )
        await bg.commit()

    other = async_sessionmaker(db_engine, class_=AsyncSession)
    async with other() as reader:
        name = (await reader.execute(text("SELECT name FROM users WHERE id = :id"), {"id": admin_user.id})).scalar_one()
    assert name == "written in the background"


def _fake_module(name: str, factory) -> types.ModuleType:
    module = types.ModuleType(name)
    module.async_session_factory = factory  # type: ignore[attr-defined]
    sys.modules[name] = module
    return module


def test_redirect_covers_modules_imported_before_and_during_the_test():
    production, replacement = object(), object()
    before = _fake_module("app._probe_before", production)
    try:
        restore = lifecycle.redirect_session_factories(replacement, production)
        assert before.async_session_factory is replacement
        # Imported while the test runs, so it copies whatever app.database holds then.
        during = _fake_module("app._probe_during", replacement)
        restore()
        assert before.async_session_factory is production
        assert during.async_session_factory is production
    finally:
        sys.modules.pop("app._probe_before", None)
        sys.modules.pop("app._probe_during", None)


def test_redirect_leaves_unrelated_modules_alone():
    production, replacement = object(), object()
    unrelated = _fake_module("somelib._probe", production)
    try:
        restore = lifecycle.redirect_session_factories(replacement, production)
        assert unrelated.async_session_factory is production
        restore()
    finally:
        sys.modules.pop("somelib._probe", None)


# --- background tasks ----------------------------------------------------------


@pytest.mark.asyncio
async def test_owned_app_tasks_are_cancelled_and_awaited_others_are_left():
    from app.services.event_bus import EventBus

    bus = EventBus()
    calls: list[asyncio.Event] = []

    async def _forever(_payload):
        calls[-1].set()
        await asyncio.sleep(3600)

    async def _emit_and_wait_until_running() -> asyncio.Task:
        calls.append(asyncio.Event())
        task = asyncio.create_task(bus.emit("probe", {}))
        await calls[-1].wait()
        return task

    bus.subscribe("probe", _forever)
    existing = await _emit_and_wait_until_running()  # app code, running before the snapshot
    before = lifecycle.snapshot_tasks()
    owned = await _emit_and_wait_until_running()  # app code, started by "the test"
    foreign = asyncio.create_task(asyncio.sleep(3600))  # not app code
    await asyncio.sleep(0)

    cancelled = await lifecycle.cancel_owned_tasks(before)

    assert cancelled >= 1
    assert owned.cancelled()
    assert not existing.done()
    assert not foreign.done()
    for task in (existing, foreign):
        task.cancel()
    await asyncio.gather(existing, foreign, return_exceptions=True)


@pytest.mark.asyncio
async def test_deferred_events_are_drained_before_tasks_are_cancelled(session):
    from app.services.event_bus import event_bus
    from app.services.post_commit_events import defer_event

    delivered: list[dict] = []

    async def _slow(payload):
        await asyncio.sleep(0.05)
        delivered.append(payload)

    event_bus.subscribe("lifecycle.probe", _slow)
    defer_event(session, "lifecycle.probe", {"n": 1})
    await session.commit()
    before = set()  # treat every task as owned, as the fixture does for tasks the test started

    await lifecycle.drain_and_cancel(before)

    assert delivered == [{"n": 1}]


@pytest.mark.asyncio
async def test_every_cleanup_step_runs_even_when_an_earlier_one_fails():
    ran: list[str] = []

    async def _fails():
        ran.append("fails")
        raise RuntimeError("first")

    async def _ok():
        ran.append("ok")

    def _sync_ok():
        ran.append("sync")

    with pytest.raises(RuntimeError, match="first"):
        await lifecycle.run_all(_fails, _ok, _sync_ok)
    assert ran == ["fails", "ok", "sync"]


# --- per-test state --------------------------------------------------------------


def test_state_is_restored_after_a_test_changes_it():
    from app.services import role_service

    with lifecycle.isolated_state():
        role_service._permission_cache[999] = (0.0, frozenset())
    assert 999 not in role_service._permission_cache


def test_state_is_restored_when_the_test_raises():
    from app.middleware.rate_limit import rate_limit_requests

    with pytest.raises(ValueError):
        with lifecycle.isolated_state():
            rate_limit_requests[("probe", "x")].append(1.0)
            raise ValueError
    assert ("probe", "x") not in rate_limit_requests


@pytest.mark.asyncio
async def test_state_is_restored_when_the_test_is_cancelled():
    from app.services.event_bus import event_bus

    async def _subscriber(_payload):
        return None

    async def _body():
        with lifecycle.isolated_state():
            event_bus.subscribe("probe.cancelled", _subscriber)
            await asyncio.sleep(3600)

    task = asyncio.create_task(_body())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "probe.cancelled" not in event_bus._subscribers


def test_scalar_caches_are_restored_too():
    from app.services import upgrade_service

    original = upgrade_service._version_cache_time
    with lifecycle.isolated_state():
        upgrade_service._version_cache_time = object()
    assert upgrade_service._version_cache_time is original


def _module_level_mutables() -> set[tuple[str, str]]:
    """Module-level names in app/ that start as an empty container, or are rebound with `global`.

    An empty container at import is filled at runtime, which makes it state that
    can carry from one test into the next.
    """
    found: set[tuple[str, str]] = set()
    containers = {"dict", "list", "set", "defaultdict", "OrderedDict", "deque"}
    for path in APP_DIR.rglob("*.py"):
        parts = path.relative_to(APP_DIR.parent).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        tree = ast.parse(path.read_text())
        for node in tree.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            empty_literal = (isinstance(value, ast.Dict) and not value.keys) or (
                isinstance(value, (ast.List, ast.Set)) and not value.elts
            )
            container_call = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in containers
                and not value.keywords
                and all(
                    not isinstance(a, (ast.Dict, ast.List, ast.Set, ast.ListComp, ast.DictComp, ast.GeneratorExp))
                    for a in value.args
                )
            )
            if empty_literal or container_call:
                for t in targets:
                    if isinstance(t, ast.Name) and not t.id.startswith("__"):
                        found.add((module, t.id))
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                for name in node.names:
                    found.add((module, name))
    return found


def test_every_piece_of_mutable_module_state_is_reset_or_explained():
    """A new module-level cache must be added to the reset list or to the explained exceptions."""
    covered = set(lifecycle.RESET_STATE) | set(lifecycle.NOT_RESET)
    missing = sorted(_module_level_mutables() - covered)
    assert missing == [], "add these to tests/support/lifecycle.py RESET_STATE or NOT_RESET: " + repr(missing)


def test_the_reset_list_names_real_attributes():
    for module_name, attr in lifecycle.RESET_STATE:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), (module_name, attr)


def test_settings_assigned_directly_are_restored():
    """Several test files assign app settings without monkeypatch; the next test must not inherit them."""
    from app.config import settings

    original = settings.encryption_keys, settings.bcrypt_rounds
    with lifecycle.isolated_state():
        settings.encryption_keys = ""
        settings.bcrypt_rounds = 31
    assert (settings.encryption_keys, settings.bcrypt_rounds) == original


def test_a_reloaded_encryption_service_is_restored():
    """Reloading encryption_service with bad keys must not leave every later test unable to encrypt."""
    import importlib

    from app.config import settings
    from app.platform import encryption_service

    with lifecycle.isolated_state():
        settings.encryption_keys = "yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U="
        importlib.reload(encryption_service)
    cipher = encryption_service.encrypt("x")
    # The suite's two-key setup is back: the second key can decrypt nothing, the first writes.
    assert encryption_service.decrypt(cipher) == "x"
    assert len(encryption_service._keys) == 2


def test_redirect_replaces_a_stale_test_factory_left_by_a_late_monkeypatch_undo():
    """monkeypatch can undo after db_engine's teardown and put a dead test factory back on a module."""
    production, first, second = object(), object(), object()
    module = _fake_module("app._probe_stale", production)
    try:
        restore = lifecycle.redirect_session_factories(first, production)
        restore()
        module.async_session_factory = first  # what the late monkeypatch undo leaves behind

        restore = lifecycle.redirect_session_factories(second, production)
        assert module.async_session_factory is second
        restore()
        assert module.async_session_factory is production
    finally:
        sys.modules.pop("app._probe_stale", None)
