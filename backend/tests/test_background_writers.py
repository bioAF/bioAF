"""Background writers are admitted like uploads (storage lifecycle 11G; review gap).

Thumbnails, QC metric caches, data export archives, environment build contexts and reference
imports write to buckets Destroy Storage empties. Each write is admitted first: while storage
writes are paused it is skipped or refused before anything is written, and while it runs it
counts as a writer, so destroy waits for it.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import storage_write_admission as admission


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
    assert str(exc.value) == "An administrator is changing storage. Try again later."
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
    assert recorded == [{"status": "failed", "error_message": "An administrator is changing storage. Try again later."}]


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
