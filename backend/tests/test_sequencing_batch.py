"""Tests for Chunk 2: POBatch (sequencing batch) and ManifestEntry models."""

import pytest

pytestmark = pytest.mark.asyncio


async def _create_experiment(client, token) -> int:
    resp = await client.post(
        "/api/experiments",
        json={"name": "Seq Batch Experiment"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


# --- Model tests via API ---


async def test_create_sequencing_batch(client, admin_token, session):
    resp = await client.post(
        "/api/sequencing-batches",
        json={"name": "Run 2026-04-01", "code": "SEQ-2026-0042"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Run 2026-04-01"
    assert data["code"] == "SEQ-2026-0042"
    assert data["status"] == "pending"


async def test_get_sequencing_batch(client, admin_token):
    create_resp = await client.post(
        "/api/sequencing-batches",
        json={"name": "Run 2", "code": "SEQ-002"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/sequencing-batches/{batch_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "SEQ-002"
    assert "manifest_entries" in resp.json()


async def test_list_sequencing_batches(client, admin_token):
    await client.post(
        "/api/sequencing-batches",
        json={"name": "List Run", "code": "SEQ-LIST-001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = await client.get(
        "/api/sequencing-batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert any(b["code"] == "SEQ-LIST-001" for b in resp.json())


async def test_update_sequencing_batch(client, admin_token):
    create_resp = await client.post(
        "/api/sequencing-batches",
        json={"name": "Upd Run", "code": "SEQ-UPD-001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/sequencing-batches/{batch_id}",
        json={"instrument_model": "NovaSeq 6000", "notes": "Updated"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["instrument_model"] == "NovaSeq 6000"
    assert resp.json()["notes"] == "Updated"


async def test_sequencing_batch_status_transitions(client, admin_token):
    """POBatch status should be settable."""
    create_resp = await client.post(
        "/api/sequencing-batches",
        json={"name": "Status Run", "code": "SEQ-ST-001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "pending"

    # Update to ingesting
    resp = await client.patch(
        f"/api/sequencing-batches/{batch_id}",
        json={"status": "ingesting"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ingesting"


async def test_experiment_sequencing_batches(client, admin_token, session):
    """GET /api/experiments/{id}/sequencing-batches returns batches with samples from that experiment."""
    exp_id = await _create_experiment(client, admin_token)

    # Create a sample in the experiment
    sample_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "SB_SAMPLE001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = sample_resp.json()["id"]

    # Create a sequencing batch
    batch_resp = await client.post(
        "/api/sequencing-batches",
        json={"name": "Exp Run", "code": "SEQ-EXP-001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    batch_id = batch_resp.json()["id"]

    # Link sample to sequencing batch via the sample update
    from sqlalchemy import text

    await session.execute(
        text("UPDATE samples SET sequencing_batch_id = :bid WHERE id = :sid"),
        {"bid": batch_id, "sid": sample_id},
    )
    await session.commit()

    resp = await client.get(
        f"/api/experiments/{exp_id}/sequencing-batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    batches = resp.json()
    assert any(b["id"] == batch_id for b in batches)


# --- Column existence tests ---


async def test_sample_has_sequencing_batch_id(session):
    """Sample table should have sequencing_batch_id column."""
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'samples' AND column_name = 'sequencing_batch_id'"
        )
    )
    assert result.scalar() == "sequencing_batch_id"


async def test_file_has_sequencing_batch_id(session):
    """File table should have sequencing_batch_id column."""
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'files' AND column_name = 'sequencing_batch_id'"
        )
    )
    assert result.scalar() == "sequencing_batch_id"


async def test_manifest_entries_table_exists(session):
    """manifest_entries table should exist."""
    from sqlalchemy import text

    result = await session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'manifest_entries'")
    )
    assert result.scalar() == 1
