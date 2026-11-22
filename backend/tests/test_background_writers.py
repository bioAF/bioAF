"""Background writers are admitted like uploads (storage lifecycle 11G; review gap).

Thumbnails, QC metric caches, data export archives, environment build contexts and reference
imports write to buckets Destroy Storage empties. Each write is admitted first: while storage
writes are paused it is skipped or refused before anything is written, and while it runs it
counts as a writer, so destroy waits for it.
"""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import storage_write_admission as admission
from tests.support.blocking import BlockingCall
from app import database
from app.adapters.storage.gcs import GcsStorageProvider


async def _pause(session):
    await admission.pause_writes(session, operation_id=None)
    await session.commit()


@pytest.mark.asyncio
async def test_a_running_background_write_is_a_writer(session):
    async with admission.background_write("thumbnail"):
        writers = await admission.outstanding_writers(session)
    assert [(w.kind, w.filename) for w in writers] == [("background", "thumbnail")]
    assert await admission.outstanding_writers(session) == []


@pytest.mark.asyncio
async def test_a_background_write_is_refused_while_writes_are_paused(session):
    await _pause(session)
    ran = []
    with pytest.raises(admission.StorageWritesPaused):
        async with admission.background_write("thumbnail"):
            ran.append(True)
    assert ran == []
    assert await admission.outstanding_writers(session) == []


@pytest.mark.asyncio
async def test_no_thumbnail_is_written_while_writes_are_paused(session):
    from app.services.thumbnail_service import ThumbnailService

    await _pause(session)
    adapter = AsyncMock()
    adapter.parse_uri = MagicMock(return_value=("bioaf-results", "plots/a.pdf"))
    adapter.build_uri = MagicMock(return_value="gs://bioaf-results/_thumbnails/plot_1.png")
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch.object(ThumbnailService, "render_pdf_thumbnail", return_value=b"png"),
    ):
        assert await ThumbnailService.generate_and_upload(session, "gs://bioaf-results/plots/a.pdf", 1) is None
    adapter.write_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_qc_metrics_are_returned_but_not_cached_while_writes_are_paused(session):
    from app.services.qc.cache import write_metrics_cache

    await _pause(session)
    adapter = AsyncMock()
    await write_metrics_cache(adapter, "gs://bioaf-results/qc/run1.json", {"reads": 1})
    adapter.write_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_qc_metrics_are_cached_when_writes_are_open(session):
    from app.services.qc.cache import write_metrics_cache

    adapter = AsyncMock()
    await write_metrics_cache(adapter, "gs://bioaf-results/qc/run1.json", {"reads": 1})
    adapter.write_text.assert_awaited_once_with(
        "gs://bioaf-results/qc/run1.json", json.dumps({"reads": 1}, indent=2), content_type="application/json"
    )


@pytest.mark.asyncio
async def test_an_environment_build_context_is_refused_while_writes_are_paused(session):
    from app.services.environment_build_service import upload_build_context

    await _pause(session)
    adapter = AsyncMock()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        with pytest.raises(admission.StorageWritesPaused) as exc:
            await upload_build_context("gs://bioaf-working/env/ctx.tgz", b"tar")
    assert (
        str(exc.value)
        == "An administrator has paused uploads and file saving. Try again later, or contact your administrator."
    )
    adapter.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_reference_url_import_is_refused_while_writes_are_paused(session, admin_user):
    from app.schemas.reference_dataset import ReferenceImportRequest
    from app.services.reference_data_service import ReferenceDataService

    await _pause(session)
    request = ReferenceImportRequest(
        name="GRCh38", category="genome", scope="internal", version="v45", source_url="https://example.org/genome.fa"
    )
    with pytest.raises(admission.UploadsPausedError):
        await ReferenceDataService.start_import(session, admin_user.organization_id, admin_user.id, request)


@pytest.mark.asyncio
async def test_a_reference_import_that_finds_writes_paused_fails_without_writing(session, monkeypatch):
    from app.services.reference_data_service import ReferenceDataService

    recorded = []

    async def record(dataset_id, **payload):
        recorded.append(payload)

    monkeypatch.setattr(ReferenceDataService, "_record_progress_in_new_session", staticmethod(record))
    await _pause(session)
    ran = []
    await ReferenceDataService._run_admitted_import(1, lambda: ran.append(True))
    assert ran == []
    assert recorded == [
        {
            "status": "failed",
            "error_message": "An administrator has paused uploads and file saving. Try again later, or contact your administrator.",
        }
    ]


