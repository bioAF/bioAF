"""Tests for sequencing batch position auto-assignment and override."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_create_sample_auto_assigns_position(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Creating samples in the same sequencing batch auto-assigns positions 1, 2, 3."""
    # Create project and experiment
    proj = await client.post(
        "/api/projects",
        json={"name": "Batch Pos Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Batch Pos Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    # Create 3 samples with same sequencing batch
    ids = []
    for i in range(3):
        resp = await client.post(
            f"/api/experiments/{exp_id}/samples",
            json={
                "sample_id_unique": f"POS-{i + 1}",
                "sequencing_batch_code": "CCB0001",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        ids.append(resp.json()["id"])

    # Verify positions are 1, 2, 3
    for idx, sample_id in enumerate(ids):
        row = (
            await session.execute(
                text("SELECT sequencing_batch_position FROM samples WHERE id = :id"),
                {"id": sample_id},
            )
        ).fetchone()
        assert row is not None
        assert row[0] == idx + 1, f"Sample {sample_id} expected position {idx + 1}, got {row[0]}"


@pytest.mark.asyncio
async def test_create_sample_explicit_position(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Explicit sequencing_batch_position overrides auto-assignment."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Explicit Pos Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Explicit Pos Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "sample_id_unique": "EXPLICIT-1",
            "sequencing_batch_code": "CCB0010",
            "sequencing_batch_position": 5,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    row = (
        await session.execute(
            text("SELECT sequencing_batch_position FROM samples WHERE id = :id"),
            {"id": resp.json()["id"]},
        )
    ).fetchone()
    assert row is not None
    assert row[0] == 5


@pytest.mark.asyncio
async def test_batch_change_auto_assigns_new_position(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Moving a sample to a different batch auto-assigns a new position."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Batch Change Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Batch Change Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    # Create a sample in batch A with position 1, and another in batch B
    resp_a = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "MOVE-1", "sequencing_batch_code": "BATCH-A"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_a.status_code == 200

    # Pre-populate batch B with one sample so next position is 2
    await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "STAY-B", "sequencing_batch_code": "BATCH-B"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Move first sample to batch B
    sample_id = resp_a.json()["id"]
    resp_update = await client.patch(
        f"/api/samples/{sample_id}",
        json={"sequencing_batch_code": "BATCH-B"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_update.status_code == 200

    row = (
        await session.execute(
            text("SELECT sequencing_batch_position FROM samples WHERE id = :id"),
            {"id": sample_id},
        )
    ).fetchone()
    assert row is not None
    assert row[0] == 2  # Next position in batch B


@pytest.mark.asyncio
async def test_position_gaps_allowed(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Positions 1 and 3 (skipping 2) are valid."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Gap Pos Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Gap Pos Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    resp1 = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "sample_id_unique": "GAP-1",
            "sequencing_batch_code": "GAP-BATCH",
            "sequencing_batch_position": 1,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 200

    resp3 = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "sample_id_unique": "GAP-3",
            "sequencing_batch_code": "GAP-BATCH",
            "sequencing_batch_position": 3,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp3.status_code == 200

    # Both should exist with their specified positions
    for sample_id, expected_pos in [(resp1.json()["id"], 1), (resp3.json()["id"], 3)]:
        row = (
            await session.execute(
                text("SELECT sequencing_batch_position FROM samples WHERE id = :id"),
                {"id": sample_id},
            )
        ).fetchone()
        assert row is not None
        assert row[0] == expected_pos


@pytest.mark.asyncio
async def test_no_batch_means_null_position(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Samples without a sequencing batch have null position."""
    proj = await client.post(
        "/api/projects",
        json={"name": "No Batch Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "No Batch Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "NO-BATCH"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    row = (
        await session.execute(
            text("SELECT sequencing_batch_position FROM samples WHERE id = :id"),
            {"id": resp.json()["id"]},
        )
    ).fetchone()
    assert row is not None
    assert row[0] is None


@pytest.mark.asyncio
async def test_bulk_create_auto_assigns_sequential(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Bulk create assigns positions sequentially within same batch."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Bulk Pos Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Bulk Pos Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    resp = await client.post(
        f"/api/experiments/{exp_id}/samples/bulk",
        json={"samples": [{"sample_id_unique": f"BULK-{i}", "sequencing_batch_code": "BULK-BATCH"} for i in range(5)]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    rows = (
        await session.execute(
            text(
                "SELECT id, sequencing_batch_position FROM samples "
                "WHERE sample_id_external LIKE 'BULK-%' "
                "ORDER BY sequencing_batch_position"
            )
        )
    ).fetchall()
    assert len(rows) == 5
    positions = [r[1] for r in rows]
    assert positions == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_response_includes_position(client: AsyncClient, admin_token: str, session: AsyncSession):
    """SampleResponse includes sequencing_batch_position."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Resp Pos Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Resp Pos Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    create_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "sample_id_unique": "RESP-1",
            "sequencing_batch_code": "RESP-BATCH",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_resp.status_code == 200
    sample_id = create_resp.json()["id"]

    # Fetch the sample and check the response
    get_resp = await client.get(
        f"/api/samples/{sample_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["sequencing_batch_position"] == 1
