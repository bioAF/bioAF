import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def experiment_id(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Sample Test Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return resp.json()["id"]


@pytest_asyncio.fixture
async def template_experiment_id(client, admin_token):
    # Create template with required fields
    resp = await client.post(
        "/api/templates",
        json={
            "name": "Required Fields Template",
            "required_fields_json": {"sample_fields": ["organism", "tissue_type"]},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = resp.json()["id"]

    resp = await client.post(
        "/api/experiments",
        json={"name": "Template Experiment", "template_id": template_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_sample(client, admin_token, experiment_id, session):
    response = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "S001", "organism": "Human", "tissue_type": "Brain"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sample_id_external"] == "S001"
    assert data["status"] == "registered"

    # Verify audit
    result = await session.execute(text("SELECT * FROM audit_log WHERE entity_type = 'sample' AND action = 'create'"))
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_bulk_create_samples(client, admin_token, experiment_id, session):
    response = await client.post(
        f"/api/experiments/{experiment_id}/samples/bulk",
        json={
            "samples": [
                {"sample_id_external": "B001", "organism": "Mouse"},
                {"sample_id_external": "B002", "organism": "Human"},
                {"sample_id_external": "B003", "organism": "Rat"},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["created"] == 3

    # Verify audit entries for each sample
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE entity_type = 'sample' AND action = 'create'")
    )
    count = result.scalar()
    assert count >= 3


@pytest.mark.asyncio
async def test_update_sample(client, admin_token, experiment_id, session):
    resp = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "U001", "organism": "Human"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = resp.json()["id"]

    response = await client.patch(
        f"/api/samples/{sample_id}",
        json={"organism": "Mouse"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["organism"] == "Mouse"

    # Verify audit
    result = await session.execute(text("SELECT * FROM audit_log WHERE entity_type = 'sample' AND action = 'update'"))
    row = result.fetchone()
    assert row is not None
    assert row.previous_value_json is not None


@pytest.mark.asyncio
async def test_create_sample_with_qc_metrics(client, admin_token, experiment_id, session):
    response = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={
            "sample_id_external": "PBMC_donor1",
            "organism": "Homo sapiens",
            "tissue_type": "PBMC",
            "cell_count": 5000,
            "viability_pct": 95.0,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cell_count"] == 5000
    assert data["viability_pct"] == 95.0


@pytest.mark.asyncio
async def test_qc_status_update(client, admin_token, experiment_id, session):
    resp = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "QC001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = resp.json()["id"]

    response = await client.patch(
        f"/api/samples/{sample_id}/qc",
        json={"qc_status": "pass", "qc_notes": "Looks good"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["qc_status"] == "pass"

    # Verify audit
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'sample' AND action = 'qc_update'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_sample_status_transition(client, admin_token, experiment_id):
    resp = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "ST001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = resp.json()["id"]

    # Valid: registered -> library_prepped
    response = await client.patch(
        f"/api/samples/{sample_id}/status",
        json={"status": "library_prepped"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    # Invalid: library_prepped -> analysis_complete
    response = await client.patch(
        f"/api/samples/{sample_id}/status",
        json={"status": "analysis_complete"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_template_required_fields_enforcement(client, admin_token, template_experiment_id):
    # Missing required fields
    response = await client.post(
        f"/api/experiments/{template_experiment_id}/samples",
        json={"sample_id_external": "TF001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "Required field" in response.json()["detail"]

    # With required fields
    response = await client.post(
        f"/api/experiments/{template_experiment_id}/samples",
        json={"sample_id_external": "TF002", "organism": "Human", "tissue_type": "Liver"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bulk_create_validates_all(client, admin_token, template_experiment_id):
    # All-or-nothing: one bad sample should fail the whole batch
    response = await client.post(
        f"/api/experiments/{template_experiment_id}/samples/bulk",
        json={
            "samples": [
                {"sample_id_external": "BV001", "organism": "Human", "tissue_type": "Brain"},
                {"sample_id_external": "BV002"},  # missing required fields
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_filter_samples(client, admin_token, experiment_id):
    await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "F001", "organism": "Human", "qc_status": "pass"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "F002", "organism": "Mouse", "qc_status": "fail"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Filter by qc_status
    response = await client.get(
        f"/api/experiments/{experiment_id}/samples?qc_status=pass",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    samples = response.json()
    assert all(s["qc_status"] == "pass" for s in samples)


@pytest.mark.asyncio
async def test_bulk_update_samples(client, admin_token, experiment_id, session):
    # Create 3 samples
    ids = []
    for ext_id in ["BU001", "BU002", "BU003"]:
        resp = await client.post(
            f"/api/experiments/{experiment_id}/samples",
            json={"sample_id_external": ext_id, "organism": "Human"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        ids.append(resp.json()["id"])

    # Bulk update organism on first two
    response = await client.patch(
        "/api/samples/bulk/update",
        json={
            "sample_ids": ids[:2],
            "update": {"organism": "Mus musculus", "tissue_type": "liver"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert response.json()["errors"] == []

    # Verify updates applied
    for sid in ids[:2]:
        s = await client.get(f"/api/samples/{sid}", headers={"Authorization": f"Bearer {admin_token}"})
        assert s.json()["organism"] == "Mus musculus"
        assert s.json()["tissue_type"] == "liver"

    # Third sample unchanged
    s3 = await client.get(f"/api/samples/{ids[2]}", headers={"Authorization": f"Bearer {admin_token}"})
    assert s3.json()["organism"] == "Human"
    assert s3.json()["tissue_type"] is None


@pytest.mark.asyncio
async def test_bulk_update_nonexistent_sample(client, admin_token, experiment_id):
    resp = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "BU_EXIST", "organism": "Human"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    real_id = resp.json()["id"]

    response = await client.patch(
        "/api/samples/bulk/update",
        json={
            "sample_ids": [real_id, 99999],
            "update": {"organism": "Mouse"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 1
    assert len(response.json()["errors"]) == 1
