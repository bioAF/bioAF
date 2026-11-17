"""The register-outputs command's event lifecycle (01C.2).

The command runs in its own process with its own engine and event bus, so the server's dispatcher
binding does not exist there. These tests drive the real command entry (``_main``) with a fresh
event bus, a separate engine on the test database, and a deliberately broken application-global
session factory, so a dispatcher that silently used the global factory could not pass. They check
that recovered files and their delivery rows are committed before the command exits, that failures
are reported with a nonzero status, and that nothing leaks across invocations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.adapters.storage.s3 import S3StorageProvider
from app.cli import register_outputs as cli
from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import file_verification_worker, webhook_dispatcher
from app.services.event_bus import event_bus
from app.services.event_types import ALL_WEBHOOK_EVENT_TYPES

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.asyncio

DATA = b"ACGT" * 256
MD5 = hashlib.md5(DATA).hexdigest()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3 as module

    monkeypatch.setattr(module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = S3StorageProvider(org_slug="t")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    monkeypatch.setattr(cli, "get_storage_adapter", lambda: adapter)

    async def no_init(*a, **k):
        return None

    monkeypatch.setattr(cli, "initialize_adapters", no_init)
    return adapter


@pytest_asyncio.fixture
async def command_env(session, monkeypatch):
    """The command's own database binding, a fresh event bus, and a broken global factory."""
    schema = (await session.execute(text("SHOW search_path"))).scalar_one()
    monkeypatch.setattr(cli, "_engine_options", lambda: {"connect_args": {"server_settings": {"search_path": schema}}})
    monkeypatch.delenv("BIOAF_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    import app.database as database_module

    def broken_global_factory(*a, **k):
        raise AssertionError("the command must use its own session factory, not the application's")

    original_bus = dict(event_bus._subscribers)
    event_bus._subscribers.clear()
    monkeypatch.setattr(webhook_dispatcher, "_server_binding", None)
    real_factory = database_module.async_session_factory
    monkeypatch.setattr(database_module, "async_session_factory", broken_global_factory)
    yield real_factory
    event_bus._subscribers = original_bus


async def _subscribe(session, org_id, user_id, *, active=True, events=None):
    session.add(
        WebhookSubscription(
            organization_id=org_id,
            name="LIMS",
            url="https://hooks.example/h",
            secret="whsec_x",
            events=events or list(ALL_WEBHOOK_EVENT_TYPES),
            created_by_user_id=user_id,
            is_active=active,
        )
    )
    await session.commit()


async def _run(session, org_id, *, status="completed", pipeline="nf-core/rnaseq", output_uri=True):
    exp = Experiment(organization_id=org_id, name="E", status="analysis")
    session.add(exp)
    await session.flush()
    run = PipelineRun(organization_id=org_id, experiment_id=exp.id, pipeline_name=pipeline, status=status)
    session.add(run)
    await session.commit()
    return run


def _outputs(monkeypatch, store, run, names, *, data=DATA, refuse=False):
    from app.services.run_output_location_service import OutputLocationRefused

    outdir = f"s3://bioaf-results-t/runs/{run.id}"

    async def authorized(_session, r, _adapter):
        if refuse:
            raise OutputLocationRefused("not this run's location")
        return f"s3://bioaf-results-t/runs/{r.id}"

    monkeypatch.setattr(cli.RunOutputLocationService, "authorized_uri", authorized)
    return outdir


async def _write(store, outdir, names, data=DATA):
    for name in names:
        await store.write_bytes(f"{outdir}/{name}", data)


async def _deliveries(factory, org_id=None):
    async with factory() as s:
        q = select(WebhookDelivery).join(WebhookSubscription)
        if org_id is not None:
            q = q.where(WebhookSubscription.organization_id == org_id)
        return [(d.event_type, d.payload_json["data"]) for d in (await s.execute(q)).scalars()]


def _args(run_id=None, force=False):
    return argparse.Namespace(run_id=run_id, force=force)


async def test_a_recovered_file_and_its_deliveries_survive_the_command(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    real_factory = command_env
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id, status="failed")  # an old failed run still recovers
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt", "b.txt"])

    code = await cli._main(_args(run_id=run.id))

    out = capsys.readouterr().out
    assert code == 0, out
    rows = await _deliveries(real_factory)
    assert sorted(e for e, _ in rows) == ["file.ready", "file.ready", "file.registered", "file.registered"]
    assert "New files registered: 2 (ready: 2, waiting for checksum verification: 0)" in out
    assert "Existing files made ready: 0" in out and "Webhook deliveries recorded: 4" in out
    # Nothing is left subscribed after the command.
    assert not any(event_bus._subscribers.get(e) for e in webhook_dispatcher.INTERNAL_EVENTS)


async def test_rerunning_with_force_announces_nothing_new(session, admin_user, store, command_env, monkeypatch, capsys):
    real_factory = command_env
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt"])

    assert await cli._main(_args(run_id=run.id)) == 0
    assert await cli._main(_args(force=True)) == 0
    assert await cli._main(_args()) == 0
    assert len(await _deliveries(real_factory)) == 2
    async with real_factory() as s:
        assert len((await s.execute(select(File))).scalars().all()) == 1


async def test_no_matching_subscription_is_a_success(session, admin_user, store, command_env, monkeypatch, capsys):
    await _subscribe(session, admin_user.organization_id, admin_user.id, active=False)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt"])

    assert await cli._main(_args(run_id=run.id)) == 0
    assert "no matching webhook subscription" in capsys.readouterr().out
    assert await _deliveries(command_env) == []


async def test_zero_files_and_unauthorized_locations(session, admin_user, store, command_env, monkeypatch, capsys):
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    _outputs(monkeypatch, store, run, [])
    assert await cli._main(_args(run_id=run.id)) == 0
    outdir = _outputs(monkeypatch, store, run, ["a.txt"], refuse=True)
    await _write(store, outdir, ["a.txt"])
    assert await cli._main(_args(run_id=run.id)) == 0
    assert await _deliveries(command_env) == []


async def test_events_reach_only_the_runs_organization(session, admin_user, store, command_env, monkeypatch):
    other = Organization(name="Other")
    session.add(other)
    await session.commit()
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    await _subscribe(session, other.id, admin_user.id)
    run = await _run(session, other.id)
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt"])

    assert await cli._main(_args(run_id=run.id)) == 0
    assert await _deliveries(command_env, admin_user.organization_id) == []
    assert len(await _deliveries(command_env, other.id)) == 2


async def test_a_dispatch_failure_after_commit_fails_the_command_truthfully(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt"])

    async def broken(*a, **k):
        raise RuntimeError("insert failed: secret detail")

    monkeypatch.setattr(webhook_dispatcher, "dispatch_event", broken)
    code = await cli._main(_args(run_id=run.id))
    out = capsys.readouterr().out
    assert code != 0
    assert "already committed" in out and "file.registered" in out
    assert "secret detail" not in out
    async with command_env() as s:
        f = (await s.execute(select(File))).scalar_one()
    assert str(f.id) in out
    # Readiness is a data fact; a notification failure does not undo it.
    assert f.ready_at is not None


async def test_a_slow_dispatch_hits_the_drain_deadline(session, admin_user, store, command_env, monkeypatch, capsys):
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt"])
    monkeypatch.setattr(cli, "DRAIN_SECONDS", 0.2)

    async def slow(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(webhook_dispatcher, "dispatch_event", slow)
    code = await cli._main(_args(run_id=run.id))
    out = capsys.readouterr().out
    assert code != 0 and "did not finish" in out


async def test_a_failed_registration_commits_and_announces_nothing(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["a.txt"])
    await _write(store, outdir, ["a.txt"])

    async def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(cli.PipelineOutputService, "register_outputs", boom)
    code = await cli._main(_args(run_id=run.id))
    assert code != 0
    assert await _deliveries(command_env) == []


async def test_pending_verification_is_handed_to_the_server_worker(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    real_factory = command_env
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id, pipeline="nf-core/fetchngs")
    outdir = _outputs(monkeypatch, store, run, ["fastq/SRR1_1.fastq.gz"])
    await _write(store, outdir, ["fastq/SRR1_1.fastq.gz"])
    await store.write_text(
        f"{outdir}/samplesheet/samplesheet.csv", f"sample,fastq_1,md5_1\nS1,/w/SRR1_1.fastq.gz,{MD5}\n"
    )

    assert await cli._main(_args(run_id=run.id)) == 0
    out = capsys.readouterr().out
    assert "waiting for checksum verification: 1" in out
    async with real_factory() as s:
        fastq = (await s.execute(select(File).where(File.filename == "SRR1_1.fastq.gz"))).scalar_one()

    def fastq_events(rows):
        return sorted(e for e, d in rows if d["file_id"] == fastq.id)

    # The FASTQ is registered and waits for its samplesheet checksum; the samplesheet itself is an
    # ordinary output and is ready at once.
    assert fastq_events(await _deliveries(real_factory)) == ["file.registered"]

    # Later, the serving process's worker verifies the same record and produces one ready event.
    import app.database as database_module

    monkeypatch.setattr(database_module, "async_session_factory", real_factory)
    event_bus._subscribers.clear()
    binding = webhook_dispatcher.subscribe_all(session_factory=real_factory, collect_receipts=True)
    try:
        while await file_verification_worker.run_once():
            pass
        from app.services.post_commit_events import wait_for_deferred_events

        await wait_for_deferred_events()
    finally:
        binding.close()
    assert fastq_events(await _deliveries(real_factory)) == ["file.ready", "file.registered"]


@pytest.mark.parametrize("variable", ["BIOAF_DATABASE_URL", "DATABASE_URL"])
async def test_both_database_variables_work(session, admin_user, store, command_env, monkeypatch, variable):
    monkeypatch.delenv("BIOAF_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(variable, TEST_DATABASE_URL)
    run = await _run(session, admin_user.organization_id)
    _outputs(monkeypatch, store, run, [])
    assert await cli._main(_args(run_id=run.id)) == 0


async def test_a_missing_database_url_is_an_error(monkeypatch):
    monkeypatch.delenv("BIOAF_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert await cli._main(_args()) != 0


async def test_ambiguous_legacy_outputs_fail_the_command_by_name(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    """A collected output matching two unreconciled legacy Files is a structured, nonzero refusal."""
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["dup.txt", "ok.txt"])
    await _write(store, outdir, ["dup.txt", "ok.txt"])
    for _ in range(2):
        session.add(
            File(
                organization_id=admin_user.organization_id,
                storage_uri=f"{outdir}/dup.txt",
                filename="dup.txt",
                file_type="other",
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
            )
        )
    await session.commit()

    code = await cli._main(_args(run_id=run.id))
    out = capsys.readouterr().out
    assert code == 1, out
    assert "source_identity_ambiguous" in out and f"{outdir}/dup.txt" in out
    async with command_env() as s:
        assert len((await s.execute(select(File).where(File.filename == "ok.txt"))).scalars().all()) == 1
        assert len((await s.execute(select(File).where(File.filename == "dup.txt"))).scalars().all()) == 2


async def test_existing_files_made_ready_are_counted_truthfully(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    """An older output the command brings to ready is reported as such, not as zero."""
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["old.txt"])
    await _write(store, outdir, ["old.txt"])
    session.add(
        File(
            organization_id=admin_user.organization_id,
            storage_uri=f"{outdir}/old.txt",
            source_storage_uri=f"{outdir}/old.txt",
            filename="old.txt",
            file_type="other",
            source_type="pipeline_output",
            source_pipeline_run_id=run.id,
        )
    )
    await session.commit()

    assert await cli._main(_args(run_id=run.id, force=True)) == 0
    out = capsys.readouterr().out
    assert "New files registered: 0" in out
    assert "Existing files made ready: 1" in out
    assert "Webhook deliveries recorded: 1" in out
    assert [e for e, _ in await _deliveries(command_env)] == ["file.ready"]


async def test_a_notification_failure_names_existing_files_made_ready(
    session, admin_user, store, command_env, monkeypatch, capsys
):
    """An older output made ready whose file.ready could not be recorded is named for reconciliation."""
    await _subscribe(session, admin_user.organization_id, admin_user.id)
    run = await _run(session, admin_user.organization_id)
    outdir = _outputs(monkeypatch, store, run, ["old.txt"])
    await _write(store, outdir, ["old.txt"])
    old = File(
        organization_id=admin_user.organization_id,
        storage_uri=f"{outdir}/old.txt",
        source_storage_uri=f"{outdir}/old.txt",
        filename="old.txt",
        file_type="other",
        source_type="pipeline_output",
        source_pipeline_run_id=run.id,
    )
    session.add(old)
    await session.commit()
    old_id = old.id
    monkeypatch.setattr(cli, "DRAIN_SECONDS", 0.2)

    async def slow(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(webhook_dispatcher, "dispatch_event", slow)
    code = await cli._main(_args(run_id=run.id, force=True))
    out = capsys.readouterr().out
    assert code != 0, out
    assert "Existing files made ready: 1" in out
    assert f"Affected file IDs: {old_id}." in out
    assert "Affected file IDs: none" not in out
