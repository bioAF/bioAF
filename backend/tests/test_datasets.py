import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def experiment_with_files(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.file import File

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Dataset Test Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/sample.fastq.gz",
        filename="sample.fastq.gz",
        size_bytes=500_000_000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
        experiment_id=exp.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return exp, f


@pytest.mark.asyncio
async def test_search_datasets_returns_200(client, admin_token, experiment_with_files):
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_datasets_aggregates_file_count(client, admin_token, experiment_with_files):
    exp, f = experiment_with_files
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    match = next((d for d in data["experiments"] if d["experiment_id"] == exp.id), None)
    assert match is not None
    assert match["file_count"] == 1
    assert match["total_size_bytes"] == 500_000_000


@pytest.mark.asyncio
async def test_search_datasets_empty_org(client, admin_token):
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "experiments" in data
    assert "total" in data
