"""TDD: POST /api/references/import, GET /import-status, POST /import-cancel."""

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
        email="compbio_importroute@test.com",
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
        email="bench_importroute@test.com",
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


def _stub_create_job(*, reference_id, **_):
    return f"refimport-{reference_id}-stub"


VALID_IMPORT = {
    "name": "GENCODE",
    "category": "annotation",
    "scope": "internal",
    "version": "v45",
    "source_url": "https://ftp.example/gencode.v45.annotation.gtf.gz",
    "extract": "gzip",
}


@pytest.mark.asyncio
async def test_import_route_starts_job(client, comp_bio_token, configured_refs_bucket):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        response = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "reference_id" in body
    assert body["status"] == "pending"
    assert body["import_job_id"].startswith("refimport-")


@pytest.mark.asyncio
async def test_import_route_rejects_bench(client, bench_token, configured_refs_bucket):
    response = await client.post(
        "/api/references/import",
        json=VALID_IMPORT,
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_import_route_409_on_duplicate(client, comp_bio_token, configured_refs_bucket):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        first = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_import_status_route_returns_progress(client, comp_bio_token, configured_refs_bucket, session):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        init = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    ref_id = init.json()["reference_id"]

    response = await client.get(
        f"/api/references/{ref_id}/import-status",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["reference_id"] == ref_id


@pytest.mark.asyncio
async def test_import_status_404_when_unknown(client, comp_bio_token, configured_refs_bucket):
    response = await client.get(
        "/api/references/999999/import-status",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_import_cancel_route_purges(client, comp_bio_token, configured_refs_bucket, session):
    with patch.object(ReferenceDataService, "_create_import_job", side_effect=_stub_create_job):
        init = await client.post(
            "/api/references/import",
            json=VALID_IMPORT,
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    ref_id = init.json()["reference_id"]

    with (
        patch.object(ReferenceDataService, "_delete_import_job", return_value=None),
        patch.object(ReferenceDataService, "_delete_blobs", return_value=None),
    ):
        response = await client.post(
            f"/api/references/{ref_id}/import-cancel",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 204

    result = await session.execute(text("SELECT id FROM reference_datasets WHERE id = :id"), {"id": ref_id})
    assert result.scalar_one_or_none() is None
