"""Tests for storage API endpoints.

Tests:
2. Deploy requires admin
3. Deploy requires terraform_initialized
4. Deploy requires not already deployed
5. Deploy stores bucket names (mocked executor)
15. GET buckets requires auth
16. GET buckets requires storage_deployed
17. GET buckets returns live data
18. File assign endpoint
19. File assign nonexistent file
20. File unlink endpoint
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text


async def _seed_platform_config(session, overrides=None):
    """Seed minimum platform_config for tests."""
    defaults = {
        "gcp_credentials_configured": "true",
        "terraform_initialized": "true",
        "storage_deployed": "false",
        "terraform_state_bucket": "bioaf-tf-state-demo",
    }
    if overrides:
        defaults.update(overrides)

    for key, value in defaults.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


@pytest.mark.asyncio
async def test_storage_deploy_requires_admin(client, session, admin_user, viewer_token):
    """Deploy endpoint requires admin role."""
    await _seed_platform_config(session)
    resp = await client.post(
        "/api/v1/infrastructure/storage/deploy",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_storage_deploy_requires_terraform_initialized(client, session, admin_user, admin_token):
    """Deploy fails when terraform not initialized."""
    await _seed_platform_config(session, {"terraform_initialized": "false"})
    resp = await client.post(
        "/api/v1/infrastructure/storage/deploy",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "initialized" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_storage_deploy_requires_not_already_deployed(client, session, admin_user, admin_token):
    """Deploy fails when storage already deployed."""
    await _seed_platform_config(session, {"storage_deployed": "true"})
    resp = await client.post(
        "/api/v1/infrastructure/storage/deploy",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409
    assert "already" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_storage_deploy_stores_bucket_names(client, session, admin_user, admin_token):
    """Mock executor to return success with bucket outputs. Assert config updated."""
    await _seed_platform_config(session)

    # Mock the executor to simulate successful plan+apply
    async def mock_deploy_storage(sess, user_id):
        # Simulate the post-apply hook storing bucket names
        for key, value in [
            ("storage_deployed", "true"),
            ("ingest_bucket_name", "bioaf-ingest-testorg"),
            ("raw_bucket_name", "bioaf-raw-testorg"),
            ("working_bucket_name", "bioaf-working-testorg"),
            ("results_bucket_name", "bioaf-results-testorg"),
            ("config_backups_bucket_name", "bioaf-config-backups-testorg"),
        ]:
            await sess.execute(
                text(
                    "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                ).bindparams(k=key, v=value)
            )
        await sess.commit()
        return {"status": "completed"}

    with patch("app.api.storage_deploy.deploy_storage_module", new=mock_deploy_storage):
        resp = await client.post(
            "/api/v1/infrastructure/storage/deploy",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    # Verify bucket names stored
    rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('storage_deployed', 'ingest_bucket_name', 'raw_bucket_name')"
            )
        )
    ).fetchall()
    config = {r[0]: r[1] for r in rows}
    assert config.get("storage_deployed") == "true"
    assert config.get("ingest_bucket_name") == "bioaf-ingest-testorg"


@pytest.mark.asyncio
async def test_get_buckets_requires_auth(client, session, admin_user):
    """No token returns 401."""
    resp = await client.get("/api/v1/infrastructure/storage/buckets")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_buckets_requires_storage_deployed(client, session, admin_user, admin_token):
    """Returns 400 when storage not deployed."""
    await _seed_platform_config(session, {"storage_deployed": "false"})
    resp = await client.get(
        "/api/v1/infrastructure/storage/buckets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "not been deployed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_buckets_returns_live_data(client, session, admin_user, admin_token):
    """Mock GCS service, call endpoint. Assert 200 with all 7 buckets."""
    await _seed_platform_config(
        session,
        {
            "storage_deployed": "true",
            "ingest_bucket_name": "bioaf-ingest-demo",
            "raw_bucket_name": "bioaf-raw-demo",
            "working_bucket_name": "bioaf-working-demo",
            "results_bucket_name": "bioaf-results-demo",
            "references_bucket_name": "bioaf-references-demo",
            "literature_bucket_name": "bioaf-literature-demo",
            "config_backups_bucket_name": "bioaf-config-backups-demo",
        },
    )

    from app.services.gcs_storage import BucketMetrics

    mock_metrics = [
        BucketMetrics(
            bucket_name=f"bioaf-{p}-demo",
            purpose=p,
            size_bytes=1024,
            object_count=5,
            storage_class="STANDARD",
            versioning_enabled=True,
            lifecycle_rules=[],
        )
        for p in [
            "ingest",
            "raw",
            "working",
            "results",
            "references",
            "literature",
            "config_backups",
        ]
    ]

    with patch("app.api.storage_deploy.GcsStorageService") as mock_svc:
        mock_svc.get_bucket_metrics = AsyncMock(return_value=mock_metrics)
        resp = await client.get(
            "/api/v1/infrastructure/storage/buckets",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["buckets"]) == 7


@pytest.mark.asyncio
async def test_file_assign_endpoint(client, session, admin_user, admin_token):
    """Assign file to experiment returns 200."""
    from app.models.experiment import Experiment

    exp = Experiment(
        name="Assign Test Exp",
        owner_user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    file_id_row = await session.execute(
        text("""
        INSERT INTO files (organization_id, gcs_uri, storage_uri, filename, file_type)
        VALUES (:org_id, 'gs://bioaf-raw-demo/unlinked/test.fastq.gz', 'gs://bioaf-raw-demo/unlinked/test.fastq.gz', 'test.fastq.gz', 'fastq')
        RETURNING id
        """).bindparams(org_id=admin_user.organization_id)
    )
    file_id = file_id_row.scalar_one()
    await session.commit()

    with patch("app.api.storage_deploy.FileOrganizationService") as mock_svc:
        mock_svc.assign_file_to_experiment = AsyncMock()
        resp = await client.post(
            f"/api/v1/files/{file_id}/assign",
            json={"experiment_id": exp.id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_file_assign_nonexistent_file(client, session, admin_user, admin_token):
    """Assign nonexistent file returns 404."""
    with patch("app.api.storage_deploy.FileOrganizationService") as mock_svc:
        mock_svc.assign_file_to_experiment = AsyncMock(side_effect=ValueError("File 99999 not found"))
        resp = await client.post(
            "/api/v1/files/99999/assign",
            json={"experiment_id": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_file_unlink_endpoint(client, session, admin_user, admin_token):
    """Unlink file returns 200."""
    file_id_row = await session.execute(
        text("""
        INSERT INTO files (organization_id, gcs_uri, storage_uri, filename, file_type, experiment_id)
        VALUES (:org_id, 'gs://bioaf-raw-demo/experiments/1/test.fastq.gz', 'gs://bioaf-raw-demo/experiments/1/test.fastq.gz', 'test.fastq.gz', 'fastq', NULL)
        RETURNING id
        """).bindparams(org_id=admin_user.organization_id)
    )
    file_id = file_id_row.scalar_one()
    await session.commit()

    with patch("app.api.storage_deploy.FileOrganizationService") as mock_svc:
        mock_svc.unlink_file_from_experiment = AsyncMock()
        resp = await client.post(
            f"/api/v1/files/{file_id}/unlink",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
