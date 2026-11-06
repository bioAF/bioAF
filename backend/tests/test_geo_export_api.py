"""Tests for GEO export API endpoint."""

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def export_experiment(client, admin_token, session, admin_user):
    from app.models.sample_batch import SampleBatch
    from app.models.pipeline_run import PipelineRun
    from app.models.sample import Sample

    org_id = admin_user.organization_id

    resp = await client.post(
        "/api/experiments",
        json={"name": "GEO API Test", "description": "Test for GEO API."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    batch = SampleBatch(
        experiment_id=exp_id,
        name="Batch1",
    )
    session.add(batch)
    await session.flush()

    s1 = Sample(
        experiment_id=exp_id,
        sample_batch_id=batch.id,
        external_id="S001",
        organism="Homo sapiens",
        molecule_type="total RNA",
        library_prep_method="10x Chromium 3' v3.1",
        library_layout="paired",
    )
    session.add(s1)
    await session.flush()

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp_id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.0",
        status="completed",
        reference_genome="GRCh38",
        alignment_algorithm="STARsolo",
    )
    session.add(run)
    await session.flush()
    await session.commit()

    return {"experiment_id": exp_id, "run_id": run.id}


@pytest.mark.asyncio
async def test_validate_only_returns_json(client, comp_bio_token, export_experiment):
    response = await client.post(
        f"/api/experiments/{export_experiment['experiment_id']}/export/geo?validate_only=true",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "series_fields" in data
    assert "sample_validations" in data


@pytest.mark.asyncio
async def test_full_export_returns_zip(client, comp_bio_token, export_experiment):
    response = await client.post(
        f"/api/experiments/{export_experiment['experiment_id']}/export/geo",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "geo_export_" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_bench_cannot_export(client, bench_token, export_experiment):
    response = await client.post(
        f"/api/experiments/{export_experiment['experiment_id']}/export/geo",
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_nonexistent_experiment_404(client, comp_bio_token):
    response = await client.post(
        "/api/experiments/99999/export/geo",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_audit_log_created(client, comp_bio_token, export_experiment, session):
    await client.post(
        f"/api/experiments/{export_experiment['experiment_id']}/export/geo?validate_only=true",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'geo_export' AND action = 'validated'")
    )
    assert result.fetchone() is not None
