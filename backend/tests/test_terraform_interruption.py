"""Interruption, closure, and stop ordering for the streaming Terraform operations.

The rule under test: Terraform has exited before its lock or work directory goes.
Persistence is checked under real commit and rollback, through a second session.
"""

import asyncio
import json
import tempfile
from contextlib import aclosing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.component import TerraformRun
from app.services import terraform_stream
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.terraform_executor import TerraformExecutor, _active_processes


class FakeProcess:
    """A process that stays alive until signalled."""

    def __init__(self, lines=(), *, term_exits=True, kill_exits=True, journal=None):
        self.returncode = None
        self.signals = []
        self.term_exits = term_exits
        self.kill_exits = kill_exits
        self.journal = journal if journal is not None else []
        self._lines = [f"{line}\n".encode() for line in lines]
        self._exited = asyncio.Event()
        self.reading = asyncio.Event()
        self.stdout = MagicMock()
        self.stdout.readline = self._readline
        self.stderr = MagicMock()
        self.stderr.read = AsyncMock(return_value=b"")

    async def _readline(self):
        if self._lines:
            return self._lines.pop(0)
        self.reading.set()
        await self._exited.wait()
        return b""

    async def wait(self):
        await self._exited.wait()
        return self.returncode

    def exit(self, code):
        self.returncode = code
        self._exited.set()

    def terminate(self):
        self.signals.append("term")
        if self.term_exits:
            self.journal.append("exited")
            self.exit(-15)

    def kill(self):
        self.signals.append("kill")
        if self.kill_exits:
            self.journal.append("exited")
            self.exit(-9)


def _hook(addr):
    return json.dumps({"type": "apply_complete", "hook": {"resource": {"addr": addr}}})


@pytest_asyncio.fixture
async def user_id(session):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="InterruptOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="interrupt@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return user.id


async def _seed_config(session, initialized=True):
    for key, value in {
        "gcp_credentials_configured": "true",
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "proj",
        "gcp_region": "us-central1",
        "terraform_initialized": "true" if initialized else "false",
        "terraform_state_bucket": "bioaf-tfstate",
    }.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


