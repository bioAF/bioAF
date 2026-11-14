"""Who owns an export's scratch and slot, and when they are released (plan 24 contract 3)."""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest

from app.api.streaming_responses import GuardedFileResponse
from app.exceptions import ExportTimeoutError
from app.services.data_export import resources
from app.services.data_export.archive import ArchivePlan, ExportJob
from app.services.data_export.selection import SelectedFile


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch):
    monkeypatch.setattr(
        resources,
        "disk_probe",
        lambda path: lambda: resources.DiskSpace(free=500 * resources.GiB, total=1000 * resources.GiB),
    )
    yield
    assert not resources.SLOT.busy


def _file(name: str = "a.txt") -> SelectedFile:
    return SelectedFile(
        id=1,
        uuid=uuid.uuid4(),
        filename=name,
        file_type="txt",
        size_bytes=4,
        storage_uri="gs://b/a",
        storage_deleted=False,
    )


def _plan() -> ArchivePlan:
    plan = ArchivePlan()
    plan.add_generated("root/README.txt", "hi")
    plan.add_file("root", _file())
    return plan


class _HangingAdapter:
    """A download whose worker thread ignores cancellation until released (a stuck network read)."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    async def download_to_file(self, uri, file_obj):
        def blocked():
            self.started.set()
            self.release.wait(10)
            file_obj.write(b"late")

        await asyncio.to_thread(blocked)


class _WritingAdapter:
    def __init__(self, data: bytes = b"data"):
        self.data = data

    async def download_to_file(self, uri, file_obj):
        await asyncio.to_thread(file_obj.write, self.data)


@pytest.mark.asyncio
async def test_a_late_worker_keeps_scratch_and_the_slot_until_it_settles(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "SETTLE_SECONDS", 0.2)
    job = ExportJob.admit([4], scratch_dir=str(tmp_path))
    adapter = _HangingAdapter()

    with pytest.raises(ExportTimeoutError):
        await job.build(_plan(), adapter, deadline=0.3)

    assert resources.SLOT.busy
    assert job.workdir.exists()

    adapter.release.set()
    for _ in range(100):
        if not resources.SLOT.busy:
            break
        await asyncio.sleep(0.05)
    assert not resources.SLOT.busy
    assert not job.workdir.exists()


@pytest.mark.asyncio
async def test_a_cancelled_request_stops_the_worker_at_its_next_write_and_releases(tmp_path):
    job = ExportJob.admit([4], scratch_dir=str(tmp_path))
    adapter = _HangingAdapter()
    waiting = asyncio.create_task(job.build(_plan(), adapter))
    await asyncio.to_thread(adapter.started.wait, 5)

    waiting.cancel()
    adapter.release.set()  # the stuck read returns; its write meets the cancelled budget
    with pytest.raises(asyncio.CancelledError):
        await waiting

    for _ in range(100):
        if not resources.SLOT.busy:
            break
        await asyncio.sleep(0.05)
    assert not resources.SLOT.busy
    assert not job.workdir.exists()


@pytest.mark.asyncio
async def test_a_successful_build_belongs_to_its_caller_until_released(tmp_path):
    job = ExportJob.admit([4], scratch_dir=str(tmp_path))

    size = await job.build(_plan(), _WritingAdapter())

    assert size == job.archive_path.stat().st_size
    assert resources.SLOT.busy
    job.release()
    job.release()
    assert not job.workdir.exists()


@pytest.mark.asyncio
async def test_stale_scratch_from_a_killed_process_is_removed_at_the_next_admission(tmp_path):
    stale = tmp_path / "export-leftover"
    stale.mkdir()
    (stale / "export.zip").write_bytes(b"old")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("x")

    job = ExportJob.admit([], scratch_dir=str(tmp_path))
    job.release()

    assert not stale.exists()
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_a_failed_build_releases_before_raising(tmp_path):
    from app.adapters.models import StorageObjectNotFound
    from app.exceptions import ExportContentUnavailableError

    class Missing:
        async def download_to_file(self, uri, file_obj):
            raise StorageObjectNotFound(uri)

    job = ExportJob.admit([4], scratch_dir=str(tmp_path))
    with pytest.raises(ExportContentUnavailableError):
        await job.build(_plan(), Missing())

    assert not resources.SLOT.busy
    assert not job.workdir.exists()


# -- The guarded response ------------------------------------------------------------------------

SCOPE = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "POST", "path": "/x", "headers": []}


async def _receive():
    await asyncio.sleep(3600)
    return {"type": "http.disconnect"}


def _response(tmp_path, closed: list, *, size: int = 5 * 1024 * 1024, stall: float = 0.2, deadline: float = 30):
    path = tmp_path / "export.zip"
    path.write_bytes(b"z" * size)

    async def on_close(outcome):
        closed.append(outcome)

    return GuardedFileResponse(
        str(path),
        size=size,
        filename="实验.zip",
        media_type="application/zip",
        on_close=on_close,
        stall_seconds=stall,
        deadline_seconds=deadline,
        chunk_size=64 * 1024,
    )


@pytest.mark.asyncio
async def test_a_completed_transfer_closes_once_as_sent(tmp_path):
    closed: list = []
    body = bytearray()

    async def send(message):
        body.extend(message.get("body", b""))

    await _response(tmp_path, closed, size=1000)(SCOPE, _receive, send)

    assert closed == ["sent"]
    assert bytes(body) == b"z" * 1000


@pytest.mark.asyncio
async def test_a_failure_sending_headers_still_releases(tmp_path):
    closed: list = []

    async def send(message):
        raise OSError("connection reset")

    with pytest.raises(Exception):
        await _response(tmp_path, closed)(SCOPE, _receive, send)

    assert closed == ["failed"]


@pytest.mark.asyncio
async def test_a_later_body_send_failure_releases(tmp_path):
    closed: list = []
    sends = {"n": 0}

    async def send(message):
        sends["n"] += 1
        if sends["n"] == 3:
            raise OSError("broken pipe")

    with pytest.raises(Exception):
        await _response(tmp_path, closed)(SCOPE, _receive, send)

    assert closed == ["failed"]


@pytest.mark.asyncio
async def test_a_stalled_client_is_cut_off_and_released(tmp_path):
    closed: list = []

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            await asyncio.sleep(3600)

    await asyncio.wait_for(_response(tmp_path, closed, stall=0.2)(SCOPE, _receive, send), timeout=5)

    assert closed == ["stalled"]


@pytest.mark.asyncio
async def test_a_stall_after_partial_progress_is_cut_off(tmp_path):
    closed: list = []
    sent = {"n": 0}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent["n"] += 1
            if sent["n"] > 5:
                await asyncio.sleep(3600)

    await asyncio.wait_for(_response(tmp_path, closed, stall=0.2)(SCOPE, _receive, send), timeout=5)

    assert closed == ["stalled"]
    assert sent["n"] == 6


@pytest.mark.asyncio
async def test_progress_resets_the_stall_clock(tmp_path):
    """Each send takes most of the stall limit; the whole transfer takes far longer and completes."""
    closed: list = []

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            await asyncio.sleep(0.05)

    await _response(tmp_path, closed, size=10 * 64 * 1024, stall=0.08)(SCOPE, _receive, send)

    assert closed == ["sent"]


@pytest.mark.asyncio
async def test_a_slow_but_progressing_reader_still_hits_the_absolute_deadline(tmp_path):
    closed: list = []

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            await asyncio.sleep(0.05)

    await asyncio.wait_for(
        _response(tmp_path, closed, size=100 * 64 * 1024, stall=1, deadline=0.4)(SCOPE, _receive, send), timeout=5
    )

    assert closed == ["deadline"]


@pytest.mark.asyncio
async def test_cancellation_mid_transfer_releases_before_propagating(tmp_path):
    closed: list = []
    started = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.create_task(_response(tmp_path, closed, stall=60)(SCOPE, _receive, send))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed == ["failed"]


@pytest.mark.asyncio
async def test_the_response_advertises_length_and_disables_proxy_buffering(tmp_path):
    response = _response(tmp_path, [], size=1234)
    headers = dict(response.headers)

    assert headers["content-length"] == "1234"
    assert headers["x-accel-buffering"] == "no"
    assert "filename*=UTF-8''%E5%AE%9E%E9%AA%8C.zip" in headers["content-disposition"]


# -- Deadlines, connections and the unclaimed backstop -------------------------------------------


async def _seed_experiment(session, org: int) -> None:
    from sqlalchemy import text

    await session.execute(
        text("INSERT INTO projects (id, organization_id, name, status) VALUES (1, :o, 'P', 'active')"), {"o": org}
    )
    await session.execute(
        text("INSERT INTO experiments (id, organization_id, project_id, name, status) VALUES (1, :o, 1, 'E', 'x')"),
        {"o": org},
    )
    await session.commit()


@pytest.mark.asyncio
async def test_slow_report_generation_counts_against_the_preparation_deadline(
    session, admin_user, tmp_path, monkeypatch
):
    from app.config import settings
    from app.services import export_service
    from app.services.provenance.report_service import ProvenanceReportService

    async def slow(**kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(ProvenanceReportService, "generate", slow)
    monkeypatch.setattr(resources, "BUILD_DEADLINE", 0.3)
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path))
    await _seed_experiment(session, admin_user.organization_id)

    with pytest.raises(ExportTimeoutError):
        await export_service.prepare_experiment_export(
            session, 1, admin_user.organization_id, include_fastq=False, include_provenance=True, user_email="a@b"
        )

    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_the_build_runs_without_holding_a_database_transaction(session, admin_user, tmp_path, monkeypatch):
    from app.config import settings
    from app.services import export_service

    seen = {}
    real_build = ExportJob.build

    async def build(self, plan, adapter, **kwargs):
        seen["in_transaction"] = session.in_transaction()
        return await real_build(self, plan, adapter, **kwargs)

    monkeypatch.setattr(ExportJob, "build", build)
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path))
    await _seed_experiment(session, admin_user.organization_id)

    prepared = await export_service.prepare_experiment_export(
        session, 1, admin_user.organization_id, include_fastq=False, include_provenance=False, user_email="a@b"
    )
    prepared.release()

    assert seen == {"in_transaction": False}


@pytest.mark.parametrize("claimed", [False, True])
@pytest.mark.asyncio
async def test_the_backstop_releases_only_an_export_no_response_started(tmp_path, monkeypatch, claimed):
    from app.services import export_delivery
    from app.services.export_service import PreparedExport

    monkeypatch.setattr(resources, "TRANSFER_DEADLINE", 0.05)
    monkeypatch.setattr(resources, "SETTLE_SECONDS", 0.01)
    job = ExportJob.admit([], scratch_dir=str(tmp_path))
    prepared = PreparedExport(job=job, size=0, filename="x.zip", claimed=claimed)

    export_delivery.watch_unclaimed(prepared)
    await asyncio.sleep(0.3)

    assert resources.SLOT.busy is claimed
    prepared.release()


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
@pytest.mark.asyncio
async def test_a_disconnect_with_silently_dropped_sends_is_not_reported_as_sent(tmp_path, spec):
    """uvicorn drops sends after the peer goes away (no error); the disconnect message is the signal."""
    closed: list = []
    gone = asyncio.Event()
    sent = {"bytes": 0}

    async def receive():
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent["bytes"] += len(message["body"])
            if sent["bytes"] >= 3 * 64 * 1024:
                gone.set()  # the proxy hung up; later sends vanish without an error
            await asyncio.sleep(0.001)

    scope = {**SCOPE, "asgi": {"spec_version": spec}}  # uvicorn 0.54 advertises 2.3
    await asyncio.wait_for(_response(tmp_path, closed, stall=5)(scope, receive, send), timeout=5)

    assert closed == ["disconnected"]
    assert sent["bytes"] < 5 * 1024 * 1024
