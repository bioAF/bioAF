"""Tests for Chunk 1: SampleBatch rename from Batch."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.asyncio


async def _create_experiment(client: AsyncClient, token: str) -> int:
    resp = await client.post(
        "/api/experiments",
        json={"name": "Test Experiment"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


# --- Model tests ---


async def test_sample_batch_table_exists(session: AsyncSession):
    """sample_batches table should exist after migration."""
    result = await session.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name = 'sample_batches'"))
    assert result.scalar() == 1


async def test_old_batches_table_gone(session: AsyncSession):
    """The old 'batches' table should no longer exist."""
    result = await session.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name = 'batches'"))
    assert result.scalar() is None


async def test_sample_batch_model_fields(session: AsyncSession):
    """SampleBatch should have all expected columns including instrument fields."""
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sample_batches' ORDER BY column_name"
        )
    )
    columns = {row[0] for row in result.fetchall()}
    assert {
        "id",
        "experiment_id",
        "name",
        "prep_date",
        "operator_user_id",
        "sequencer_run_id",
        "instrument_model",
        "instrument_platform",
        "quality_score_encoding",
        "notes",
        "created_at",
        "updated_at",
    }.issubset(columns)


async def test_sample_has_sample_batch_id_fk(session: AsyncSession):
    """Sample should have sample_batch_id, not batch_id."""
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'samples' AND column_name = 'sample_batch_id'"
        )
    )
    assert result.scalar() == "sample_batch_id"

    # Old column should be gone
    result2 = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'samples' AND column_name = 'batch_id'"
        )
    )
    assert result2.scalar() is None


# --- API tests ---


async def test_old_batch_endpoints_404(client: AsyncClient, admin_token: str):
    """Old /api/batches/ paths should return 404."""
    resp = await client.get(
        "/api/batches/1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


async def test_old_experiment_batches_endpoint_404(client: AsyncClient, admin_token: str):
    """Old /api/experiments/{id}/batches should return 404."""
    exp_id = await _create_experiment(client, admin_token)
    resp = await client.get(
        f"/api/experiments/{exp_id}/batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Should be 404 or method not allowed since the old route is removed
    assert resp.status_code in (404, 405)


async def test_new_sample_batch_list_endpoint(client: AsyncClient, admin_token: str):
    """GET /api/experiments/{id}/sample-batches should work."""
    exp_id = await _create_experiment(client, admin_token)
    resp = await client.get(
        f"/api/experiments/{exp_id}/sample-batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_new_sample_batch_create_endpoint(client: AsyncClient, admin_token: str):
    """POST /api/experiments/{id}/sample-batches should create a sample batch."""
    exp_id = await _create_experiment(client, admin_token)
    resp = await client.post(
        f"/api/experiments/{exp_id}/sample-batches",
        json={"name": "SB-001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "SB-001"
    assert "id" in data
    # Instrument fields should be present (nullable)
    assert "instrument_model" in data
    assert "sequencer_run_id" in data


async def test_new_sample_batch_get_endpoint(client: AsyncClient, admin_token: str):
    """GET /api/sample-batches/{id} should return the batch."""
    exp_id = await _create_experiment(client, admin_token)
    create_resp = await client.post(
        f"/api/experiments/{exp_id}/sample-batches",
        json={"name": "SB-002"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/sample-batches/{batch_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "SB-002"


async def test_new_sample_batch_update_endpoint(client: AsyncClient, admin_token: str):
    """PATCH /api/sample-batches/{id} should update the batch."""
    exp_id = await _create_experiment(client, admin_token)
    create_resp = await client.post(
        f"/api/experiments/{exp_id}/sample-batches",
        json={"name": "SB-003"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/sample-batches/{batch_id}",
        json={"name": "SB-003-updated", "notes": "Updated notes"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "SB-003-updated"


async def test_assign_samples_endpoint(client: AsyncClient, admin_token: str):
    """POST /api/sample-batches/{id}/assign-samples should work."""
    exp_id = await _create_experiment(client, admin_token)
    # Create a batch
    batch_resp = await client.post(
        f"/api/experiments/{exp_id}/sample-batches",
        json={"name": "SB-004"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = batch_resp.json()["id"]

    # Create a sample
    sample_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "SAMPLE001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = sample_resp.json()["id"]

    # Assign
    resp = await client.post(
        f"/api/sample-batches/{batch_id}/assign-samples",
        json={"sample_ids": [sample_id]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned"] == 1
