"""TDD: POST /api/references/{id}/upload-complete and /abort routes — spec §2."""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService
from app.services.reference_data_service import ReferenceDataService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_uploadcomplete_route@test.com",
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
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench_uploadcomplete_route@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user) -> str:
    return AuthService.create_token(
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
    )


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


def _stub_session_url(bucket_name, blob_path, content_type, size_bytes, origin=None, credentials=None):
    return f"https://storage.googleapis.com/stub/{blob_path}"


class _StubBlob:
    def __init__(self, name: str, size: int, md5: str):
        self.name = name
        self.size = size
        self.md5_hash = md5


VALID_INIT = {
    "name": "Custom Markers",
    "category": "markers",
    "scope": "internal",
    "version": "v1",
    "files": [
        {"filename": "markers.csv", "size_bytes": 4096},
        {"filename": "metadata.json", "size_bytes": 512},
    ],
}


async def _post_init(client, token):
    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        return await client.post(
            "/api/references/upload-init",
            json=VALID_INIT,
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.asyncio
async def test_upload_complete_route_finalizes(client, comp_bio_token, configured_refs_bucket):
    init_resp = await _post_init(client, comp_bio_token)
    assert init_resp.status_code == 200
    ref_id = init_resp.json()["reference_id"]
    prefix = init_resp.json()["gcs_prefix"]

    blobs = [
        _StubBlob(name=f"{prefix}markers.csv", size=4096, md5="aa" * 16),
        _StubBlob(name=f"{prefix}metadata.json", size=512, md5="bb" * 16),
    ]
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs):
        response = await client.post(
            f"/api/references/{ref_id}/upload-complete",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["total_size_bytes"] == 4608
    assert len(body["files"]) == 2


@pytest.mark.asyncio
async def test_upload_complete_route_404_unknown(client, comp_bio_token, configured_refs_bucket):
    response = await client.post(
        "/api/references/99999/upload-complete",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upload_complete_route_400_when_files_missing(client, comp_bio_token, configured_refs_bucket):
    init_resp = await _post_init(client, comp_bio_token)
    ref_id = init_resp.json()["reference_id"]
    prefix = init_resp.json()["gcs_prefix"]

    blobs = [_StubBlob(name=f"{prefix}markers.csv", size=4096, md5="aa" * 16)]  # missing metadata.json
    with patch.object(ReferenceDataService, "_list_uploaded_blobs", return_value=blobs):
        response = await client.post(
            f"/api/references/{ref_id}/upload-complete",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 400
    assert "metadata.json" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_complete_route_rejects_bench(client, comp_bio_token, bench_token, configured_refs_bucket):
    init_resp = await _post_init(client, comp_bio_token)
    ref_id = init_resp.json()["reference_id"]

    response = await client.post(
        f"/api/references/{ref_id}/upload-complete",
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_abort_route_deletes_reference(client, comp_bio_token, session, configured_refs_bucket):
    init_resp = await _post_init(client, comp_bio_token)
    ref_id = init_resp.json()["reference_id"]

    with patch.object(ReferenceDataService, "_delete_blobs", return_value=None):
        response = await client.post(
            f"/api/references/{ref_id}/abort",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )

    assert response.status_code == 204

    # Row gone
    result = await session.execute(text("SELECT id FROM reference_datasets WHERE id = :id"), {"id": ref_id})
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_abort_route_idempotent_on_unknown(client, comp_bio_token, configured_refs_bucket):
    """Aborting a missing reference returns 204 (idempotent per spec)."""
    with patch.object(ReferenceDataService, "_delete_blobs", return_value=None):
        response = await client.post(
            "/api/references/99999/abort",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 204
