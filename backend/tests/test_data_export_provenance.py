"""Requested provenance reports in exports: bounded before work, rendered off the loop, owned (plan 24)."""

from __future__ import annotations

import asyncio
import io
import threading
import time
import zipfile

import pytest
from sqlalchemy import text

from app.services.data_export import resources
from app.services.provenance.report_service import ProvenanceReportService
from app.services.provenance.schema import ReportOutput


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.adapters.storage import gcs
    from app.config import settings

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path / "scratch"))
    monkeypatch.setattr(
        resources,
        "disk_probe",
        lambda path: lambda: resources.DiskSpace(free=500 * resources.GiB, total=1000 * resources.GiB),
    )
    return tmp_path / "scratch"


@pytest.fixture(autouse=True)
def _slot_is_free_after_each_test():
    yield
    assert not resources.SLOT.busy


@pytest.fixture
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _seed(session, org: int, *, samples: int = 0, files: int = 0) -> None:
    await session.execute(
        text("INSERT INTO projects (id, organization_id, name, status) VALUES (1, :o, 'P', 'active')"), {"o": org}
    )
    await session.execute(
        text("INSERT INTO experiments (id, organization_id, project_id, name, status) VALUES (1, :o, 1, 'E', 'x')"),
        {"o": org},
    )
    await session.execute(
        text(
            "INSERT INTO samples (experiment_id, external_id, status, organism) "
            "SELECT 1, 'S'||g, 'registered', 'Homo sapiens' FROM generate_series(1, :n) g"
        ),
        {"n": samples},
    )
    await session.execute(
        text(
            "INSERT INTO files (organization_id, filename, gcs_uri, storage_uri, file_type, size_bytes, experiment_id,"
            " source_type, storage_deleted) SELECT :o, 'f'||g||'.csv', 'gs://b/f'||g, 'gs://b/f'||g, 'csv', 1, 1,"
            " 'upload', true FROM generate_series(1, :n) g"
        ),
        {"o": org, "n": files},
    )
    await session.commit()


def _post(client, headers, path="/api/experiments/1/export/data"):
    return client.post(
        path,
        json={"delivery_method": "direct", "include_fastq": False, "include_provenance": True},
        headers=headers,
    )


def _spy(monkeypatch, name: str) -> list:
    calls = []
    real = getattr(ProvenanceReportService, name)

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ProvenanceReportService, name, spy)
    return calls


async def _until(predicate, seconds: float = 5) -> None:
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    assert predicate()


@pytest.mark.parametrize("path", ["/api/experiments/1/export/data", "/api/projects/1/export/data"])
@pytest.mark.asyncio
async def test_too_many_records_are_refused_before_they_are_read(
    client, session, admin_user, headers, store, monkeypatch, path
):
    from sqlalchemy import event

    monkeypatch.setattr(resources, "PROVENANCE_MAX_ROWS", 3)
    rendered = _spy(monkeypatch, "render")
    await _seed(session, admin_user.organization_id, samples=4)
    materialized = []

    def record(conn, cursor, statement, params, context, executemany):
        # The sample manifest streams samples ordered by id (bounded on its own); the report reads do not.
        if statement.lstrip().upper().startswith("SELECT SAMPLES.") and "ORDER BY samples.id" not in statement:
            materialized.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        resp = await _post(client, headers, path)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert resp.status_code == 413
    assert "provenance report" in resp.json()["detail"]
    assert "without the provenance report" in resp.json()["detail"]
    assert rendered == []
    assert materialized == []


@pytest.mark.asyncio
async def test_a_report_too_large_to_render_is_refused_before_rendering(
    client, session, admin_user, headers, store, monkeypatch
):
    monkeypatch.setattr(resources, "PROVENANCE_MAX_REPORT_BYTES", 2_000)
    rendered = _spy(monkeypatch, "render")
    await _seed(session, admin_user.organization_id, samples=50)

    resp = await _post(client, headers)

    assert resp.status_code == 413
    assert "KiB" in resp.json()["detail"]
    assert rendered == []


