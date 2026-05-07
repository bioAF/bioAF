"""TDD: ReferenceDataService.start_import / import_status / import_cancel.

Spec §3 import-from-URL flow. The service:
- creates a ReferenceDataset row in status='uploading' (same lifecycle as
  upload — finalize via the existing upload_complete path),
- creates a ReferenceImportProgress row in status='pending',
- launches a GKE job (stubbed in tests) and stores its name as import_job_id,
- exposes status reads + a cancel that deletes the GKE job and aborts the
  reference (purges GCS + deletes the row).
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


def _stub_create_job(*, reference_id, source_url, gcs_prefix, **kwargs):
    """Drop-in for the live GKE job creator. Returns the job name."""
    return f"refimport-{reference_id}-stub"


@pytest.mark.asyncio
async def test_start_import_creates_dataset_progress_and_job(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceImportRequest(
        name="GENCODE",
        category="annotation",
        scope="public",
        version="v45",
        source_url="https://ftp.ebi.ac.uk/.../gencode.v45.annotation.gtf.gz",
        extract="gzip",
    )

    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job) as mock_job:
        dataset, job_id = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    assert dataset.status == "uploading"
    assert dataset.gcs_prefix.endswith("/")
    assert job_id == f"refimport-{dataset.id}-stub"

    # k8s job creation called once with expected args
    mock_job.assert_called_once()
    call_kwargs = mock_job.call_args.kwargs
    assert call_kwargs["reference_id"] == dataset.id
    assert call_kwargs["source_url"] == payload.source_url
    assert call_kwargs["gcs_prefix"] == dataset.gcs_prefix

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
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
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
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    # Importer container would write progress; simulate it here:
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
async def test_cancel_import_deletes_job_and_purges_reference(session, comp_bio_user, configured_refs_bucket):
    payload = ReferenceImportRequest(
        name="CancelMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()
    dataset_id = dataset.id

    deleted_jobs: list[str] = []

    def _capture_delete(job_id: str) -> None:
        deleted_jobs.append(job_id)

    with (
        patch.object(ReferenceDataService, "_delete_import_job", side_effect=_capture_delete),
        patch.object(ReferenceDataService, "_delete_blobs", return_value=None),
    ):
        await ReferenceDataService.cancel_import(
            session, reference_id=dataset_id, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id
        )
        await session.commit()

    assert deleted_jobs == [f"refimport-{dataset_id}-stub"]
    fresh = await session.get(ReferenceDataset, dataset_id)
    assert fresh is None
    # Bypass the ORM identity map: cascade DELETE happens at the DB level via
    # FK ON DELETE CASCADE, so SQLAlchemy may still return the cached instance
    # via session.get(). Query directly to confirm the row is gone.
    progress_row = (
        await session.execute(
            text("SELECT 1 FROM reference_import_progress WHERE reference_id = :id"),
            {"id": dataset_id},
        )
    ).first()
    assert progress_row is None  # cascade delete


@pytest.mark.asyncio
async def test_record_import_progress_updates_row(session, comp_bio_user, configured_refs_bucket):
    """Internal callback path: the importer container POSTs progress updates."""
    payload = ReferenceImportRequest(
        name="ProgressMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
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
    """When the importer reports status='failed', the dataset row must also
    flip to status='failed' so the existing UI surfaces it."""
    payload = ReferenceImportRequest(
        name="FailMe",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
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
