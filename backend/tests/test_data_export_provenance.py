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
async def test_too_many_records_are_refused_before_any_report_is_gathered(
    client, session, admin_user, headers, store, monkeypatch, path
):
    monkeypatch.setattr(resources, "PROVENANCE_MAX_RECORDS", 3)
    gathered = _spy(monkeypatch, "gather_report")
    await _seed(session, admin_user.organization_id, samples=4)

    resp = await _post(client, headers, path)

    assert resp.status_code == 413
    assert "provenance report" in resp.json()["detail"]
    assert "without the provenance report" in resp.json()["detail"]
    assert gathered == []


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
