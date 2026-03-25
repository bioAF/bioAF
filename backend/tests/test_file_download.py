import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def sample_file(session, admin_user):
    from app.models.file import File

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/test-download.fastq.gz",
        filename="test-download.fastq.gz",
        size_bytes=2048000,
        md5_checksum="def456",
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return f


@pytest_asyncio.fixture
async def other_org_user(session):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Other Org Download", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="other-dl@other.com",
        password_hash=AuthService.hash_password("otherpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def other_org_token(other_org_user) -> str:
    return AuthService.create_token(
        other_org_user.id,
        other_org_user.email,
        other_org_user.role_id,
        other_org_user.organization_id,
        role_name="admin",
    )


def _mock_gcs():
    """Return stacked patches for GCS signed URL generation."""
    mock_client_cls = MagicMock()
    mock_blob = mock_client_cls.return_value.bucket.return_value.blob.return_value
    mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url"

    return (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


@pytest.mark.asyncio
async def test_single_file_download_returns_signed_url(client, admin_token, sample_file):
    """GET /api/files/{id}/download returns a signed URL for an admin user."""
    gcs_patch, creds_patch = _mock_gcs()
    with gcs_patch, creds_patch:
        resp = await client.get(
            f"/api/files/{sample_file.id}/download",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "download_url" in data
    assert data["download_url"] == "https://storage.googleapis.com/signed-url"


@pytest.mark.asyncio
async def test_single_file_download_creates_audit_log(client, admin_token, admin_user, sample_file, session):
    """Download creates an audit log entry with file ID, filename, and size."""
    from sqlalchemy import text

    gcs_patch, creds_patch = _mock_gcs()
    with gcs_patch, creds_patch:
        resp = await client.get(
            f"/api/files/{sample_file.id}/download",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    row = (
        await session.execute(
            text(
                "SELECT entity_type, entity_id, action, details_json "
                "FROM audit_log "
                "WHERE entity_type = 'file' AND action = 'downloaded' AND entity_id = :fid"
            ).bindparams(fid=sample_file.id)
        )
    ).fetchone()

    assert row is not None, "Expected an audit log entry for the download"
    assert row[0] == "file"
    assert row[1] == sample_file.id
    assert row[2] == "downloaded"
    import json

    details = json.loads(row[3]) if isinstance(row[3], str) else row[3]
    assert details["filename"] == "test-download.fastq.gz"
    assert details["file_type"] == "fastq"
    assert details["size_bytes"] == 2048000


@pytest.mark.asyncio
async def test_download_requires_permission(client, viewer_token, sample_file):
    """Viewer-role user gets 403 (missing files:download permission)."""
    resp = await client.get(
        f"/api/files/{sample_file.id}/download",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_download_nonexistent_file_returns_404(client, admin_token):
    """Downloading a nonexistent file returns 404."""
    resp = await client.get(
        "/api/files/99999/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_cross_org_isolation(client, other_org_token, sample_file):
    """User from org A cannot download org B's file."""
    resp = await client.get(
        f"/api/files/{sample_file.id}/download",
        headers={"Authorization": f"Bearer {other_org_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_content_endpoint_does_not_create_audit_log(client, admin_token, sample_file, session):
    """GET /api/files/{id}/content must NOT create audit entries -- it is
    used for inline image display, not user-initiated downloads."""
    from sqlalchemy import text

    mock_client_cls = MagicMock()
    mock_blob = mock_client_cls.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"\x89PNG fake image bytes"

    with (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.get(
            f"/api/files/{sample_file.id}/content",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    row = (
        await session.execute(
            text(
                "SELECT id FROM audit_log WHERE entity_type = 'file' AND action = 'downloaded' AND entity_id = :fid"
            ).bindparams(fid=sample_file.id)
        )
    ).fetchone()

    assert row is None, "Content endpoint should not create audit log entries"