@pytest.mark.asyncio
async def test_the_measured_envelope_admits_a_typical_experiment_and_refuses_a_large_one(
    client, session, admin_user, headers, store
):
    """Defaults: ~300 samples renders; 1,000 samples and 1,000 files (~700 KiB of report) does not."""
    await _seed(session, admin_user.organization_id, samples=300)

    ok = await _post(client, headers)

    assert ok.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(ok.content))
    assert any("/provenance/" in n for n in zf.namelist())

    await session.execute(
        text(
            "INSERT INTO samples (experiment_id, external_id, status, organism) "
            "SELECT 1, 'T'||g, 'registered', 'Homo sapiens' FROM generate_series(1, 700) g"
        )
    )
    await session.execute(
        text(
            "INSERT INTO files (organization_id, filename, gcs_uri, storage_uri, file_type, size_bytes, experiment_id,"
            " source_type, deleted_at) SELECT :o, 'f'||g||'.csv', 'gs://b/f'||g, 'gs://b/f'||g, 'csv', 1, 1,"
            " 'upload', now() FROM generate_series(1, 1000) g"
        ),
        {"o": admin_user.organization_id},
    )
    await session.commit()

    refused = await _post(client, headers)

    assert refused.status_code == 413


@pytest.mark.asyncio
async def test_rendering_runs_off_the_event_loop(client, session, admin_user, headers, store, monkeypatch):
    def slow_render(entity_type, entity_id, json_report, format="json"):
        time.sleep(0.4)  # the PDF renderer is pure CPU
        return ReportOutput(content=b"pdf", content_type="application/zip", filename="r.zip")

    monkeypatch.setattr(ProvenanceReportService, "render", staticmethod(slow_render))
    await _seed(session, admin_user.organization_id, samples=2)
    delays = []

    async def heartbeat():
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            delays.append(time.perf_counter() - t - 0.01)

    assert (await _post(client, headers)).status_code == 200  # warm: first-request imports and caches
    beat = asyncio.create_task(heartbeat())
    resp = await _post(client, headers)
    await asyncio.sleep(0.05)
    beat.cancel()

    assert resp.status_code == 200
    assert max(delays) < 0.1


def _blocking_render(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def render(entity_type, entity_id, json_report, format="json"):
        started.set()
        release.wait(10)
        return ReportOutput(content=b"pdf", content_type="application/zip", filename="r.zip")

    monkeypatch.setattr(ProvenanceReportService, "render", staticmethod(render))
    return started, release


def _prepare(session, org):
    from app.services import export_service

    return export_service.prepare_experiment_export(
        session, 1, org, include_fastq=False, include_provenance=True, user_email="a@b"
    )


@pytest.mark.asyncio
async def test_a_cancelled_export_keeps_the_slot_until_rendering_stops(session, admin_user, store, monkeypatch):
    monkeypatch.setattr(resources, "SETTLE_SECONDS", 0.1)  # the request stops waiting; the render does not
    started, release = _blocking_render(monkeypatch)
    await _seed(session, admin_user.organization_id, samples=2)

    task = asyncio.create_task(_prepare(session, admin_user.organization_id))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert resources.SLOT.busy and any(store.iterdir())  # the render thread still owns them
    release.set()
    await _until(lambda: not resources.SLOT.busy)
    assert not any(store.iterdir())


@pytest.mark.asyncio
async def test_a_render_past_the_deadline_fails_and_keeps_the_slot_until_it_stops(
    session, admin_user, store, monkeypatch
):
    from app.exceptions import ExportTimeoutError

    monkeypatch.setattr(resources, "BUILD_DEADLINE", 0.5)
    monkeypatch.setattr(resources, "SETTLE_SECONDS", 0.05)
    started, release = _blocking_render(monkeypatch)
    await _seed(session, admin_user.organization_id, samples=2)

    with pytest.raises(ExportTimeoutError):
        await _prepare(session, admin_user.organization_id)

    assert resources.SLOT.busy
    release.set()
    await _until(lambda: not resources.SLOT.busy)
    assert not any(store.iterdir())


@pytest.mark.asyncio
async def test_a_render_failure_fails_the_export(client, session, admin_user, headers, store, monkeypatch):
    def broken(entity_type, entity_id, json_report, format="json"):
        raise MemoryError("pdf")

    monkeypatch.setattr(ProvenanceReportService, "render", staticmethod(broken))
    await _seed(session, admin_user.organization_id, samples=2)

    resp = await _post(client, headers)

    assert resp.status_code == 409
    assert resp.json()["code"] == "export_content_unavailable"


# -- Every gathered collection is bounded before it is read (fifth review) ------------------------


def _rss_mib() -> int:
    import os
    import subprocess

    return (
        int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout) // 1024
    )


