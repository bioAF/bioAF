"""Tests for short-lived content tokens (pentest finding #5).

Content tokens replace full session JWTs in URL query parameters for
file/plot content endpoints used by <img> tags. They are scoped to a
single resource, expire quickly, and carry no user identity.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient


@pytest_asyncio.fixture
async def sample_file(session, admin_user):
    from app.models.file import File

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/test-image.png",
        filename="test-image.png",
        size_bytes=1024,
        md5_checksum="abc123",
        file_type="image",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return f


# -- Token creation endpoint --


@pytest.mark.asyncio
async def test_create_content_token_for_file(client: AsyncClient, admin_token: str, sample_file):
    """POST /api/content-tokens returns a short-lived token for a file."""
    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "file", "resource_id": sample_file.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["expires_in"] <= 60


@pytest.mark.asyncio
async def test_create_content_token_for_plot(client: AsyncClient, admin_token: str, session, admin_user):
    """POST /api/content-tokens returns a token for a plot thumbnail."""
    from app.models.file import File
    from app.models.plot_archive_entry import PlotArchiveEntry

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/plot.png",
        filename="plot.png",
        size_bytes=500,
        md5_checksum="plot123",
        file_type="png",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        thumbnail_gcs_uri="gs://bucket/plot_thumb.png",
    )
    session.add(plot)
    await session.flush()
    await session.commit()

    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "plot_thumbnail", "resource_id": plot.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert "token" in resp.json()


@pytest.mark.asyncio
async def test_create_content_token_requires_auth(client: AsyncClient, sample_file):
    """Unauthenticated callers cannot create content tokens."""
    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "file", "resource_id": sample_file.id},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_content_token_invalid_resource_type(client: AsyncClient, admin_token: str):
    """Invalid resource_type is rejected."""
    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "users", "resource_id": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_content_token_nonexistent_file(client: AsyncClient, admin_token: str):
    """Token creation fails for a file that does not exist."""
    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "file", "resource_id": 99999},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


# -- Token usage on content endpoints --


def _mock_gcs_content():
    mock_client_cls = MagicMock()
    mock_blob = mock_client_cls.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"\x89PNG fake image"
    return (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


@pytest.mark.asyncio
async def test_content_endpoint_accepts_content_token(client: AsyncClient, admin_token: str, sample_file):
    """File content endpoint accepts a content token in the query param."""
    # Step 1: get a content token
    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "file", "resource_id": sample_file.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    content_token = resp.json()["token"]

    # Step 2: use it on the content endpoint
    gcs_patch, creds_patch = _mock_gcs_content()
    with gcs_patch, creds_patch:
        resp = await client.get(f"/api/files/{sample_file.id}/content?token={content_token}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_content_token_rejected_on_wrong_resource(client: AsyncClient, admin_token: str, sample_file, session):
    """A content token for file 1 cannot be used to access file 2."""
    from app.models.file import File

    other_file = File(
        organization_id=sample_file.organization_id,
        gcs_uri="gs://test-bucket/other.png",
        filename="other.png",
        size_bytes=512,
        md5_checksum="def789",
        file_type="image",
        uploader_user_id=sample_file.uploader_user_id,
    )
    session.add(other_file)
    await session.flush()
    await session.commit()

    resp = await client.post(
        "/api/content-tokens",
        json={"resource_type": "file", "resource_id": sample_file.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    content_token = resp.json()["token"]

    gcs_patch, creds_patch = _mock_gcs_content()
    with gcs_patch, creds_patch:
        resp = await client.get(f"/api/files/{other_file.id}/content?token={content_token}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_jwt_rejected_in_content_query_param(client: AsyncClient, admin_token: str, sample_file):
    """Full session JWTs must no longer be accepted as query param tokens."""
    gcs_patch, creds_patch = _mock_gcs_content()
    with gcs_patch, creds_patch:
        resp = await client.get(f"/api/files/{sample_file.id}/content?token={admin_token}")
    assert resp.status_code == 401
