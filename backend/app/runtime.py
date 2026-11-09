"""Application task ownership (plan 14).

A ``TaskOwner`` holds the tasks one application lifespan (or one test) starts: strong references,
a stable role name per task, the observed outcome of each, and a shutdown bounded by shared
deadlines. One task's failure never cancels its peers, and nothing is replayed automatically.

Ownership reaches deep service code two ways:

- ``bound(owner)`` sets a context variable around a request, a worker, or a test. Tasks copy their
  context when created, so work spawned from owned work stays owned.
- ``owned_sessions(factory, owner)`` puts the owner in each new session's ``info``, where the
  synchronous post-commit hook can find it without a running context.

``spawn`` uses the bound owner, or, while callers are migrated, falls back to a plain unowned task
(the previous behavior; such tasks are still cancelled by the test fixtures' filename detection).

Lifecycle log lines carry only controlled fields: role, transition, outcome, elapsed time,
remaining count, and exception type. Never the exception text, the coroutine's arguments, a task
repr, or a traceback: those may hold credentials. Each job keeps its own domain error logging.

The owner is per process. Several uvicorn workers or replicas would each run their own, with no
exclusion between them; the image runs one process.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger("bioaf.runtime")

OWNER_INFO_KEY = "bioaf_task_owner"

_current: contextvars.ContextVar[TaskOwner | None] = contextvars.ContextVar("bioaf_task_owner", default=None)


class OwnerClosed(RuntimeError):
    """The owner is shutting down and admits no new work of this kind."""


@dataclass
class ShutdownReport:
    """What shutdown did: roles cancelled after the drain, and roles still running at the deadline."""

    cancelled: list[str] = field(default_factory=list)
    uncooperative: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.uncooperative


@dataclass
class _Entry:
    role: str
    started: float
    event: bool


def _lifecycle(level: int, role: str, transition: str, **fields: Any) -> None:
    parts = [f"role={role}", f"transition={transition}"]
    parts += [f"{k}={v}" for k, v in fields.items() if v is not None]
    extra = {"lifecycle_role": role, "lifecycle_transition": transition}
    extra.update({f"lifecycle_{k}": v for k, v in fields.items()})
    logger.log(level, "task %s", " ".join(parts), extra=extra)


class TaskOwner:
    """Owns the tasks of one application lifespan or one test."""

    def __init__(self, name: str = "application") -> None:
        self.name = name
        self._tasks: dict[asyncio.Task[Any], _Entry] = {}
        self._admitting = True
        self._admitting_events = True
        self._reservations = 0
        self.stopping = asyncio.Event()
        self.report: ShutdownReport | None = None

    @property
    def admitting(self) -> bool:
        return self._admitting

    def roles(self) -> list[str]:
        return sorted(e.role for e in self._tasks.values())

    def tasks(self) -> list[asyncio.Task[Any]]:
        return list(self._tasks)

    def spawn(
        self, coro: Coroutine[Any, Any, Any], *, role: str, event: bool = False, reserved: bool = False
    ) -> asyncio.Task[Any]:
        """Start ``coro`` as an owned task.

        Closed admission refuses new work, but not a descendant of work already owned: a task
        settling during the drain may still start the children it needs. Events are admitted until
        event admission closes.
        """
        admitted = self._admitting_events if event else self._admitting
        descendant = asyncio.current_task() in self._tasks and self._admitting_events
        if not admitted and not descendant and not (reserved and self._admitting_events):
            coro.close()
            _lifecycle(logging.WARNING, role, "refused", reason="shutting_down")
            raise OwnerClosed(f"{self.name} is shutting down")
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        context.run(_current.set, self)
        task = loop.create_task(coro, name=f"bioaf:{role}", context=context)
        self._tasks[task] = _Entry(role, loop.time(), event)
        task.add_done_callback(self._settled)
        _lifecycle(logging.DEBUG, role, "started")
        return task

    def _settled(self, task: asyncio.Task[Any]) -> None:
        entry = self._tasks.pop(task, None)
        if entry is None:
            return
        elapsed = f"{task.get_loop().time() - entry.started:.3f}s"
        remaining = len(self._tasks)
        if task.cancelled():
            _lifecycle(logging.DEBUG, entry.role, "settled", outcome="cancelled", elapsed=elapsed, remaining=remaining)
            return
        exc = task.exception()
        if exc is not None:
            _lifecycle(
                logging.ERROR,
                entry.role,
                "settled",
                outcome="failed",
                elapsed=elapsed,
                remaining=remaining,
                exception_type=type(exc).__name__,
            )
            return
        _lifecycle(logging.DEBUG, entry.role, "settled", outcome="finished", elapsed=elapsed, remaining=remaining)

    def reserve(self, role: str) -> None:
        """Hold admission for one job a request is about to commit; refused once closed."""
        if not self._admitting:
            _lifecycle(logging.WARNING, role, "refused", reason="shutting_down")
            raise OwnerClosed(f"{self.name} is shutting down")
        self._reservations += 1

    def release(self) -> None:
        self._reservations -= 1

    def close_admission(self) -> None:
        """Refuse new ordinary work; events stay admitted (stopping jobs may still commit some)."""
        self._admitting = False
        self.stopping.set()

    def close_event_admission(self) -> None:
        self._admitting_events = False

    async def drain(self, seconds: float) -> bool:
        """Wait, up to ``seconds`` in total, for every owned task (children included) to finish."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        current = asyncio.current_task()
        while (pending := [t for t in self._tasks if t is not current]) or self._reservations:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            # A reserved job not yet started counts too: its request is committing it.
            if not pending:
                await asyncio.sleep(min(remaining, 0.01))
            else:
                await asyncio.wait(pending, timeout=min(remaining, 0.05) if self._reservations else remaining)
        return True

    async def cancel_all(self, seconds: float) -> tuple[list[str], list[str]]:
        """Cancel every owned task, repeatedly, until all settle or ``seconds`` pass in total.

        Returns the roles cancelled and the roles still running at the deadline.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        current = asyncio.current_task()
        cancelled: list[str] = []
        seen: set[asyncio.Task[Any]] = set()
        while pending := [t for t in self._tasks if t is not current]:
            for task in pending:
                if task not in seen:
                    seen.add(task)
                    cancelled.append(self._tasks[task].role)
                task.cancel()
            remaining = deadline - loop.time()
            if remaining <= 0:
                return sorted(cancelled), sorted(self._tasks[t].role for t in pending if not t.done())
            await asyncio.wait(pending, timeout=min(remaining, 0.05))
        return sorted(cancelled), []

    async def shutdown(self, *, drain_seconds: float, cancel_seconds: float) -> ShutdownReport:
        """Close admission, let work settle for ``drain_seconds``, then cancel within ``cancel_seconds``.

        Uncooperative work (a task that keeps running after repeated cancellation) is reported,
        not waited for: cancellation cannot stop a thread or a blocked loop, so the process
        supervisor's deadline is the hard stop.
        """
        self.close_admission()
        report = ShutdownReport()
        drained = await self.drain(drain_seconds)
        if not drained:
            report.cancelled, report.uncooperative = await self.cancel_all(cancel_seconds)
        self.close_event_admission()
        if self._tasks and not report.uncooperative:
            # Events admitted while cancelling.
            more_cancelled, report.uncooperative = await self.cancel_all(cancel_seconds)
            report.cancelled = sorted(set(report.cancelled) | set(more_cancelled))
        level = logging.INFO if report.clean else logging.ERROR
        _lifecycle(
            level,
            self.name,
            "shutdown",
            outcome="clean" if report.clean else "unclean",
            cancelled=len(report.cancelled),
            remaining=len(report.uncooperative),
        )
        for role in report.uncooperative:
            _lifecycle(logging.ERROR, role, "abandoned", outcome="uncooperative")
        self.report = report
        return report


def current_owner() -> TaskOwner | None:
    return _current.get()


@contextmanager
def bound(owner: TaskOwner | None) -> Iterator[TaskOwner | None]:
    """Make ``owner`` the current owner for the enclosed code (and tasks it creates)."""
    token = _current.set(owner)
    try:
        yield owner
    finally:
        _current.reset(token)


def spawn(coro: Coroutine[Any, Any, Any], *, role: str, event: bool = False) -> asyncio.Task[Any]:
    """Start ``coro`` under the current owner; without one, as a plain (legacy, unowned) task."""
    owner = _current.get()
    if owner is not None:
        return owner.spawn(coro, role=role, event=event)
    return asyncio.create_task(coro, name=f"bioaf:{role}")


def spawn_event(coro: Coroutine[Any, Any, Any], *, event_type: str) -> asyncio.Task[Any] | None:
    """Start an event emission; a shutting-down owner drops it (events are best effort)."""
    try:
        return spawn(coro, role=f"event:{event_type}", event=True)
    except OwnerClosed:
        return None


class Ticket:
    """A reserved admission: ``start`` the job once its row is committed."""

    def __init__(self, owner: TaskOwner | None, role: str) -> None:
        self._owner = owner
        self._role = role
        self.task: asyncio.Task[Any] | None = None

    def start(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if self._owner is None:
            self.task = asyncio.create_task(coro, name=f"bioaf:{self._role}")
        else:
            self.task = self._owner.spawn(coro, role=self._role, reserved=True)
        return self.task


@contextmanager
def admission(role: str) -> Iterator[Ticket]:
    """Reserve admission for one job before committing it; start it after the commit.

    Raises ``OwnerClosed`` before the caller writes anything when the owner is shutting down. If
    the block ends without starting the job (a rollback, a failure), the reservation is released
    and nothing runs. Without a bound owner the job starts as a plain task.
    """
    owner = _current.get()
    if owner is not None:
        owner.reserve(role)
    try:
        yield Ticket(owner, role)
    finally:
        if owner is not None:
            owner.release()


def owned_sessions(factory: Callable[..., Any], owner: TaskOwner) -> Callable[..., Any]:
    """A session factory whose sessions carry ``owner`` in their ``info`` for the commit hooks."""

    def make(**kwargs: Any) -> Any:
        info = dict(kwargs.pop("info", None) or {})
        info[OWNER_INFO_KEY] = owner
        return factory(info=info, **kwargs)

    return make


def session_owner(session: Session) -> TaskOwner | None:
    """The owner a session was opened for, else the current owner, else None."""
    owner = session.info.get(OWNER_INFO_KEY)
    return owner if owner is not None else _current.get()
