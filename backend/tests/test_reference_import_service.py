"""TDD: ReferenceDataService.start_import / import_status / import_cancel.

Import-from-URL runs as an in-process asyncio background task in the
backend (not a GKE Pod), so the worker code is the running backend code
and there is no image / KSA / callback-URL plumbing to keep in sync.
start_import:

- creates a ReferenceDataset row in status='uploading' (same lifecycle as
  upload -- finalize via the existing upload_complete path),
- creates a ReferenceImportProgress row in status='pending',
- schedules a background task that streams the URL into GCS and writes
  progress directly to the DB,
- returns immediately so the caller (the HTTP request) can navigate away.
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.reference_dataset import ReferenceDataset
from app.models.reference_import_progress import ReferenceImportProgress
from app.schemas.reference_dataset import ReferenceImportRequest
from app.services.auth_service import AuthService
from app.services.reference_data_service import ReferenceDataService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_import@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def configured_refs_bucket(session):
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('references_bucket_name', 'bioaf-references-test', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


def _stub_schedule(*, dataset_id, request):
    """Drop-in for the live background scheduler. Returns the sentinel job id."""
    return f"refimport-{dataset_id}-inproc"


@pytest.mark.asyncio
async def test_start_import_creates_dataset_progress_and_schedules_background_task(
    session, comp_bio_user, configured_refs_bucket
):
    payload = ReferenceImportRequest(
        name="GENCODE",
        category="annotation",
        scope="public",
        version="v45",
        source_url="https://ftp.ebi.ac.uk/.../gencode.v45.annotation.gtf.gz",
        extract="gzip",
    )

    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule) as mock_schedule:
        dataset, job_id = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    assert dataset.status == "uploading"
    assert dataset.gcs_prefix.endswith("/")
    assert job_id == f"refimport-{dataset.id}-inproc"

    # Background task scheduled once, with the dataset id and the request.
    mock_schedule.assert_called_once()
    call_kwargs = mock_schedule.call_args.kwargs
    assert call_kwargs["dataset_id"] == dataset.id
    assert call_kwargs["request"].source_url == payload.source_url

    # progress row exists
    progress = await session.get(ReferenceImportProgress, dataset.id)
    assert progress is not None
    assert progress.status == "pending"
    assert progress.import_job_id == job_id

    # audit log
    audit = await session.execute(
        text(
            "SELECT * FROM audit_log WHERE entity_type='reference_dataset' "
            "AND entity_id=:id AND action='import_started'"
        ),
        {"id": dataset.id},
    )
    assert audit.fetchone() is not None


@pytest.mark.asyncio
async def test_start_import_rejects_duplicate(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceImportRequest(
        name="GENCODE",
        category="annotation",
        scope="public",
        version="v45",
        source_url="https://ftp.example/gencode.gtf.gz",
        extract="gzip",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

        with pytest.raises(ValueError, match="already exists"):
            await ReferenceDataService.start_import(
                session,
                org_id=comp_bio_user.organization_id,
                user_id=comp_bio_user.id,
                request=payload,
            )


@pytest.mark.asyncio
async def test_get_import_status_returns_progress_row(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceImportRequest(
        name="ScratchRef",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    # Simulate the background task writing progress.
    progress = await session.get(ReferenceImportProgress, dataset.id)
    progress.status = "downloading"
    progress.progress_pct = 42
    progress.bytes_downloaded = 100
    progress.total_bytes = 1000
    await session.commit()

    status = await ReferenceDataService.get_import_status(
        session, reference_id=dataset.id, org_id=comp_bio_user.organization_id
    )
    assert status.status == "downloading"
    assert status.progress_pct == 42
    assert status.bytes_downloaded == 100
    assert status.total_bytes == 1000


@pytest.mark.asyncio
async def test_get_import_status_404_when_not_found(session, comp_bio_user):
    with pytest.raises(ValueError, match="not found"):
        await ReferenceDataService.get_import_status(
            session, reference_id=999_999, org_id=comp_bio_user.organization_id
        )


@pytest.mark.asyncio
async def test_cancel_import_purges_reference(session, comp_bio_user, configured_refs_bucket):
    """Cancel deletes the dataset + progress rows and purges GCS. There is
    no GKE Job to delete; an in-flight background task may continue
    running for a short window after cancel returns, but its writes to a
    nonexistent progress row are no-ops."""
    payload = ReferenceImportRequest(
        name="CancelMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()
    dataset_id = dataset.id

    with patch.object(ReferenceDataService, "_delete_blobs", return_value=None):
        await ReferenceDataService.cancel_import(
            session, reference_id=dataset_id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )
        await session.commit()

    fresh = await session.get(ReferenceDataset, dataset_id)
    assert fresh is None
    progress_row = (
        await session.execute(
            text("SELECT 1 FROM reference_import_progress WHERE reference_id = :id"),
            {"id": dataset_id},
        )
    ).first()
    assert progress_row is None


@pytest.mark.asyncio
async def test_record_import_progress_updates_row(session, comp_bio_user, configured_refs_bucket):
    """The in-process background task records progress via record_import_progress."""
    payload = ReferenceImportRequest(
        name="ProgressMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    await ReferenceDataService.record_import_progress(
        session,
        reference_id=dataset.id,
        status="downloading",
        progress_pct=25,
        bytes_downloaded=250,
        total_bytes=1000,
    )
    await session.commit()

    progress = await session.get(ReferenceImportProgress, dataset.id)
    assert progress.status == "downloading"
    assert progress.progress_pct == 25
    assert progress.bytes_downloaded == 250


@pytest.mark.asyncio
async def test_record_import_progress_failure_sets_dataset_failed(session, comp_bio_user, configured_refs_bucket):
    """When the background task records status='failed', the dataset row
    must also flip to status='failed' so the existing UI surfaces it."""
    payload = ReferenceImportRequest(
        name="FailMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    await ReferenceDataService.record_import_progress(
        session,
        reference_id=dataset.id,
        status="failed",
        error_message="404 from upstream",
    )
    await session.commit()

    fresh = await session.get(ReferenceDataset, dataset.id)
    assert fresh.status == "failed"
    assert "404" in (fresh.deprecation_note or "")


@pytest.mark.asyncio
async def test_finalize_import_writes_files_and_flips_dataset_to_active(session, comp_bio_user, configured_refs_bucket):
    """After the in-process importer task returns its ImportResult, the
    service must finalize the dataset row: write a ReferenceDatasetFile
    per imported file, aggregate total_size_bytes / file_count /
    md5_manifest_json, flip dataset.status off 'uploading' (to 'active'
    for internal scope), and mark the progress row 'active'. Without
    this step the UI's 'Importing' badge persists forever because
    ReferenceDataset.status never transitions out of 'uploading'."""
    from app.models.reference_dataset import ReferenceDatasetFile
    from app.workers.reference_importer import ImportedFile, ImportResult

    payload = ReferenceImportRequest(
        name="FinalizeMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()
    dataset_id = dataset.id

    result = ImportResult(
        files=[
            ImportedFile(
                filename="file.gz",
                gcs_uri="gs://bioaf-references-test/annotation/finalizeme/v1/file.gz",
                size_bytes=1_234_567,
                md5="0123456789abcdef0123456789abcdef",
            ),
        ]
    )
    await ReferenceDataService.finalize_import(session, reference_id=dataset_id, result=result)
    await session.commit()

    fresh = await session.get(ReferenceDataset, dataset_id)
    assert fresh.status == "active"
    assert fresh.total_size_bytes == 1_234_567
    assert fresh.file_count == 1
    assert fresh.md5_manifest_json == {"file.gz": "0123456789abcdef0123456789abcdef"}

    rows = (
        await session.execute(
            text(
                "SELECT filename, storage_uri, size_bytes, md5_checksum FROM reference_dataset_files WHERE reference_dataset_id = :id"
            ),
            {"id": dataset_id},
        )
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "file.gz"
    assert rows[0][1] == "gs://bioaf-references-test/annotation/finalizeme/v1/file.gz"
    assert rows[0][2] == 1_234_567
    assert rows[0][3] == "0123456789abcdef0123456789abcdef"

    progress = await session.get(ReferenceImportProgress, dataset_id)
    assert progress.status == "active"

    # Used for tests below to clear unused import warnings.
    _ = ReferenceDatasetFile


@pytest.mark.asyncio
async def test_recover_finalize_lists_gcs_and_finalizes_a_stuck_dataset(session, comp_bio_user, configured_refs_bucket):
    """Recovery path for the bug we just shipped a fix for: a dataset
    whose bytes are already in GCS but whose row is still in 'uploading'
    (because the previous URL-import code never flipped the dataset
    status). recover_finalize lists the blobs under the dataset's
    gcs_prefix, builds an ImportResult, and runs the same finalize_import
    we run after a fresh URL import. Without this, the only way to clear
    a stuck row would be to delete it and re-download tens of GB."""

    payload = ReferenceImportRequest(
        name="StuckRef",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()
    dataset_id = dataset.id

    class _FakeBlob:
        def __init__(self, name, size, md5_hash=None):
            self.name = name
            self.size = size
            self.md5_hash = md5_hash

    fake_blobs = [
        _FakeBlob(
            name=f"{dataset.gcs_prefix}file.gz",
            size=11_448_662_640,
            md5_hash="deadbeef" * 4,
        ),
    ]
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=fake_blobs):
        result_ds = await ReferenceDataService.recover_finalize(
            session, reference_id=dataset_id, org_id=comp_bio_user.organization_id
        )
        await session.commit()

    assert result_ds.status == "active"
    assert result_ds.total_size_bytes == 11_448_662_640
    assert result_ds.file_count == 1

    rows = (
        await session.execute(
            text("SELECT filename, storage_uri, size_bytes FROM reference_dataset_files WHERE reference_dataset_id = :id"),
            {"id": dataset_id},
        )
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "file.gz"
    assert rows[0][1] == f"gs://bioaf-references-test/{dataset.gcs_prefix}file.gz"
    assert rows[0][2] == 11_448_662_640


@pytest.mark.asyncio
async def test_recover_finalize_rejects_already_finalized_dataset(session, comp_bio_user, configured_refs_bucket):
    """If a caller hits the recovery endpoint on a dataset that's already
    active / pending_approval / failed, raise so the API can return 409.
    Re-running finalize would not add any rows (idempotency guard already
    handles that) but the surface should be loud rather than silent."""

    payload = ReferenceImportRequest(
        name="AlreadyActive",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()
    dataset.status = "active"
    await session.commit()

    with pytest.raises(ValueError, match="not in 'uploading'"):
        await ReferenceDataService.recover_finalize(
            session, reference_id=dataset.id, org_id=comp_bio_user.organization_id
        )


@pytest.mark.asyncio
async def test_recover_finalize_raises_when_no_blobs_exist(session, comp_bio_user, configured_refs_bucket):
    """If nothing's under the prefix, the dataset wasn't actually
    uploaded -- finalizing with zero files would silently produce a
    'finished' dataset with no contents. Raise instead so the caller
    knows to cancel + retry."""
    payload = ReferenceImportRequest(
        name="EmptyPrefix",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=[]):
        with pytest.raises(ValueError, match="no files"):
            await ReferenceDataService.recover_finalize(
                session, reference_id=dataset.id, org_id=comp_bio_user.organization_id
            )


@pytest.mark.asyncio
async def test_finalize_import_flips_public_dataset_to_pending_approval(session, comp_bio_user, configured_refs_bucket):
    """For scope='public' the upload path flips to 'pending_approval' so
    an admin can review before the dataset is exposed to the org. The
    URL-import finalization must mirror that rule -- the only difference
    between the two ingest paths is *how* bytes get to GCS."""
    from app.workers.reference_importer import ImportedFile, ImportResult

    payload = ReferenceImportRequest(
        name="PublicFinalize",
        category="annotation",
        scope="public",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_schedule_import", side_effect=_stub_schedule):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()
    dataset_id = dataset.id

    result = ImportResult(
        files=[
            ImportedFile(filename="file.gz", gcs_uri="gs://b/p/file.gz", size_bytes=10, md5="a" * 32),
        ]
    )
    await ReferenceDataService.finalize_import(session, reference_id=dataset_id, result=result)
    await session.commit()

    fresh = await session.get(ReferenceDataset, dataset_id)
    assert fresh.status == "pending_approval"
