import io
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


def _storage_adapter():
    """Adapter mock for the upload + file-organization flows (Phase 3).

    The upload endpoints mint signed URLs / stream uploads via the BAL storage
    adapter, and file-linking moves objects through it; both go through this one
    adapter. move() echoes the destination URI.
    """
    adapter = AsyncMock()
    adapter.generate_signed_url.return_value = "https://storage.googleapis.com/fake-signed-url"
    adapter.move.side_effect = lambda src, dst: dst
    # resolve_uri is the backend-neutral URI factory (Phase 7); echo a realistic
    # store-scoped URI so callers that build write URIs through it get a string.
    adapter.resolve_uri.side_effect = lambda store, key: f"gs://bioaf-{store.value}-test/{key}"
    return adapter


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
    with patch("app.adapters.registry.get_storage_adapter", return_value=_storage_adapter()):
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

    with patch("app.adapters.registry.get_storage_adapter", return_value=_storage_adapter()):
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

    with patch("app.adapters.registry.get_storage_adapter", return_value=_storage_adapter()):
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
    adapter = _storage_adapter()
    adapter.upload_file.side_effect = Exception("403 Provided scope(s) are not authorized")

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
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
async def test_simple_upload_routes_through_storage_adapter(client, admin_token, configured_ingest_bucket, session):
    """simple_upload streams the file to storage via the adapter (which owns
    credential resolution internally after Phase 3)."""
    adapter = _storage_adapter()

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("creds_test.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    adapter.upload_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_simple_upload_resolves_uri_via_adapter(client, admin_token, configured_ingest_bucket, session):
    """simple_upload builds its write URI through the adapter's resolve_uri
    (backend-neutral), so the proxied upload path works on NFS as well as GCS
    rather than hardcoding a gs:// URI (Phase 7)."""
    from app.adapters.models import StorageStore

    adapter = _storage_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("neutral.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    adapter.resolve_uri.assert_awaited_once()
    store_arg = adapter.resolve_uri.call_args.args[0]
    assert store_arg == StorageStore.INGEST
    # The URI handed to upload_file is the one resolve_uri produced.
    upload_uri = adapter.upload_file.call_args.args[0]
    assert upload_uri.startswith("gs://bioaf-ingest-test/")


@pytest.mark.asyncio
async def test_simple_upload_streams_file_without_buffering(client, admin_token, configured_ingest_bucket, session):
    """simple_upload must pass a file-like object to the adapter, not bytes.

    This prevents OOM crashes when uploading large FASTQ files.
    """
    captured = {}

    async def fake_upload(uri, file_obj, *, content_type=None):
        # file_obj must be a readable IO object, not bytes
        assert hasattr(file_obj, "read"), "Expected a file-like object, got bytes or other type"
        captured["file_obj"] = file_obj

    adapter = _storage_adapter()
    adapter.upload_file.side_effect = fake_upload

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
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
async def test_signed_upload_links_experiment_id(client, admin_token, configured_ingest_bucket, session):
    """Signed upload flow (initiate -> complete) must persist experiment_id on the file record."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.user import User
    from sqlalchemy import select

    user = (await session.execute(select(User).limit(1))).scalar_one()
    exp = Experiment(
        organization_id=user.organization_id,
        name="Signed Upload Link Test",
        owner_user_id=user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await session.commit()

    with patch("app.adapters.registry.get_storage_adapter", return_value=_storage_adapter()):
        initiate_resp = await client.post(
            "/api/files/upload/initiate",
            json={
                "filename": "linked.fastq.gz",
                "expected_size_bytes": 5000,
                "experiment_id": exp.id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert initiate_resp.status_code == 200
        upload_id = initiate_resp.json()["upload_id"]

        complete_resp = await client.post(
            "/api/files/upload/complete",
            json={"upload_id": upload_id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["experiment_id"] == exp.id

    file_row = (await session.execute(select(File).where(File.filename == "linked.fastq.gz"))).scalar_one_or_none()
    assert file_row is not None
    assert file_row.experiment_id == exp.id


@pytest.mark.asyncio
async def test_signed_upload_moves_file_to_raw_bucket(client, admin_token, configured_ingest_bucket, session):
    """Signed upload with experiment_id must move the file from ingest to raw bucket."""
    from app.models.component import PlatformConfig
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.user import User
    from sqlalchemy import select

    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test-abc123"))
    session.add(PlatformConfig(key="gcp_credential_source", value="vm_default"))
    await session.flush()
    await session.commit()

    user = (await session.execute(select(User).limit(1))).scalar_one()
    exp = Experiment(
        organization_id=user.organization_id,
        name="Move Test",
        owner_user_id=user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await session.commit()

    adapter = _storage_adapter()

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        initiate_resp = await client.post(
            "/api/files/upload/initiate",
            json={
                "filename": "moved.fastq.gz",
                "expected_size_bytes": 5000,
                "experiment_id": exp.id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert initiate_resp.status_code == 200
        upload_id = initiate_resp.json()["upload_id"]

        complete_resp = await client.post(
            "/api/files/upload/complete",
            json={"upload_id": upload_id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert complete_resp.status_code == 200

    # File should have been moved from ingest to raw bucket via the adapter
    adapter.move.assert_awaited_once()
    src, dest = adapter.move.call_args.args
    assert "uploads/" in src
    assert f"experiments/{exp.id}/" in dest
    assert "bioaf-raw-test-abc123" in dest

    # DB record should have the new URI
    file_row = (await session.execute(select(File).where(File.filename == "moved.fastq.gz"))).scalar_one_or_none()
    assert file_row is not None
    assert f"experiments/{exp.id}/" in file_row.gcs_uri
    assert "uploads/" not in file_row.gcs_uri


@pytest.mark.asyncio
async def test_signed_upload_without_experiment_id(client, admin_token, configured_ingest_bucket, session):
    """Signed upload flow without experiment_id must leave experiment_id as None."""
    from app.models.file import File
    from sqlalchemy import select

    with patch("app.adapters.registry.get_storage_adapter", return_value=_storage_adapter()):
        initiate_resp = await client.post(
            "/api/files/upload/initiate",
            json={"filename": "nolink.fastq.gz", "expected_size_bytes": 5000},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert initiate_resp.status_code == 200
    upload_id = initiate_resp.json()["upload_id"]

    complete_resp = await client.post(
        "/api/files/upload/complete",
        json={"upload_id": upload_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["experiment_id"] is None

    file_row = (await session.execute(select(File).where(File.filename == "nolink.fastq.gz"))).scalar_one_or_none()
    assert file_row is not None
    assert file_row.experiment_id is None


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
