"""Subprocess mechanics shared by the streaming Terraform operations.

Apply, foundation bootstrap, and destroy all read Terraform's JSON output line by
line, heartbeat while it is quiet, and must stop it on every exit path. This
module owns those mechanics. What each operation does with a line, and what it
does before and after the process, stays explicit in ``TerraformExecutor``.

Terminal results. An async generator cannot return a value, so an operation
reports its outcome through its last event: ``apply_complete`` (confirmed
success, with outputs for deployment hooks and billing consumers),
``apply_error`` (failure), or ``interrupted`` (stopped before it could finish).
``interrupted`` is internal: routes translate it, because the frontend does not
know it. ``OperationOutcome`` reads those events, and treats an exhausted stream
with no terminal event as uncertain rather than as success.

Transaction ownership. Generators flush; they never commit on behalf of a caller.
The session owner holds the stream with ``owned_event_stream``, which closes it
first (so Terraform has stopped and an interrupted run is flushed) and then
commits or rolls back.

Cancellation. A client disconnect arrives through an anyio cancel scope, which
cancels every further ``await`` in the scope, not just the next one. Cleanup that
must finish therefore runs in a shielded scope. It is bounded: the longest wait is
``STOP_TERM_SECONDS + STOP_KILL_SECONDS``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.terraform_stream")

HEARTBEAT_SECONDS: float = 15
STOP_TERM_SECONDS: float = 10
STOP_KILL_SECONDS: float = 5

INTERRUPTED_EVENT = "interrupted"


@dataclass
class TerraformProgressEvent:
    """A single SSE event emitted during terraform apply."""

    # "resource_complete" | "apply_complete" | "apply_error" | "progress" | "heartbeat" | "interrupted"
    event_type: str
    message: str = ""
    resource_address: str = ""
    resources_completed: int = 0
    resources_total: int = 0
    log_line: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class OperationOutcome:
    """What the events of one Terraform operation established.

    Success has to be confirmed by ``apply_complete``. A stream that ends without
    a terminal event is neither success nor failure, so callers must not report
    success, clear resource identity, or enter failure branches on it.
    """

    terminal_event: str | None = None

    def observe(self, event: TerraformProgressEvent) -> None:
        if event.event_type in ("apply_complete", "apply_error", INTERRUPTED_EVENT):
            self.terminal_event = event.event_type

    @property
    def succeeded(self) -> bool:
        return self.terminal_event == "apply_complete"

    @property
    def failed(self) -> bool:
        return self.terminal_event == "apply_error"


@dataclass
class StreamProgress:
    """Counters and raw output for one running process."""

    resources_completed: int = 0
    resources_total: int = 0
    log_lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessExit:
    return_code: int
    stderr: str


# Turns one parsed JSON output line into an event, or None for a line the operation ignores.
LineClassifier = Callable[[dict, str], Awaitable[TerraformProgressEvent | None]]


async def stream_terraform_output(
    process: asyncio.subprocess.Process,
    *,
    heartbeat_message: str,
    progress: StreamProgress,
    classify: LineClassifier,
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Yield an event per classified output line until stdout closes.

    Lines that are empty are dropped; lines that are not JSON objects are logged
    but not classified. A quiet process produces heartbeats. The process is left
    running on early exit: stopping it belongs to the operation, which has to
    order that against its own lock and directory cleanup.
    """
    while True:
        try:
            line_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            yield TerraformProgressEvent(
                event_type="heartbeat",
                message=heartbeat_message,
                resources_completed=progress.resources_completed,
                resources_total=progress.resources_total,
            )
            continue

        if not line_bytes:
            return

        line = line_bytes.decode(errors="replace").rstrip()
        if not line:
            continue
        progress.log_lines.append(line)

        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        event = await classify(entry, line)
        if event is not None:
            yield event


async def read_exit(process: asyncio.subprocess.Process) -> ProcessExit:
    """Collect stderr and the exit code once stdout has closed."""
    stderr = (await process.stderr.read()).decode(errors="replace") if process.stderr else ""
    return ProcessExit(return_code=await process.wait(), stderr=stderr)


def diagnostics_message(log_lines: list[str]) -> str:
    """Join the diagnostics in Terraform's output.

    In JSON mode Terraform writes diagnostic errors to stdout, not stderr, so this
    is where the real error is.
    """
    diagnostics: list[str] = []
    for line in log_lines:
        try:
            entry = json.loads(line)
            if entry.get("type") == "diagnostic":
                diag = entry.get("diagnostic", {})
                detail = diag.get("detail") or diag.get("summary", "")
                if detail:
                    diagnostics.append(detail)
        except (json.JSONDecodeError, AttributeError):
            pass
    return "; ".join(diagnostics)


async def stop_process(process: asyncio.subprocess.Process) -> bool:
    """Stop Terraform and report whether its exit is confirmed.

    SIGTERM, wait, SIGKILL, wait. This is the only stop routine: remote-state lock
    and work-directory cleanup must run only after it returns True, because
    releasing a lock under a running Terraform lets another operation overlap it.
    """
    if process.returncode is not None:
        return True

    with anyio.CancelScope(shield=True):
        _send_signal(process.terminate)
        await _wait_for_exit(process, STOP_TERM_SECONDS)
        if process.returncode is None:
            _send_signal(process.kill)
            await _wait_for_exit(process, STOP_KILL_SECONDS)

    return process.returncode is not None


def _send_signal(send: Callable[[], None]) -> None:
    try:
        send()
    except ProcessLookupError:
        pass


async def _wait_for_exit(process: asyncio.subprocess.Process, seconds: float) -> None:
    try:
        await asyncio.wait_for(process.wait(), seconds)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # wait() can outlast the process while its pipes drain, so the caller
        # trusts returncode. A repeated cancellation only shortens the grace.
        pass


async def commit_interruption(session: AsyncSession) -> None:
    """Commit an interruption outcome if the transaction survived it."""
    with anyio.CancelScope(shield=True):
        try:
            await session.commit()
        except Exception:
            logger.warning("Could not commit an interrupted Terraform operation", exc_info=True)
            await session.rollback()


@asynccontextmanager
async def owned_event_stream(
    session: AsyncSession,
    events: AsyncGenerator[TerraformProgressEvent, None],
) -> AsyncIterator[AsyncGenerator[TerraformProgressEvent, None]]:
    """Hold a Terraform event stream as the owner of ``session``.

    The stream is closed before the transaction ends, so whatever way the body
    exits, Terraform has stopped and restoration has run by then. A drained stream
    commits. Cancellation or early closure commits the interruption outcome. An
    unexpected error rolls back: it must not persist partial business changes.
    """
    try:
        async with aclosing(events) as stream:
            yield stream
    except (asyncio.CancelledError, GeneratorExit):
        await commit_interruption(session)
        raise
    except Exception:
        with anyio.CancelScope(shield=True):
            await session.rollback()
        raise
    else:
        await session.commit()
