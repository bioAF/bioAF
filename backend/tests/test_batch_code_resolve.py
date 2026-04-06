"""Tests for batch code find-or-create on sample create/update."""

import pytest
from sqlalchemy import select

from app.models.sample_batch import SampleBatch
from app.models.sequencing_batch import SequencingBatch

pytestmark = pytest.mark.asyncio


async def _create_experiment(client, token) -> int:
    resp = await client.post(
        "/api/experiments",
        json={"name": "Batch Code Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json()["id"]


async def test_sample_batch_code_creates_batch(client, admin_token, session):
    """Creating a sample with sample_batch_code should find-or-create a SampleBatch."""
    exp_id = await _create_experiment(client, admin_token)

    resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S001", "sample_batch_code": "prep-42"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_batch"] is not None
    assert data["sample_batch"]["name"] == "prep-42"

    # Verify SampleBatch was created in DB
    result = await session.execute(
        select(SampleBatch).where(SampleBatch.name == "prep-42", SampleBatch.experiment_id == exp_id)
    )
    batch = result.scalar_one_or_none()
    assert batch is not None


async def test_sample_batch_code_reuses_existing(client, admin_token, session):
    """Creating two samples with the same sample_batch_code reuses the same batch."""
    exp_id = await _create_experiment(client, admin_token)

    await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S002", "sample_batch_code": "prep-42"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S003", "sample_batch_code": "prep-42"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    result = await session.execute(
        select(SampleBatch).where(SampleBatch.name == "prep-42", SampleBatch.experiment_id == exp_id)
    )
    batches = result.scalars().all()
    assert len(batches) == 1


async def test_sequencing_batch_code_creates_batch(client, admin_token, session):
    """Creating a sample with sequencing_batch_code should find-or-create a SequencingBatch."""
    exp_id = await _create_experiment(client, admin_token)

    resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S004", "sequencing_batch_code": "cro123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sequencing_batch"] is not None
    assert data["sequencing_batch"]["code"] == "cro123"


async def test_sequencing_batch_code_shared_across_experiments(client, admin_token, session):
    """SequencingBatch with same code across experiments should be the same record."""
    exp1 = await _create_experiment(client, admin_token)
    exp2_resp = await client.post(
        "/api/experiments",
        json={"name": "Batch Code Test 2"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp2 = exp2_resp.json()["id"]

    await client.post(
        f"/api/experiments/{exp1}/samples",
        json={"sample_id_external": "S005", "sequencing_batch_code": "cro-shared"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/api/experiments/{exp2}/samples",
        json={"sample_id_external": "S006", "sequencing_batch_code": "cro-shared"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    result = await session.execute(select(SequencingBatch).where(SequencingBatch.code == "cro-shared"))
    batches = result.scalars().all()
    assert len(batches) == 1


async def test_update_sample_with_batch_codes(client, admin_token, session):
    """Updating a sample with new batch codes should resolve them."""
    exp_id = await _create_experiment(client, admin_token)

    create_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S007"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/samples/{sample_id}",
        json={"sample_batch_code": "new-prep", "sequencing_batch_code": "new-seq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Re-fetch via GET to ensure relationships are loaded
    get_resp = await client.get(
        f"/api/samples/{sample_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = get_resp.json()
    assert data["sample_batch"] is not None
    assert data["sample_batch"]["name"] == "new-prep"
    assert data["sequencing_batch"] is not None
    assert data["sequencing_batch"]["code"] == "new-seq"


async def test_no_batch_code_leaves_null(client, admin_token):
    """Creating a sample without batch codes leaves both null."""
    exp_id = await _create_experiment(client, admin_token)

    resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S008"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_batch"] is None
    assert data["sequencing_batch"] is None
