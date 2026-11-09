"""Per-test lifecycle helpers used by conftest's database fixture.

- ``redirect_session_factories`` points every ``app``/``tests`` module that holds
  the production session factory (``from app.database import
  async_session_factory`` binds a copy at import) at the test one, and puts
  them all back afterwards, including modules first imported during the test.
- ``drain_and_cancel`` waits for post-commit events the test scheduled, then
  cancels and awaits application tasks the test started. Tasks that existed
  before the test, and tasks that are not application code (pytest, anyio,
  httpx), are left alone.
- ``run_all`` runs every cleanup step even if an earlier one raises.
- ``isolated_state`` restores in-process caches and registries to how they
  were when the test started, on success, error or cancellation.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import app as _app_package

_APP_DIR = str(Path(_app_package.__file__).resolve().parent)

# Module state that persists between tests unless reset: (module, attribute).
RESET_STATE: tuple[tuple[str, str], ...] = (
    ("app.middleware.rate_limit", "rate_limit_requests"),
    ("app.services.role_service", "_permission_cache"),
    ("app.services.api_key_service", "_LAST_USED_DEBOUNCE"),
    ("app.services.request_health", "_counters"),
    ("app.services.vocabulary_validator", "_cache"),
    ("app.services.plot_archive_service", "_last_scan"),
    ("app.services.upload_service", "_pending_uploads"),
    ("app.services.lab_document_upload_service", "_pending"),
    ("app.services.upgrade_service", "_version_cache"),
    ("app.services.upgrade_service", "_version_cache_time"),
    ("app.services.slurm_service", "_cluster_status_cache"),
    ("app.services.slurm_service", "_cluster_status_cache_time"),
    ("app.services.terraform_executor", "_active_processes"),
    ("app.services.literature.fulltext_service", "_parses_running"),
    ("app.services.backup_service", "_restore_state"),
    ("app.services.networking_applier", "_mock_singleton"),
    ("app.services.pubsub_listener", "_manager"),
    ("app.platform.cloud_provider", "_resolved_backends"),
    ("app.adapters.work_nodes.ec2", "_local_vms"),
    ("app.adapters.work_nodes.gce", "_local_vms"),
    ("app.adapters.notebooks.kubernetes", "_local_sessions"),
    # Tests reload encryption_service with other keys; reload keeps the module object, so
    # restoring these two restores it for every importer.
    ("app.platform.encryption_service", "_keys"),
    ("app.platform.encryption_service", "_multifernet"),
)

# Object attributes reset the same way: (module, object, attribute).
RESET_OBJECT_STATE: tuple[tuple[str, str, str], ...] = (("app.services.event_bus", "event_bus", "_subscribers"),)

# Module state deliberately not reset here, and why.
NOT_RESET: dict[tuple[str, str], str] = {
    ("app.database", "engine"): "replaced by the database fixture's session-factory redirect",
    ("app.database", "async_session_factory"): "redirected per test by redirect_session_factories",
    ("app.adapters.registry", "_compute_adapter"): "reset by the _init_adapter_registry fixture",
    ("app.adapters.registry", "_storage_adapter"): "reset by the _init_adapter_registry fixture",
    ("app.adapters.registry", "_notebook_adapter"): "reset by the _init_adapter_registry fixture",
    ("app.adapters.registry", "_cellxgene_adapter"): "reset by the _init_adapter_registry fixture",
    ("app.adapters.registry", "_work_node_adapter"): "reset by the _init_adapter_registry fixture",
    ("app.adapters.registry", "_initialized"): "reset by the _init_adapter_registry fixture",
    ("app.services.post_commit_events", "_in_flight"): "drained by drain_and_cancel; tasks remove themselves",
    ("app.adapters.validation.gcp", "RECOMMENDED_ROLES"): "built once at import from a static map",
    ("app.adapters.validation.gcp", "_APP_PERMS"): "built once at import from a static probe file",
    ("app.adapters.validation.gcp", "_BOOTSTRAP_PERMS"): "built once at import from a static probe file",
    ("app.adapters.validation.gcp", "_DROPPED_PERMS"): "built once at import from a static probe file",
    ("app.adapters.validation.gcp", "_SHARED_PERMS"): "built once at import from a static probe file",
    ("app.api.sheets_import", "_ALL_SAMPLE_FIELDS"): "built once at import from a constant",
    ("app.api.sheets_import", "_DEFAULTABLE_FIELDS"): "built once at import from a constant",
    ("app.services.validation_classifier_service", "_SPEC_BY_KEY"): "registry filled at import",
    ("app.services.validation_classifier_service", "_KEY_LOOKUP"): "registry filled at import",
}


# --- session factory routing ------------------------------------------------------


# Every test factory ever installed. A monkeypatch undo that runs after db_engine's teardown can put
# one back on a module, so the next redirect replaces those too, not just the production factory.
_INSTALLED: list[object] = []


def _holders(*values: object) -> list[Any]:
    wanted = {id(v) for v in values}
    found = []
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "app" or name.startswith(("app.", "tests."))):
            continue
        if id(getattr(module, "async_session_factory", None)) in wanted:
            found.append(module)
    return found


def redirect_session_factories(test_factory: object, production_factory: object) -> Callable[[], None]:
    """Point every module holding the production (or a stale test) factory at ``test_factory``; return the undo."""
    for module in _holders(production_factory, *_INSTALLED):
        module.async_session_factory = test_factory
    _INSTALLED.clear()  # older factories are now on no module; keep only the live one
    _INSTALLED.append(test_factory)

    def restore() -> None:
        # Also catches modules imported during the test, which copied the test factory.
        for module in _holders(test_factory):
            module.async_session_factory = production_factory

    return restore


# --- tasks ---------------------------------------------------------------------------


def snapshot_tasks() -> set[asyncio.Task]:
    try:
        return set(asyncio.all_tasks())
    except RuntimeError:  # no running loop
        return set()


def _is_app_task(task: asyncio.Task) -> bool:
    coro = task.get_coro()
    code = getattr(coro, "cr_code", None) or getattr(coro, "gi_code", None)
    filename = getattr(code, "co_filename", "") or ""
    return filename.startswith(_APP_DIR)


async def cancel_owned_tasks(before: set[asyncio.Task]) -> int:
    """Cancel and await unfinished application tasks started after ``before`` was taken."""
    current = asyncio.current_task()
    owned = [
        t for t in asyncio.all_tasks() if t is not current and t not in before and not t.done() and _is_app_task(t)
    ]
    for task in owned:
        task.cancel()
    if owned:
        await asyncio.gather(*owned, return_exceptions=True)
    return len(owned)


async def drain_and_cancel(before: set[asyncio.Task], drain_timeout: float = 10.0) -> int:
    """Let scheduled post-commit emissions finish, then cancel what the test left running."""
    from app.services.post_commit_events import wait_for_deferred_events

    try:
        await asyncio.wait_for(wait_for_deferred_events(), timeout=drain_timeout)
    except TimeoutError:
        pass  # whatever is still running is cancelled below
    return await cancel_owned_tasks(before)


async def run_all(*steps: Callable[[], Any]) -> None:
    """Run every step (sync or async); re-raise the first failure after all have run."""
    first: BaseException | None = None
    for step in steps:
        try:
            result = step()
            if inspect.isawaitable(result):
                await result
        except BaseException as exc:  # noqa: BLE001 -- cleanup must continue; re-raised below
            if first is None:
                first = exc
    if first is not None:
        raise first


# --- per-test state ----------------------------------------------------------------------


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in value.items()}
    if isinstance(value, (list, set)):
        return type(value)(value) if not isinstance(value, list) else list(value)
    return value


def _restore_into(owner: Any, attr: str, original: Any, contents: Any) -> None:
    setattr(owner, attr, original)
    if isinstance(original, dict):
        original.clear()
        original.update({k: (list(v) if isinstance(v, list) else v) for k, v in contents.items()})
    elif isinstance(original, (list, set)):
        original.clear()
        (original.extend if isinstance(original, list) else original.update)(contents)


@contextmanager
def isolated_state() -> Iterator[None]:
    saved: list[tuple[Any, str, Any, Any]] = []
    for module_name, attr in RESET_STATE:
        module = importlib.import_module(module_name)
        value = getattr(module, attr)
        saved.append((module, attr, value, _copy(value)))
    # Some test files assign app settings directly instead of through monkeypatch.
    from app.config import settings

    settings_before = dict(settings.__dict__)
    for module_name, obj_name, attr in RESET_OBJECT_STATE:
        owner = getattr(importlib.import_module(module_name), obj_name)
        value = getattr(owner, attr)
        saved.append((owner, attr, value, _copy(value)))
    try:
        yield
    finally:
        for owner, attr, original, contents in saved:
            _restore_into(owner, attr, original, contents)
        for key in set(settings.__dict__) - set(settings_before):
            del settings.__dict__[key]
        settings.__dict__.update(settings_before)
