"""TDD: POST /api/internal/references/{id}/import-progress — importer-container callback.

Authenticated by `X-Internal-Token` matching settings.internal_token rather
than a user JWT (the importer container has no user identity).
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.services.auth_service import AuthService
from app.services.reference_data_service import ReferenceDataService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_callback@test.com",
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
async def configured(session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "test-internal-secret")
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value, updated_at) "
            "VALUES ('references_bucket_name', 'bioaf-references-test', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


def _stub_create_job(*, reference_id, **_):
    return f"refimport-{reference_id}-stub"


VALID_IMPORT = {
    "name": "GENCODE",
    "category": "annotation",
    "scope": "internal",
    "version": "v45",
    "source_url": "https://ftp.example/file.gz",
    "extract": "gzip",
}


@pytest.mark.asyncio
async def test_callback_updates_progress(client, comp_bio_token, configured):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        init = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    ref_id = init.json()["reference_id"]

    response = await client.post(
        f"/api/internal/references/{ref_id}/import-progress",
        json={
            "status": "downloading",
            "progress_pct": 33,
            "bytes_downloaded": 333,
            "total_bytes": 1000,
        },
        headers={"X-Internal-Token": "test-internal-secret"},
    )
    assert response.status_code == 200, response.text

    # Status endpoint reflects the update
    status = await client.get(
        f"/api/references/{ref_id}/import-status",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert status.json()["status"] == "downloading"
    assert status.json()["progress_pct"] == 33


@pytest.mark.asyncio
async def test_callback_rejects_missing_token(client, comp_bio_token, configured):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        init = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    ref_id = init.json()["reference_id"]

    response = await client.post(
        f"/api/internal/references/{ref_id}/import-progress",
        json={"status": "downloading"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_callback_rejects_wrong_token(client, comp_bio_token, configured):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        init = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    ref_id = init.json()["reference_id"]

    response = await client.post(
        f"/api/internal/references/{ref_id}/import-progress",
        json={"status": "downloading"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_callback_failed_status_marks_dataset_failed(client, comp_bio_token, configured, session):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        init = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    ref_id = init.json()["reference_id"]

    response = await client.post(
        f"/api/internal/references/{ref_id}/import-progress",
        json={"status": "failed", "error_message": "404 from upstream"},
        headers={"X-Internal-Token": "test-internal-secret"},
    )
    assert response.status_code == 200

    result = await session.execute(text("SELECT status FROM reference_datasets WHERE id = :id"), {"id": ref_id})
    assert result.scalar_one() == "failed"
