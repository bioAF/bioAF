import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def sample_experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="File Test Experiment",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def sample_file(session, admin_user):
    from app.models.file import File

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/test-file.fastq.gz",
        filename="test-file.fastq.gz",
        size_bytes=1024000,
        md5_checksum="abc123",
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return f


# --- API Tests ---


@pytest.mark.asyncio
async def test_list_files(client, admin_token, sample_file):
    resp = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(f["filename"] == "test-file.fastq.gz" for f in data["files"])


@pytest.mark.asyncio
async def test_get_file(client, admin_token, sample_file):
    resp = await client.get(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["filename"] == "test-file.fastq.gz"


@pytest.mark.asyncio
async def test_get_file_not_found(client, admin_token):
    resp = await client.get(
        "/api/files/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_file(client, admin_token, sample_file):
    resp = await client.delete(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_delete_file(client, viewer_token, sample_file):
    resp = await client.delete(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_files_includes_experiment_id(client, admin_token, sample_file, sample_experiment, session):
    sample_file.experiment_id = sample_experiment.id
    await session.commit()

    resp = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    match = next(f for f in resp.json()["files"] if f["id"] == sample_file.id)
    assert match["experiment_id"] == sample_experiment.id


@pytest.mark.asyncio
async def test_list_files_null_experiment_id(client, admin_token, sample_file):
    resp = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    match = next(f for f in resp.json()["files"] if f["id"] == sample_file.id)
    assert match["experiment_id"] is None


@pytest.mark.asyncio
async def test_link_file_to_experiment(client, admin_token, sample_file, sample_experiment):
    resp = await client.post(
        f"/api/files/{sample_file.id}/link",
        json={"experiment_id": sample_experiment.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.json()["experiment_id"] == sample_experiment.id


# --- Upload Service Unit Tests ---


def test_parse_illumina_filename():
    from app.services.upload_service import UploadService

    result = UploadService.parse_illumina_filename("SampleName_S1_L001_R1_001.fastq.gz")
    assert result is not None
    assert result["sample_name"] == "SampleName"
    assert result["sample_number"] == 1
    assert result["lane"] == 1
    assert result["read"] == "R1"

    # Non-Illumina filename
    result = UploadService.parse_illumina_filename("random_file.fastq.gz")
    assert result is None
