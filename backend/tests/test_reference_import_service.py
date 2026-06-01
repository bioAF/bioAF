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
async def configured_refs_bucket(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "internal_token", "test-internal-token")
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('references_bucket_name', 'bioaf-references-test', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    # The callback URL is rendered from the Networking settings the operator
    # already configured for the UI/API to be reachable.
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('networking_hostname', 'bioaf', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('networking_domain', 'example.com', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('networking_https_enforced', 'true', NOW()) "
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
async def test_start_import_renders_callback_url_from_networking_settings(
    session, comp_bio_user, configured_refs_bucket
):
    """The Pod's callback URL must point at the *publicly reachable* bioAF
    API on the VM. That hostname/domain is already configured via the
    Networking settings page (networking_hostname + networking_domain +
    networking_https_enforced). The importer must derive the callback URL
    from those existing keys: no separate 'bioaf_api_url' to maintain."""
    payload = ReferenceImportRequest(
        name="CallbackCheck",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _stub_create_job(**kwargs)

    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_capture):
        dataset, _ = await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    # Configured: hostname=bioaf, domain=example.com, https=true
    assert captured["callback_url"] == (
        f"https://bioaf.example.com/api/internal/references/{dataset.id}/import-progress"
    )


@pytest.mark.asyncio
async def test_start_import_callback_url_uses_http_when_https_not_enforced(session, comp_bio_user, monkeypatch):
    """When networking_https_enforced is not 'true', fall back to http://
    so the importer Pod can reach the backend before TLS is provisioned."""
    from app.config import settings

    monkeypatch.setattr(settings, "internal_token", "test-internal-token")
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('references_bucket_name', 'bioaf-references-test'),"
            "('networking_hostname', 'bioaf-staging'),"
            "('networking_domain', 'example.com'),"
            "('networking_https_enforced', 'false') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    payload = ReferenceImportRequest(
        name="HttpCheck",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _stub_create_job(**kwargs)

    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_capture):
        await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    assert captured["callback_url"].startswith("http://bioaf-staging.example.com/")


@pytest.mark.asyncio
async def test_start_import_auto_bootstraps_internal_token_when_unset(session, comp_bio_user):
    """The internal callback token is never set by any installer flow; the
    backend must self-bootstrap one on first use, persist it to
    platform_config so it survives restarts, and pass it to the Pod. The
    operator should not have to set BIOAF_INTERNAL_TOKEN by hand."""
    from app.config import settings

    # Networking + references bucket configured; internal_token deliberately NOT.
    settings.internal_token = ""
    await session.execute(
        text(
            "DELETE FROM platform_config WHERE key IN "
            "('internal_callback_token', 'references_bucket_name', 'networking_hostname', "
            "'networking_domain', 'networking_https_enforced')"
        )
    )
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('references_bucket_name', 'bioaf-references-test'),"
            "('networking_hostname', 'bioaf'),"
            "('networking_domain', 'example.com'),"
            "('networking_https_enforced', 'true')"
        )
    )
    await session.commit()

    payload = ReferenceImportRequest(
        name="AutoBootstrap",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _stub_create_job(**kwargs)

    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_capture):
        await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    # A token was generated and passed to the Pod.
    assert captured["internal_token"]
    assert len(captured["internal_token"]) >= 16

    # Persisted to platform_config so a backend restart sees the same value.
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'internal_callback_token'"))
    ).scalar_one_or_none()
    assert row == captured["internal_token"]

    # And the running process's settings.internal_token is now set so the
    # callback endpoint will accept the Pod's POST.
    assert settings.internal_token == captured["internal_token"]


@pytest.mark.asyncio
async def test_start_import_reuses_persisted_internal_token(session, comp_bio_user):
    """If a token already exists in platform_config (set by a previous
    start_import or a future installer), the backend reuses it instead of
    generating a fresh one each request."""
    from app.config import settings

    settings.internal_token = ""
    await session.execute(
        text(
            "DELETE FROM platform_config WHERE key IN "
            "('internal_callback_token', 'references_bucket_name', 'networking_hostname', "
            "'networking_domain', 'networking_https_enforced')"
        )
    )
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('references_bucket_name', 'bioaf-references-test'),"
            "('networking_hostname', 'bioaf'),"
            "('networking_domain', 'example.com'),"
            "('networking_https_enforced', 'true'),"
            "('internal_callback_token', 'pre-existing-token-value')"
        )
    )
    await session.commit()

    payload = ReferenceImportRequest(
        name="ReusePersisted",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _stub_create_job(**kwargs)

    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_capture):
        await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
        await session.commit()

    assert captured["internal_token"] == "pre-existing-token-value"
    assert settings.internal_token == "pre-existing-token-value"


@pytest.mark.asyncio
async def test_start_import_raises_when_networking_unset(session, comp_bio_user, monkeypatch):
    """If neither hostname nor domain is set, the importer Pod has no way
    to reach the backend. Raise a ValueError with 'not configured' so the
    API maps it to 503."""
    from app.config import settings

    monkeypatch.setattr(settings, "internal_token", "test-internal-token")
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('references_bucket_name', 'bioaf-references-test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    payload = ReferenceImportRequest(
        name="NoNetworking",
        category="annotation",
        scope="internal",
        version="v1",
        source_url="https://ftp.example/file.gz",
        extract="none",
    )
    with pytest.raises(ValueError) as exc:
        await ReferenceDataService.start_import(
            session, org_id=comp_bio_user.organization_id, user_id=comp_bio_user.id, request=payload
        )
    assert "not configured" in str(exc.value).lower()


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
