import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy import text

from app.models.controlled_vocabulary import ControlledVocabulary


@pytest_asyncio.fixture
async def seeded_design_types(session):
    """Seed design_type vocabulary values for tests."""
    for val in ("case-control", "cohort", "time-series", "dose-response", "other"):
        session.add(
            ControlledVocabulary(
                field_name="design_type",
                allowed_value=val,
                display_order=0,
                is_default=False,
            )
        )
    await session.flush()
    await session.commit()

    from app.services.vocabulary_validator import invalidate_cache

    invalidate_cache("design_type")


@pytest_asyncio.fixture
async def experiment(client, admin_token, seeded_design_types):
    response = await client.post(
        "/api/experiments",
        json={"name": "Provenance Test Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_create_experiment_with_design_type(client, admin_token, seeded_design_types, session):
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Design Type Experiment",
            "design_type": "case-control",
            "protocol_version": "v2.1",
            "variables_json": {
                "independent": ["drug_concentration"],
                "dependent": ["gene_expression"],
            },
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["design_type"] == "case-control"
    assert data["protocol_version"] == "v2.1"
    assert data["variables_json"]["independent"] == ["drug_concentration"]


@pytest.mark.asyncio
async def test_create_experiment_invalid_design_type(client, admin_token, seeded_design_types, session):
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Bad Design Type",
            "design_type": "not-a-real-type",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_experiment_without_new_fields(client, admin_token, seeded_design_types, session):
    response = await client.post(
        "/api/experiments",
        json={"name": "Plain Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["design_type"] is None
    assert data["protocol_version"] is None
    assert data["variables_json"] is None


@pytest.mark.asyncio
async def test_sample_parent_relationship(client, admin_token, experiment, session):
    exp_id = experiment["id"]

    # Create parent sample
    resp1 = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"organism": "Homo sapiens", "tissue_type": "blood"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 200
    parent = resp1.json()

    # Create child sample referencing parent
    resp2 = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "organism": "Homo sapiens",
            "tissue_type": "plasma",
            "parent_sample_id": parent["id"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 200
    child = resp2.json()
    assert child["parent_sample_id"] == parent["id"]


@pytest.mark.asyncio
async def test_sample_collection_fields(client, admin_token, experiment, session):
    exp_id = experiment["id"]
    ts = "2026-03-20T14:30:00+00:00"

    response = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "organism": "Mus musculus",
            "collection_timestamp": ts,
            "collection_method": "needle biopsy",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["collection_method"] == "needle biopsy"
    assert data["collection_timestamp"] is not None


@pytest.mark.asyncio
async def test_file_sha256_checksum(client, admin_token, session):
    # Create file directly in DB to test the new columns
    row = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org_id = row.scalar_one()

    from app.models.file import File

    f = File(
        organization_id=org_id,
        gcs_uri="gs://test-bucket/checksum-test.fastq.gz",
        filename="checksum-test.fastq.gz",
        file_type="fastq",
        sha256_checksum="a" * 64,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    result = await session.execute(
        text("SELECT sha256_checksum FROM files WHERE id = :id"),
        {"id": f.id},
    )
    assert result.scalar_one() == "a" * 64


@pytest.mark.asyncio
async def test_file_artifact_type(client, admin_token, session):
    row = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org_id = row.scalar_one()

    from app.models.file import File

    f = File(
        organization_id=org_id,
        gcs_uri="gs://test-bucket/artifact-test.csv",
        filename="artifact-test.csv",
        file_type="csv",
        artifact_type="feature_matrix",
    )
    session.add(f)
    await session.flush()
    await session.commit()

    result = await session.execute(
        text("SELECT artifact_type FROM files WHERE id = :id"),
        {"id": f.id},
    )
    assert result.scalar_one() == "feature_matrix"


@pytest.mark.asyncio
async def test_pipeline_run_review_fields(client, admin_token, admin_user, session):
    row = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org_id = row.scalar_one()

    from app.models.pipeline_run import PipelineRun

    now = datetime.now(timezone.utc)
    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/rnaseq",
        status="complete",
        reviewed_by_user_id=admin_user.id,
        reviewed_at=now,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    result = await session.execute(
        text("SELECT reviewed_by_user_id, reviewed_at FROM pipeline_runs WHERE id = :id"),
        {"id": run.id},
    )
    row = result.one()
    assert row[0] == admin_user.id
    assert row[1] is not None


@pytest.mark.asyncio
async def test_pipeline_run_retry_count_default(client, admin_token, session):
    row = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org_id = row.scalar_one()

    from app.models.pipeline_run import PipelineRun

    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/rnaseq",
        status="pending",
    )
    session.add(run)
    await session.flush()
    await session.commit()

    result = await session.execute(
        text("SELECT retry_count FROM pipeline_runs WHERE id = :id"),
        {"id": run.id},
    )
    assert result.scalar_one() == 0
