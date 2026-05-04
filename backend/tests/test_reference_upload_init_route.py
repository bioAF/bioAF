"""TDD: POST /api/references/upload-init route — spec §2.

Wraps ReferenceDataService.init_upload behind a FastAPI endpoint. Permission:
`references:upload`. The browser uses the response to PUT bytes to GCS directly.
"""

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
        email="compbio_uploadroute@test.com",
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
        email="bench_uploadroute@test.com",
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
    return (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket_name}/o"
        f"?uploadType=resumable&upload_id=stub-{blob_path.replace('/', '-')}"
    )


VALID_PAYLOAD = {
    "name": "GRCh38 GENCODE",
    "category": "genome",
    "scope": "public",
    "version": "v45",
    "source_url": "https://www.gencodegenes.org/human/release_45.html",
    "files": [
        {"filename": "genome.fa", "size_bytes": 2_500_000_000},
        {"filename": "genes.gtf", "size_bytes": 500_000_000, "content_type": "text/plain"},
    ],
}


@pytest.mark.asyncio
async def test_upload_init_happy_path(client, comp_bio_token, configured_refs_bucket):
    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        response = await client.post(
            "/api/references/upload-init",
            json=VALID_PAYLOAD,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert "reference_id" in data
    assert data["gcs_prefix"].endswith("/")
    assert "v45" in data["gcs_prefix"]
    assert len(data["uploads"]) == 2
    filenames = {u["filename"] for u in data["uploads"]}
    assert filenames == {"genome.fa", "genes.gtf"}
    for slot in data["uploads"]:
        assert slot["session_url"].startswith("https://storage.googleapis.com/")
        assert "expires_at" in slot


@pytest.mark.asyncio
async def test_upload_init_rejects_bench(client, bench_token, configured_refs_bucket):
    """bench role lacks references:upload."""
    response = await client.post(
        "/api/references/upload-init",
        json=VALID_PAYLOAD,
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_upload_init_returns_409_on_duplicate(client, comp_bio_token, configured_refs_bucket):
    with patch.object(ReferenceDataService, "_create_resumable_session", side_effect=_stub_session_url):
        # first call ok
        first = await client.post(
            "/api/references/upload-init",
            json=VALID_PAYLOAD,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
        assert first.status_code == 200

        # second call (same name+version) -> 409
        second = await client.post(
            "/api/references/upload-init",
            json=VALID_PAYLOAD,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_upload_init_503_when_bucket_missing(client, comp_bio_token, session):
    """If references bucket isn't configured, return 503 with clear message."""
    await session.execute(text("DELETE FROM platform_config WHERE key = 'references_bucket_name'"))
    await session.commit()

    response = await client.post(
        "/api/references/upload-init",
        json=VALID_PAYLOAD,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_init_400_on_empty_files(client, comp_bio_token, configured_refs_bucket):
    payload = {**VALID_PAYLOAD, "files": []}
    response = await client.post(
        "/api/references/upload-init",
        json=payload,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