@pytest.mark.asyncio
async def test_after_a_restart_the_paused_state_is_read_from_the_database(session, monkeypatch):
    """The process keeps its own view of the pause; an unknown view (a fresh process) is read
    from the durable setting before any background write is admitted."""
    await _pause(session)
    monkeypatch.setattr(admission, "_paused_mirror", None)
    with pytest.raises(admission.StorageWritesPaused):
        async with admission.background_write("thumbnail"):
            pass

    await admission.allow_writes(session)
    await session.commit()
    async with admission.background_write("thumbnail"):
        pass


@pytest.mark.asyncio
async def test_a_cancelled_thread_write_stays_a_writer_until_the_thread_stops(session):
    """A thread cannot be interrupted: cancelling its awaiter must not make destroy think it stopped."""
    import asyncio
    import threading

    started, stop = threading.Event(), threading.Event()

    def writing():
        started.set()
        stop.wait(10)

    task = asyncio.create_task(admission.write_in_thread("reference import", writing))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [w.filename for w in await admission.outstanding_writers(session)] == ["reference import"]
    finally:
        stop.set()
    for _ in range(50):
        if not await admission.outstanding_writers(session):
            break
        await asyncio.sleep(0.05)
    assert await admission.outstanding_writers(session) == []


@pytest.mark.asyncio
async def test_a_request_admitted_before_the_pause_is_waited_for(session):
    """Admission registers the writer before reading the pause: a destroy that pauses meanwhile
    finds it and waits, instead of seeing nobody and deleting under it."""
    async with admission.admitted(session):
        await admission.pause_writes(session, operation_id=None)
        await session.commit()
        assert len(await admission.outstanding_writers(session)) == 1
    assert await admission.outstanding_writers(session) == []


@pytest.mark.asyncio
async def test_a_request_arriving_after_the_pause_is_refused_and_not_left_registered(session):
    await _pause(session)
    with pytest.raises(admission.UploadsPausedError):
        async with admission.admitted(session):
            pass
    assert await admission.outstanding_writers(session) == []


# ---------------------------------------------------------------------------
# a storage call outlives a cancelled awaiter (third review round)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxied_upload_remains_a_writer_until_its_sdk_thread_stops(session, admin_user):
    from app.services.upload_service import UploadService

    user_id, org_id = admin_user.id, admin_user.organization_id
    adapter = GcsStorageProvider.__new__(GcsStorageProvider)
    call = BlockingCall()

    async def request():
        async with database.async_session_factory() as request_session:
            await UploadService.simple_upload(
                request_session,
                org_id,
                user_id,
                "a.fastq.gz",
                io.BytesIO(b"data"),
                size_bytes=4,
            )

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.storage_write_admission.require_store_usable", AsyncMock()),
        patch.object(GcsStorageProvider, "is_local", property(lambda self: False)),
        patch.object(adapter, "resolve_uri", AsyncMock(return_value="gs://review-ingest/uploads/review/a.fastq.gz")),
        patch.object(adapter, "_get_credentials", AsyncMock(return_value=None)),
        patch.object(adapter, "_gcs_upload_file", side_effect=call),
    ):
        task = asyncio.create_task(request())
        try:
            await call.wait_until_started()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not call.finished.is_set()
            remaining = await admission.outstanding_writers(session)
            assert remaining, "A cancelled upload lost every writer claim while its provider call was still running"
        finally:
            await call.stop()


@pytest.mark.asyncio
async def test_thumbnail_remains_a_writer_until_its_sdk_thread_stops(session):
    from app.services.thumbnail_service import ThumbnailService

    adapter = GcsStorageProvider.__new__(GcsStorageProvider)
    call = BlockingCall()
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch.object(GcsStorageProvider, "is_local", property(lambda self: False)),
        patch.object(adapter, "read_bytes", AsyncMock(return_value=b"pdf")),
        patch.object(ThumbnailService, "render_pdf_thumbnail", return_value=b"png"),
        patch.object(adapter, "_get_credentials", AsyncMock(return_value=None)),
        patch.object(adapter, "_gcs_write_bytes", side_effect=call),
    ):
        task = asyncio.create_task(ThumbnailService.generate_and_upload(session, "gs://review-results/a.pdf", 1))
        try:
            await call.wait_until_started()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not call.finished.is_set()
            remaining = await admission.outstanding_writers(session)
            assert remaining, "Thumbnail writer disappeared while its storage SDK call was still running"
        finally:
            await call.stop()
