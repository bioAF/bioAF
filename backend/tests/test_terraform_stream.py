"""Shared Terraform subprocess mechanics, exercised against real processes."""

import asyncio
import json
import sys
from contextlib import aclosing
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from app.services import terraform_stream
from app.services.terraform_stream import (
    OperationOutcome,
    StreamProgress,
    TerraformProgressEvent,
    diagnostics_message,
    owned_event_stream,
    read_exit,
    stop_process,
    stream_terraform_output,
)


async def _spawn(script):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def _emit(*lines, then="", exit_code=0):
    body = "".join(f"print({line!r})\n" for line in lines)
    return f"import sys, time\n{body}{then}\nsys.exit({exit_code})\n"


async def _classify_all(entry, _line):
    return TerraformProgressEvent(event_type="progress", message=entry.get("@message", ""))


def _stream(process, progress=None, classify=_classify_all, heartbeat="working"):
    return stream_terraform_output(
        process,
        heartbeat_message=heartbeat,
        progress=progress or StreamProgress(),
        classify=classify,
    )


@pytest.mark.asyncio
async def test_valid_and_malformed_output():
    process = await _spawn(
        _emit(
            json.dumps({"@message": "first"}),
            "not json",
            "[1, 2]",
            "",
            json.dumps({"@message": "second"}),
        )
    )
    progress = StreamProgress()

    events = [event async for event in _stream(process, progress)]

    assert [e.message for e in events] == ["first", "second"]
    assert progress.log_lines == [json.dumps({"@message": "first"}), "not json", "[1, 2]", json.dumps({"@message": "second"})]
    assert (await read_exit(process)).return_code == 0


@pytest.mark.asyncio
async def test_undecodable_output_does_not_end_the_stream():
    script = (
        "import sys\n"
        "sys.stdout.buffer.write(b'\\xff\\xfe not text\\n')\n"
        'sys.stdout.buffer.write(b\'{"@message": "after"}\\n\')\n'
    )
    process = await _spawn(script)

    events = [event async for event in _stream(process)]

    assert [e.message for e in events] == ["after"]


@pytest.mark.asyncio
async def test_heartbeat_carries_current_counts(monkeypatch):
    monkeypatch.setattr(terraform_stream, "HEARTBEAT_SECONDS", 0.05)
    process = await _spawn(_emit(then="time.sleep(0.3)"))
    progress = StreamProgress(resources_completed=2, resources_total=5)

    events = [event async for event in _stream(process, progress, heartbeat="still going")]

    assert events
    assert {e.event_type for e in events} == {"heartbeat"}
    assert {(e.message, e.resources_completed, e.resources_total) for e in events} == {("still going", 2, 5)}


@pytest.mark.asyncio
async def test_classifier_updates_resource_progress():
    process = await _spawn(_emit("{}", "{}"))
    progress = StreamProgress(resources_total=2)

    async def classify(_entry, _line):
        progress.resources_completed += 1
        return TerraformProgressEvent(
            event_type="resource_complete",
            resources_completed=progress.resources_completed,
            resources_total=progress.resources_total,
        )

    events = [event async for event in _stream(process, progress, classify)]

    assert [e.resources_completed for e in events] == [1, 2]


@pytest.mark.asyncio
async def test_classifier_may_drop_a_line():
    process = await _spawn(_emit('{"keep": false}', '{"keep": true, "@message": "kept"}'))

    async def classify(entry, line):
        return await _classify_all(entry, line) if entry["keep"] else None

    events = [event async for event in _stream(process, classify=classify)]

    assert [e.message for e in events] == ["kept"]


@pytest.mark.asyncio
async def test_nonzero_exit_is_reported_with_stderr():
    process = await _spawn(_emit(then="sys.stderr.write('bad things')", exit_code=3))

    _ = [event async for event in _stream(process)]
    result = await read_exit(process)

    assert (result.return_code, result.stderr) == (3, "bad things")


@pytest.mark.asyncio
async def test_classifier_exception_reaches_the_operation():
    process = await _spawn(_emit("{}", then="time.sleep(30)"))

    async def classify(_entry, _line):
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        async for _ in _stream(process, classify=classify):
            pass

    assert await stop_process(process) is True


@pytest.mark.asyncio
async def test_closing_the_stream_early_leaves_the_process_to_its_owner():
    process = await _spawn(_emit('{"@message": "one"}', then="time.sleep(30)"))

    async with aclosing(_stream(process)) as events:
        async for _ in events:
            break

    assert process.returncode is None
    assert await stop_process(process) is True
    assert process.returncode is not None


def _diagnostic(**fields):
    return json.dumps({"type": "diagnostic", "diagnostic": fields})


def test_diagnostics_prefer_detail_and_skip_noise():
    lines = [_diagnostic(summary="s", detail="d1"), "garbage", '"text"', _diagnostic(summary="s2"), "{}"]

    assert diagnostics_message(lines) == "d1; s2"
    assert diagnostics_message(["garbage"]) == ""


# -----------------------------------------------------------------------
# Stop routine
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_is_a_noop_for_an_exited_process():
    process = await _spawn("pass")
    await process.wait()

    assert await stop_process(process) is True


