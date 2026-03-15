import io
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def configured_ingest_bucket(session, admin_user):
    """Insert ingest_bucket_name into platform_config for this org's deployment."""
    from app.models.component import PlatformConfig

    cfg = PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test-abc123")
    session.add(cfg)
    await session.flush()
    await session.commit()
    return "bioaf-ingest-test-abc123"


@pytest.mark.asyncio
async def test_initiate_upload_requires_bucket_config(client, admin_token):
    """Upload initiate must return 400 when ingest_bucket_name is not configured."""
    resp = await client.post(
        "/api/files/upload/initiate",
        json={"filename": "sample.fastq.gz", "expected_size_bytes": 1_000_000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "bucket" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_initiate_upload_uses_configured_bucket(client, admin_token, configured_ingest_bucket):
    """Upload initiate must use the bucket name stored in platform_config."""
    from app.services.upload_service import UploadService

    with patch.object(
        UploadService,
        "_generate_signed_upload_url",
        new=AsyncMock(return_value="https://storage.googleapis.com/fake-signed-url"),
    ):
        resp = await client.post(
            "/api/files/upload/initiate",
            json={"filename": "sample.fastq.gz", "expected_size_bytes": 1_000_000},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert configured_ingest_bucket in data["gcs_uri"]


@pytest.mark.asyncio
async def test_initiate_upload_rejects_null_bucket(client, admin_token, session):
    """platform_config row with value 'null' is treated as not configured."""
    from app.models.component import PlatformConfig

    cfg = PlatformConfig(key="ingest_bucket_name", value="null")
    session.add(cfg)
    await session.flush()
    await session.commit()

    resp = await client.post(
        "/api/files/upload/initiate",
        json={"filename": "sample.fastq.gz", "expected_size_bytes": 1_000_000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# simple_upload: experiment_id linkage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_upload_links_experiment_id(client, admin_token, configured_ingest_bucket, session):
    """Files uploaded with ?experiment_id=N must have experiment_id set on the DB record."""
    from app.models.experiment import Experiment
    from app.models.user import User
    from sqlalchemy import select

    user = (await session.execute(select(User).limit(1))).scalar_one()
    exp = Experiment(
        organization_id=user.organization_id,
        name="Upload Link Test",
        owner_user_id=user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await session.commit()

    with patch("app.services.upload_service.UploadService._upload_file_to_gcs", new_callable=AsyncMock):
        resp = await client.post(
            f"/api/files/upload/simple?experiment_id={exp.id}",
            files={"file": ("sample.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    from app.models.file import File

    file_row = (await session.execute(select(File).where(File.filename == "sample.fastq.gz"))).scalar_one_or_none()
    assert file_row is not None
    assert file_row.experiment_id == exp.id


@pytest.mark.asyncio
async def test_simple_upload_without_experiment_id(client, admin_token, configured_ingest_bucket, session):
    """Files uploaded without ?experiment_id must have experiment_id=None."""
    from app.models.file import File
    from sqlalchemy import select

    with patch("app.services.upload_service.UploadService._upload_file_to_gcs", new_callable=AsyncMock):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("noexp.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    file_row = (await session.execute(select(File).where(File.filename == "noexp.fastq.gz"))).scalar_one_or_none()
    assert file_row is not None
    assert file_row.experiment_id is None


# ---------------------------------------------------------------------------
# simple_upload: GCS errors surface as 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_upload_returns_500_on_gcs_failure(client, admin_token, configured_ingest_bucket):
    """GCS upload failure must return 500, not silently create a dangling file record."""
    from app.services.upload_service import UploadService

    async def fake_upload_file_to_gcs(*args, **kwargs):
        raise Exception("403 Provided scope(s) are not authorized")

    with patch.object(UploadService, "_upload_file_to_gcs", side_effect=fake_upload_file_to_gcs):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("fail.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 500
    assert "Upload failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GCS credential selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_gcs_credentials_returns_none_for_vm_default(session):
    """_get_gcs_credentials returns None when source is vm_default (use ADC)."""
    from app.models.component import PlatformConfig
    from app.services.upload_service import UploadService

    session.add(PlatformConfig(key="gcp_credential_source", value="vm_default"))
    await session.flush()
    await session.commit()

    creds = await UploadService._get_gcs_credentials(session)
    assert creds is None


@pytest.mark.asyncio
async def test_get_gcs_credentials_returns_none_when_no_source_configured(session):
    """_get_gcs_credentials returns None when gcp_credential_source is absent."""
    from app.services.upload_service import UploadService

    creds = await UploadService._get_gcs_credentials(session)
    assert creds is None


@pytest.mark.asyncio
async def test_get_gcs_credentials_parses_sa_key(session):
    """_get_gcs_credentials returns credentials when source is service_account_key."""
    from app.models.component import PlatformConfig
    from app.services.upload_service import UploadService

    fake_key = {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "key-id",
        "private_key": "fake-key",
        "client_email": "bioaf@test-project.iam.gserviceaccount.com",
        "client_id": "123",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    session.add(PlatformConfig(key="gcp_credential_source", value="service_account_key"))
    session.add(PlatformConfig(key="gcp_service_account_key", value=json.dumps(fake_key)))
    await session.flush()
    await session.commit()

    mock_creds = MagicMock()
    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=mock_creds,
    ) as mock_from_info:
        creds = await UploadService._get_gcs_credentials(session)

    mock_from_info.assert_called_once()
    call_args = mock_from_info.call_args
    assert call_args[0][0]["client_email"] == "bioaf@test-project.iam.gserviceaccount.com"
    assert creds is mock_creds


@pytest.mark.asyncio
async def test_simple_upload_passes_credentials_to_gcs(client, admin_token, configured_ingest_bucket, session):
    """simple_upload passes credentials from _get_gcs_credentials to _upload_file_to_gcs."""
    from app.services.upload_service import UploadService

    mock_creds = MagicMock()
    captured = {}

    async def fake_upload(bucket_name, gcs_path, file_obj, credentials=None):
        captured["credentials"] = credentials

    with (
        patch.object(UploadService, "_get_gcs_credentials", new=AsyncMock(return_value=mock_creds)),
        patch.object(UploadService, "_upload_file_to_gcs", side_effect=fake_upload),
    ):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("creds_test.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert captured["credentials"] is mock_creds


@pytest.mark.asyncio
async def test_simple_upload_streams_file_without_buffering(client, admin_token, configured_ingest_bucket, session):
    """simple_upload must pass a file-like object to _upload_file_to_gcs, not bytes.

    This prevents OOM crashes when uploading large FASTQ files.
    """
    from app.services.upload_service import UploadService

    captured = {}

    async def fake_upload(bucket_name, gcs_path, file_obj, credentials=None):
        # file_obj must be a readable IO object, not bytes
        assert hasattr(file_obj, "read"), "Expected a file-like object, got bytes or other type"
        captured["file_obj"] = file_obj

    with patch.object(UploadService, "_upload_file_to_gcs", side_effect=fake_upload):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("stream_test.fastq.gz", io.BytesIO(b"hello fastq"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert "file_obj" in captured


# ---------------------------------------------------------------------------
# complete_upload: optional md5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_upload_omitting_actual_md5_returns_200(client, admin_token, configured_ingest_bucket):
    """complete_upload must accept a request body that omits actual_md5.

    The frontend cannot efficiently compute MD5 for 50GB+ files, so the
    field must be optional when no expected_md5 was set during initiate.
    """
    # Seed a pending upload directly in the in-memory store
    from app.services.upload_service import _pending_uploads

    upload_id = str(uuid.uuid4())
    _pending_uploads[upload_id] = {
        "org_id": 1,
        "user_id": 1,
        "filename": "big.fastq.gz",
        "gcs_uri": f"gs://bioaf-ingest-test-abc123/uploads/{upload_id}/big.fastq.gz",
        "expected_size": None,
        "expected_md5": None,  # No MD5 check
        "experiment_id": None,
        "sample_ids": [],
    }

    resp = await client.post(
        "/api/files/upload/complete",
        json={"upload_id": upload_id},  # No actual_md5 field
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_complete_upload_with_explicit_empty_md5_returns_200(client, admin_token, configured_ingest_bucket):
    """complete_upload must accept actual_md5 as empty string when no MD5 check needed."""
    from app.services.upload_service import _pending_uploads

    upload_id = str(uuid.uuid4())
    _pending_uploads[upload_id] = {
        "org_id": 1,
        "user_id": 1,
        "filename": "big2.fastq.gz",
        "gcs_uri": f"gs://bioaf-ingest-test-abc123/uploads/{upload_id}/big2.fastq.gz",
        "expected_size": None,
        "expected_md5": None,
        "experiment_id": None,
        "sample_ids": [],
    }

    resp = await client.post(
        "/api/files/upload/complete",
        json={"upload_id": upload_id, "actual_md5": ""},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200, resp.text
