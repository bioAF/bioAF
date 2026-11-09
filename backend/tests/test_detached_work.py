"""Detached work and its parent-owned children (plan 14, delivery D1).

- A detached job started while an owner is bound belongs to that owner, under a stable role.
- A parent that fans work out owns those children: cancelling it cancels and awaits every child,
  including siblings it had not awaited yet.
- Every task the application starts goes through the owner, except the recorded exceptions below.
"""

import ast
import asyncio
from pathlib import Path

import pytest

from app import runtime
from app.runtime import TaskOwner

pytestmark = pytest.mark.asyncio

APP_DIR = Path(__file__).resolve().parent.parent / "app"


async def _until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def test_a_detached_job_belongs_to_the_bound_owner(monkeypatch):
    from app.services.literature import search_service

    release = asyncio.Event()

    async def run(search_id, user_id, max_per_source):
        await release.wait()

    monkeypatch.setattr(search_service, "_run_search", run)
    owner = TaskOwner()
    with runtime.bound(owner):
        await search_service.schedule_search_run(search_id=1, user_id=1)
    assert owner.roles() == ["literature-search"]
    report = await owner.shutdown(drain_seconds=0, cancel_seconds=1)
    assert report.cancelled == ["literature-search"]


async def test_cancelling_a_search_run_cancels_and_awaits_every_source_call(session, admin_user, monkeypatch):
    from app.models.literature import SOURCE_EUROPEPMC, SOURCE_OPENALEX, SOURCE_PUBMED
    from app.services.literature import search_service

    sources = [SOURCE_PUBMED, SOURCE_EUROPEPMC, SOURCE_OPENALEX]
    search = await search_service.create_search(
        session, org_id=admin_user.organization_id, user_id=admin_user.id, query="q", sources=sources
    )
    await session.commit()
    started: list[str] = []
    cancelled: list[str] = []

    async def source(src, query, api_key, max_per_source):
        started.append(src)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)  # cleanup that takes a turn of the loop
            cancelled.append(src)
            raise

    monkeypatch.setattr(search_service, "_run_source_with_timeout", source)
    run = asyncio.create_task(search_service._run_search(search.id, admin_user.id, 5))
    await _until(lambda: len(started) == 3)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert sorted(cancelled) == sorted(sources)


# Task creation that does not go through the owner, and why. Anything else is a regression.
UNOWNED = {
    ("runtime.py", "TaskOwner.spawn"): "the owner itself",
    ("runtime.py", "spawn"): "legacy fallback while no owner is bound",
    ("runtime.py", "Ticket.start"): "response job started while no owner is bound",
    ("runtime.py", "spawn_threadsafe.start"): "thread callback while no owner is bound",
    ("services/post_commit_events.py", "_publish_after_commit"): "legacy path for unbound sessions",
    ("main.py", "lifespan"): "maintenance watcher, released by the lifespan's exit stack",
    ("startup.py", "bounded"): "a shutdown step run after the owner closed; abandoned at its deadline",
}


def test_every_task_the_application_starts_is_owned_or_a_recorded_exception():
    found = set()
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = str(path.relative_to(APP_DIR))
        tree = ast.parse(path.read_text())
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def _scope(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

            def visit_Call(self, node):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr in ("create_task", "ensure_future"):
                    found.add((rel, ".".join(stack)))
                self.generic_visit(node)

        Visitor().visit(tree)
    assert found == set(UNOWNED)