async def _heavy_export(client, headers, path="/api/experiments/1/export/data"):
    """Export with provenance while sampling RSS and a 10 ms heartbeat; returns (resp, growth MiB, max delay s)."""
    idle = _rss_mib()
    peak = {"v": idle}
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak["v"] = max(peak["v"], _rss_mib())
            time.sleep(0.02)

    threading.Thread(target=sampler, daemon=True).start()
    delays = []

    async def heartbeat():
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            delays.append(time.perf_counter() - t - 0.01)

    beat = asyncio.create_task(heartbeat())
    resp = await _post(client, headers, path)
    await asyncio.sleep(0.05)
    beat.cancel()
    stop.set()
    return resp, peak["v"] - idle, max(delays)


@pytest.mark.asyncio
async def test_a_huge_audit_history_is_refused_without_loading_it(client, session, admin_user, headers, store):
    await _seed(session, admin_user.organization_id)
    assert (await _post(client, headers)).status_code == 200  # warm
    await session.execute(
        text(
            "INSERT INTO audit_log (entity_type, entity_id, action, details_json) "
            "SELECT 'experiment', 1, 'updated', jsonb_build_object('note', repeat('x', 200)) "
            "FROM generate_series(1, 100000)"
        )
    )
    await session.commit()

    resp, growth, delay = await _heavy_export(client, headers)

    assert resp.status_code == 413
    assert "provenance report" in resp.json()["detail"]
    assert growth < 64
    assert delay < 0.1


@pytest.mark.asyncio
async def test_one_huge_payload_is_refused_by_width_not_count(client, session, admin_user, headers, store):
    await _seed(session, admin_user.organization_id)
    await session.execute(
        text(
            "INSERT INTO audit_log (entity_type, entity_id, action, details_json) "
            "VALUES ('experiment', 1, 'updated', jsonb_build_object('blob', repeat('y', 20000000)))"
        )
    )
    await session.commit()

    resp, growth, _ = await _heavy_export(client, headers)

    assert resp.status_code == 413
    assert growth < 64


@pytest.mark.asyncio
async def test_many_pipeline_processes_are_refused_before_loading(
    client, session, admin_user, headers, store, monkeypatch
):

    from sqlalchemy import event

    rendered = _spy(monkeypatch, "render")
    materialized = []

    def record(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT PIPELINE_PROCESSES."):
            materialized.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    await _seed(session, admin_user.organization_id)
    run = await session.execute(
        text(
            "INSERT INTO pipeline_runs (organization_id, experiment_id, pipeline_name, status) "
            "VALUES (:o, 1, 'p', 'completed') RETURNING id"
        ),
        {"o": admin_user.organization_id},
    )
    run_id = run.scalar_one()
    await session.execute(
        text(
            "INSERT INTO pipeline_processes (pipeline_run_id, process_name, status) "
            "SELECT :r, 'proc'||g, 'completed' FROM generate_series(1, 20000) g"
        ),
        {"r": run_id},
    )
    await session.commit()

    try:
        resp = await _post(client, headers)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert resp.status_code == 413
    assert rendered == []
    assert materialized == []


@pytest.mark.asyncio
async def test_a_project_report_is_bounded_too(client, session, admin_user, headers, store):
    await _seed(session, admin_user.organization_id)
    await session.execute(
        text(
            "INSERT INTO audit_log (entity_type, entity_id, action, details_json) "
            "SELECT 'project', 1, 'updated', jsonb_build_object('note', repeat('x', 200)) "
            "FROM generate_series(1, 100000)"
        )
    )
    await session.commit()

    resp, growth, delay = await _heavy_export(client, headers, "/api/projects/1/export/data")

    assert resp.status_code == 413
    assert growth < 64
    assert delay < 0.1


@pytest.mark.asyncio
async def test_the_standalone_report_api_is_unchanged_without_a_budget(session, admin_user):
    """Only exports pass a budget; other callers gather exactly as before."""
    await _seed(session, admin_user.organization_id, samples=3)

    report = await ProvenanceReportService.gather_report(session, "experiment", 1, admin_user.organization_id, "a@b")

    assert len(report["entity"]["samples"]) == 3
