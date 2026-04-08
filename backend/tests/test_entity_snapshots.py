"""Tests for entity snapshots (metadata provenance)."""

import pytest
from sqlalchemy import select

from app.models.entity_snapshot import EntitySnapshot

pytestmark = pytest.mark.asyncio


async def test_sample_create_creates_snapshot(client, admin_token, session):
    """Creating a sample should produce an entity snapshot."""
    # Create experiment
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Snapshot Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]

    # Create sample
    sample_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "SNAP001", "organism": "Human"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert sample_resp.status_code == 200
    sample_id = sample_resp.json()["id"]

    # Check snapshot was created
    result = await session.execute(
        select(EntitySnapshot).where(
            EntitySnapshot.entity_type == "sample",
            EntitySnapshot.entity_id == sample_id,
        )
    )
    snapshots = result.scalars().all()
    assert len(snapshots) >= 1

    snap = snapshots[0]
    assert snap.snapshot_json["sample_id_unique"] == "SNAP001"
    assert snap.snapshot_json["organism"] == "Human"


async def test_experiment_create_creates_snapshot(client, admin_token, session):
    """Creating an experiment should produce an entity snapshot."""
    resp = await client.post(
        "/api/experiments",
        json={"name": "Snapshot Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    exp_id = resp.json()["id"]

    result = await session.execute(
        select(EntitySnapshot).where(
            EntitySnapshot.entity_type == "experiment",
            EntitySnapshot.entity_id == exp_id,
        )
    )
    snapshots = result.scalars().all()
    assert len(snapshots) >= 1
    assert snapshots[0].snapshot_json["name"] == "Snapshot Experiment"


async def test_sample_update_creates_new_snapshot(client, admin_token, session):
    """Updating a sample should create a new snapshot with updated state."""
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Update Snap Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]

    sample_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "SNAP002"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = sample_resp.json()["id"]

    # Update the sample
    await client.patch(
        f"/api/samples/{sample_id}",
        json={"organism": "Mouse"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Should have 2 snapshots: create + update
    result = await session.execute(
        select(EntitySnapshot)
        .where(
            EntitySnapshot.entity_type == "sample",
            EntitySnapshot.entity_id == sample_id,
        )
        .order_by(EntitySnapshot.created_at)
    )
    snapshots = result.scalars().all()
    assert len(snapshots) >= 2

    # Latest snapshot should reflect the update
    latest = snapshots[-1]
    assert latest.snapshot_json["organism"] == "Mouse"
