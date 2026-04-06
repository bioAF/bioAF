import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def experiment_id(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Batch Test Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_sample_batch(client, admin_token, experiment_id, session):
    response = await client.post(
        f"/api/experiments/{experiment_id}/sample-batches",
        json={"name": "Batch 1", "notes": "First batch"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Batch 1"
    assert data["sample_count"] == 0

    # Verify audit
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'sample_batch' AND action = 'create'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_update_sample_batch(client, admin_token, experiment_id, session):
    resp = await client.post(
        f"/api/experiments/{experiment_id}/sample-batches",
        json={"name": "Original Batch"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = resp.json()["id"]

    response = await client.patch(
        f"/api/sample-batches/{batch_id}",
        json={"name": "Updated Batch"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Batch"

    # Verify audit
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'sample_batch' AND action = 'update'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_assign_samples_to_sample_batch(client, admin_token, experiment_id, session):
    # Create batch
    batch_resp = await client.post(
        f"/api/experiments/{experiment_id}/sample-batches",
        json={"name": "Assignment Batch"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = batch_resp.json()["id"]

    # Create samples
    s1 = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "AS001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    s2 = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "AS002"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assign samples
    response = await client.post(
        f"/api/sample-batches/{batch_id}/assign-samples",
        json={"sample_ids": [s1.json()["id"], s2.json()["id"]]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["assigned"] == 2

    # Verify batch now has samples
    batch = await client.get(
        f"/api/sample-batches/{batch_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert batch.json()["sample_count"] == 2

    # Verify audit
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE entity_type = 'sample' AND action = 'batch_assignment'")
    )
    assert result.scalar() >= 2


@pytest.mark.asyncio
async def test_list_sample_batches_with_sample_counts(client, admin_token, experiment_id):
    batch_resp = await client.post(
        f"/api/experiments/{experiment_id}/sample-batches",
        json={"name": "Count Batch"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = batch_resp.json()["id"]

    # Add samples to batch
    await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "CB001", "sample_batch_code": "Count Batch"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/api/experiments/{experiment_id}/sample-batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    batches = response.json()
    matching = [b for b in batches if b["id"] == batch_id]
    assert len(matching) == 1
    assert matching[0]["sample_count"] >= 1