@pytest.mark.asyncio
async def test_stop_terminates_gracefully():
    process = await _spawn("import time; print('up', flush=True); time.sleep(30)")
    await process.stdout.readline()

    assert await stop_process(process) is True
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_stop_kills_a_process_that_ignores_sigterm(monkeypatch):
    monkeypatch.setattr(terraform_stream, "STOP_TERM_SECONDS", 0.1)
    process = await _spawn(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('up', flush=True)\n"
        "time.sleep(30)\n"
    )
    await process.stdout.readline()

    assert await stop_process(process) is True
    assert process.returncode == -9


def _unkillable():
    process = MagicMock()
    process.returncode = None

    async def wait():
        await asyncio.Event().wait()

    process.wait = wait
    return process


@pytest.mark.asyncio
async def test_stop_reports_unconfirmed_exit(monkeypatch):
    monkeypatch.setattr(terraform_stream, "STOP_TERM_SECONDS", 0.01)
    monkeypatch.setattr(terraform_stream, "STOP_KILL_SECONDS", 0.01)
    process = _unkillable()

    assert await stop_process(process) is False
    process.terminate.assert_called_once()
    process.kill.assert_called_once()


@pytest.mark.asyncio
async def test_stop_tolerates_a_process_that_already_vanished(monkeypatch):
    monkeypatch.setattr(terraform_stream, "STOP_TERM_SECONDS", 0.01)
    monkeypatch.setattr(terraform_stream, "STOP_KILL_SECONDS", 0.01)
    process = _unkillable()
    process.terminate.side_effect = ProcessLookupError

    assert await stop_process(process) is False


@pytest.mark.asyncio
async def test_stop_completes_inside_a_cancelled_scope():
    """A client disconnect cancels level-triggered: every await in the scope is
    cancelled again, so the stop has to shield itself to reach the exit."""
    process = await _spawn("import time; print('up', flush=True); time.sleep(30)")
    await process.stdout.readline()
    confirmed = None

    with anyio.CancelScope() as scope:
        scope.cancel()
        confirmed = await stop_process(process)

    assert confirmed is True
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_stop_survives_a_task_cancellation(monkeypatch):
    process = await _spawn("import time; print('up', flush=True); time.sleep(30)")
    await process.stdout.readline()
    task = asyncio.create_task(stop_process(process))
    await asyncio.sleep(0)

    task.cancel()
    await asyncio.wait([task], timeout=5)

    assert process.returncode is not None


# -----------------------------------------------------------------------
# Terminal outcome
# -----------------------------------------------------------------------


def _observe(*event_types):
    outcome = OperationOutcome()
    for event_type in event_types:
        outcome.observe(TerraformProgressEvent(event_type=event_type))
    return outcome


def test_outcome_requires_confirmed_success():
    assert _observe("apply_complete").succeeded
    assert not _observe().succeeded
    assert not _observe("progress", "heartbeat", "resource_complete").succeeded
    assert not _observe("interrupted").succeeded
    assert not _observe("apply_error").succeeded


def test_outcome_distinguishes_failure_from_absence_and_interruption():
    assert _observe("apply_error").failed
    assert not _observe("interrupted").failed
    assert not _observe().failed


# -----------------------------------------------------------------------
# Session ownership
# -----------------------------------------------------------------------


def _session():
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


async def _events(closed):
    try:
        yield TerraformProgressEvent(event_type="progress")
        yield TerraformProgressEvent(event_type="progress")
    finally:
        closed.append("closed")


@pytest.mark.asyncio
async def test_owner_commits_after_a_drained_stream():
    session, closed = _session(), []

    async with owned_event_stream(session, _events(closed)) as events:
        async for _ in events:
            pass

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    assert closed == ["closed"]


@pytest.mark.asyncio
async def test_owner_rolls_back_an_unexpected_error_after_closing():
    session, closed = _session(), []
    session.rollback.side_effect = lambda: closed.append("rollback")

    with pytest.raises(RuntimeError):
        async with owned_event_stream(session, _events(closed)) as events:
            async for _ in events:
                raise RuntimeError("unrelated failure")

    session.commit.assert_not_awaited()
    assert closed == ["closed", "rollback"]


@pytest.mark.asyncio
async def test_owner_commits_the_interruption_after_closing():
    session, closed = _session(), []
    session.commit.side_effect = lambda: closed.append("commit")

    with pytest.raises(asyncio.CancelledError):
        async with owned_event_stream(session, _events(closed)) as events:
            async for _ in events:
                raise asyncio.CancelledError

    assert closed == ["closed", "commit"]


@pytest.mark.asyncio
async def test_owner_rolls_back_when_the_interrupted_transaction_is_unusable():
    session, closed = _session(), []
    session.commit.side_effect = RuntimeError("transaction aborted")

    with pytest.raises(asyncio.CancelledError):
        async with owned_event_stream(session, _events(closed)) as events:
            async for _ in events:
                raise asyncio.CancelledError

    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_commits_inside_a_cancelled_scope():
    session, closed = _session(), []

    async def commit():
        await asyncio.sleep(0)
        closed.append("commit")

    session.commit.side_effect = commit

    with anyio.CancelScope() as scope:
        async with owned_event_stream(session, _events(closed)) as events:
            async for _ in events:
                scope.cancel()
                await asyncio.sleep(1)

    assert closed == ["closed", "commit"]
