"""Tests for GEO export API endpoint."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
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
        email="bench@test.com",
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
async def export_experiment(client, admin_token, session, admin_user):
    from app.models.batch import Batch
    from app.models.pipeline_run import PipelineRun
    from app.models.sample import Sample

    org_id = admin_user.organization_id

    resp = await client.post(
        "/api/experiments",
        json={"name": "GEO API Test", "description": "Test for GEO API."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    batch = Batch(
        experiment_id=exp_id,
        name="Batch1",
        instrument_model="Illumina NovaSeq 6000",
    )
    session.add(batch)
    await session.flush()

    s1 = Sample(
        experiment_id=exp_id,
        batch_id=batch.id,
        sample_id_external="S001",
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