@pytest.fixture
def harness(monkeypatch):
    """Patch process creation and terraform calls; journal lock deletion."""

    class Harness:
        journal: list = []
        work_dir = Path(tempfile.mkdtemp(prefix="tf_interrupt_"))

        def __init__(self):
            self.journal = []
            self.process = None
            self.create = AsyncMock(side_effect=lambda *a, **k: self.process)

        def fake(self, *args, **kwargs):
            self.process = FakeProcess(*args, journal=self.journal, **kwargs)
            return self.process

    h = Harness()
    completed = MagicMock(returncode=0, stdout=json.dumps({"resource_changes": []}), stderr="")

    async def delete_lock(*_args, **_kwargs):
        h.journal.append("lock_deleted")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", h.create)
    monkeypatch.setattr("app.services.terraform_executor.subprocess.run", lambda *a, **k: completed)
    monkeypatch.setattr(TerraformExecutor, "_prepare_work_dir", staticmethod(lambda *a: h.work_dir))
    monkeypatch.setattr(TerraformExecutor, "_delete_gcs_lock_file", staticmethod(delete_lock))
    monkeypatch.setattr(TerraformExecutor, "_load_gcs_credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(terraform_stream, "STOP_TERM_SECONDS", 0.05)
    monkeypatch.setattr(terraform_stream, "STOP_KILL_SECONDS", 0.05)
    yield h
    _active_processes.clear()


async def _approved_run(session, user_id, module="storage"):
    run = TerraformRun(
        triggered_by_user_id=user_id,
        action="plan",
        module_name=module,
        status="awaiting_confirmation",
        resources_planned=2,
        plan_json={"resources": [{"address": "a.one"}]},
    )
    session.add(run)
    await session.commit()
    return run


async def _operation(session, user_id, name):
    """Start an operation; returns (generator, run lookup)."""
    await _seed_config(session)
    if name == "apply":
        run = await _approved_run(session, user_id)
        return TerraformExecutor.run_apply(session, run.id, user_id)
    return TerraformExecutor.run_destroy(session, user_id, "storage")


async def _run_row(session):
    return (await session.execute(text("SELECT id, status, error_message, apply_log FROM terraform_runs"))).fetchone()


async def _committed_status(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        row = (await other.execute(text("SELECT status FROM terraform_runs"))).fetchone()
    return row[0] if row else None


async def _until_process_reads(harness, task):
    while not harness.process or not harness.process.reading.is_set():
        await asyncio.sleep(0)
        assert not task.done(), "operation ended before reaching the process"


async def _collect(gen, into):
    async for event in gen:
        into.append(event)


OPERATIONS = ["apply", "destroy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_closing_early_stops_terraform_then_releases_the_lock(session, user_id, harness, db_engine, operation):
    harness.fake([_hook("a.one")])
    gen = await _operation(session, user_id, operation)

    async with aclosing(gen) as events:
        async for event in events:
            if event.event_type == "resource_complete":
                break

    assert harness.process.signals == ["term"]
    assert harness.journal == ["exited", "lock_deleted"]
    row = await _run_row(session)
    assert row.status == "failed"
    assert row.error_message == "Operation cancelled (client disconnected)"
    assert _hook("a.one") in row.apply_log
    assert row.id not in _active_processes
    # Flushed, not committed: the outcome belongs to the session owner.
    assert await _committed_status(db_engine) != "failed"
    await session.commit()
    assert await _committed_status(db_engine) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_task_cancellation_produces_an_interrupted_outcome(session, user_id, harness, operation):
    harness.fake()
    gen = await _operation(session, user_id, operation)
    events = []

    with patch("app.services.terraform_executor.event_bus.emit", AsyncMock()) as emit:
        task = asyncio.create_task(_collect(gen, events))
        await _until_process_reads(harness, task)
        task.cancel()
        await task

    assert events[-1].event_type == "interrupted"
    assert harness.journal == ["exited", "lock_deleted"]
    assert (await _run_row(session)).status == "failed"
    emit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_cancelled_scope_still_completes_cleanup(session, user_id, harness, operation):
    harness.fake()
    gen = await _operation(session, user_id, operation)
    events = []

    async def drain(scope_holder):
        with anyio.CancelScope() as scope:
            scope_holder.append(scope)
            await _collect(gen, events)

    holder = []
    task = asyncio.create_task(drain(holder))
    await _until_process_reads(harness, task)
    holder[0].cancel()
    await task

    assert events[-1].event_type == "interrupted"
    assert harness.journal == ["exited", "lock_deleted"]
    assert (await _run_row(session)).status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_exception_path_stops_terraform_before_the_lock(session, user_id, harness, operation):
    process = harness.fake()

    async def explode():
        raise RuntimeError("stream broke")

    process.stdout.readline = explode
    gen = await _operation(session, user_id, operation)

    events = [event async for event in gen]

    assert events[-1].event_type == "apply_error"
    assert events[-1].message == "stream broke"
    assert harness.journal == ["exited", "lock_deleted"]
    assert (await _run_row(session)).status == "failed"


@pytest.mark.asyncio
async def test_exception_path_reports_the_failure_only_after_terraform_stopped(session, user_id, harness):
    process = harness.fake()

    async def explode():
        raise RuntimeError("stream broke")

    process.stdout.readline = explode
    gen = await _operation(session, user_id, "apply")

    async for event in gen:
        if event.event_type == "apply_error":
            assert process.returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_unconfirmed_exit_keeps_lock_and_registry_entry(session, user_id, harness, operation):
    harness.fake([_hook("a.one")], term_exits=False, kill_exits=False)
    gen = await _operation(session, user_id, operation)

    async with aclosing(gen) as events:
        async for event in events:
            if event.event_type == "resource_complete":
                break

    assert harness.process.signals == ["term", "kill"]
    assert harness.journal == []
    row = await _run_row(session)
    assert row.status == "failed"
    assert row.id in _active_processes


@pytest.mark.asyncio
async def test_sigkill_fallback_confirms_exit(session, user_id, harness):
    harness.fake(term_exits=False)
    gen = await _operation(session, user_id, "apply")
    events = []

    task = asyncio.create_task(_collect(gen, events))
    await _until_process_reads(harness, task)
    task.cancel()
    await task

    assert harness.process.signals == ["term", "kill"]
    assert harness.journal == ["exited", "lock_deleted"]


@pytest.mark.asyncio
async def test_closing_after_success_keeps_the_completed_outcome(session, user_id, harness):
    process = harness.fake()
    process.exit(0)
    gen = await _operation(session, user_id, "apply")

    async with aclosing(gen) as events:
        async for event in events:
            if event.event_type == "apply_complete":
                break

    assert (await _run_row(session)).status == "completed"
    assert harness.journal == []


@pytest.mark.asyncio
async def test_closing_before_the_process_started_marks_the_run(session, user_id, harness):
    harness.fake()
    gen = await _operation(session, user_id, "apply")

    async with aclosing(gen) as events:
        async for event in events:
            assert event.event_type == "planned_resources"
            break

    assert harness.create.await_count == 0
    assert (await _run_row(session)).status == "failed"


@pytest.mark.asyncio
async def test_provider_setup_failure_still_raises_to_the_caller(session, user_id, harness):
    harness.fake()
    gen = await _operation(session, user_id, "apply")

    with patch(
        "app.services.terraform_cloud.GcpTerraformCloud.build_provider_env",
        AsyncMock(side_effect=RuntimeError("no credentials")),
    ):
        with pytest.raises(RuntimeError, match="no credentials"):
            async for _ in gen:
                pass


# -----------------------------------------------------------------------
# Bootstrap
# -----------------------------------------------------------------------


@pytest.fixture
def bootstrap_harness(harness):
    harness.work_dir = Path(tempfile.mkdtemp(prefix="tf_bootstrap_"))
    return harness


@pytest.mark.asyncio
async def test_bootstrap_cancellation_terminates_terraform(session, user_id, bootstrap_harness):
    harness = bootstrap_harness
    await _seed_config(session, initialized=False)
    harness.fake()
    events = []

    task = asyncio.create_task(_collect(TerraformExecutor.bootstrap_foundation(session, user_id), events))
    await _until_process_reads(harness, task)
    task.cancel()
    await task

    assert harness.process.signals == ["term"]
    assert events[-1].event_type == "interrupted"
    assert (await _run_row(session)).status == "failed"
    assert not harness.work_dir.exists()


@pytest.mark.asyncio
async def test_bootstrap_closure_terminates_terraform(session, user_id, bootstrap_harness):
    harness = bootstrap_harness
    await _seed_config(session, initialized=False)
    harness.fake([_hook("a.one")])

    async with aclosing(TerraformExecutor.bootstrap_foundation(session, user_id)) as events:
        async for event in events:
            if event.event_type == "resource_complete":
                break

    assert harness.process.signals == ["term"]
    assert (await _run_row(session)).status == "failed"
    assert not harness.work_dir.exists()


@pytest.mark.asyncio
async def test_bootstrap_interruption_before_the_run_exists(session, user_id, bootstrap_harness):
    harness = bootstrap_harness
    await _seed_config(session, initialized=False)
    planning = asyncio.Event()

    async def blocked_plan(*_args):
        planning.set()
        await asyncio.Event().wait()

    events = []
    with patch.object(TerraformExecutor, "_run_plan_capture", blocked_plan):
        task = asyncio.create_task(_collect(TerraformExecutor.bootstrap_foundation(session, user_id), events))
        await planning.wait()
        task.cancel()
        await task

    assert events[-1].event_type == "interrupted"
    assert await _run_row(session) is None
    assert not harness.work_dir.exists()


@pytest.mark.asyncio
async def test_bootstrap_unconfirmed_exit_keeps_the_work_dir(session, user_id, bootstrap_harness):
    harness = bootstrap_harness
    await _seed_config(session, initialized=False)
    harness.fake([_hook("a.one")], term_exits=False, kill_exits=False)

    async with aclosing(TerraformExecutor.bootstrap_foundation(session, user_id)) as events:
        async for event in events:
            if event.event_type == "resource_complete":
                break

    row = await _run_row(session)
    assert row.status == "failed"
    assert row.id in _active_processes
    assert harness.work_dir.exists()


# -----------------------------------------------------------------------
# abandon_run and the safety ceiling
# -----------------------------------------------------------------------


async def _active_run(session, user_id, process, started_at=None):
    run = TerraformRun(
        triggered_by_user_id=user_id,
        action="apply",
        module_name="storage",
        status="applying",
    )
    session.add(run)
    await session.flush()
    if started_at:
        run.started_at = started_at
    await session.commit()
    _active_processes[run.id] = process
    return run


@pytest.mark.asyncio
async def test_abandon_stops_terraform_before_deleting_the_lock(session, user_id, harness):
    await _seed_config(session)
    process = harness.fake()
    run = await _active_run(session, user_id, process)

    result = await TerraformExecutor.abandon_run(session, run.id, user_id)

    assert harness.journal == ["exited", "lock_deleted"]
    assert result.status == "cancelled"
    assert run.id not in _active_processes


@pytest.mark.asyncio
async def test_abandon_with_unconfirmed_exit_keeps_the_lock(session, user_id, harness):
    await _seed_config(session)
    process = harness.fake(term_exits=False, kill_exits=False)
    run = await _active_run(session, user_id, process)

    result = await TerraformExecutor.abandon_run(session, run.id, user_id)

    assert harness.journal == []
    assert result.status == "failed"
    assert _active_processes[run.id] is process


@pytest.mark.asyncio
async def test_safety_ceiling_stops_terraform_before_deleting_the_lock(session, user_id, harness):
    await _seed_config(session)
    process = harness.fake()
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    run = await _active_run(session, user_id, process, started_at=old)

    await TerraformExecutor._recover_stale_runs(session)

    assert harness.journal == ["exited", "lock_deleted"]
    assert (await _run_row(session)).status == "failed"
    assert run.id not in _active_processes


@pytest.mark.asyncio
async def test_safety_ceiling_with_unconfirmed_exit_keeps_the_lock(session, user_id, harness):
    await _seed_config(session)
    process = harness.fake(term_exits=False, kill_exits=False)
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    run = await _active_run(session, user_id, process, started_at=old)

    await TerraformExecutor._recover_stale_runs(session)

    assert harness.journal == []
    assert (await _run_row(session)).status == "failed"
    assert _active_processes[run.id] is process
