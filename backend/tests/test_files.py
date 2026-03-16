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
    from unittest.mock import AsyncMock, patch

    with patch(
        "app.services.file_organization.GcsStorageService.move_file",
        new_callable=AsyncMock,
        return_value=f"gs://test-bucket/experiments/{sample_experiment.id}/test-file.fastq.gz",
    ):
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


@pytest.mark.asyncio
async def test_link_file_moves_to_raw_bucket(client, admin_token, session, admin_user):
    """Linking a file should call FileOrganizationService to move it in GCS."""
    from unittest.mock import AsyncMock, patch

    from app.models.experiment import Experiment
    from app.models.file import File
    from sqlalchemy import text

    # Seed raw bucket config
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('raw_bucket_name', 'bioaf-raw-test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Link Move Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-ingest-test/uploads/abc/reads.fastq.gz",
        filename="reads.fastq.gz",
        size_bytes=5000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    with patch(
        "app.services.file_organization.GcsStorageService.move_file",
        new_callable=AsyncMock,
        return_value=f"gs://bioaf-raw-test/experiments/{exp.id}/reads.fastq.gz",
    ):
        resp = await client.post(
            f"/api/files/{f.id}/link",
            json={"experiment_id": exp.id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    row = (await session.execute(text("SELECT gcs_uri FROM files WHERE id = :fid").bindparams(fid=f.id))).fetchone()
    # File should have moved to the raw bucket under the experiment prefix
    assert "bioaf-raw-test" in row[0], f"Expected raw bucket in URI, got {row[0]}"
    assert f"experiments/{exp.id}/" in row[0]


@pytest.mark.asyncio
async def test_link_fastq_transitions_experiment_to_fastq_uploaded(client, admin_token, session, admin_user):
    """Linking a FASTQ file to a registered experiment should advance status."""
    from unittest.mock import AsyncMock, patch

    from app.models.experiment import Experiment
    from app.models.file import File
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('raw_bucket_name', 'bioaf-raw-test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Status Transition Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-ingest-test/uploads/def/sample.fastq.gz",
        filename="sample.fastq.gz",
        size_bytes=5000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    with patch(
        "app.services.file_organization.GcsStorageService.move_file",
        new_callable=AsyncMock,
        return_value=f"gs://bioaf-raw-test/experiments/{exp.id}/sample.fastq.gz",
    ):
        resp = await client.post(
            f"/api/files/{f.id}/link",
            json={"experiment_id": exp.id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    row = (
        await session.execute(text("SELECT status FROM experiments WHERE id = :eid").bindparams(eid=exp.id))
    ).fetchone()
    assert row[0] == "fastq_uploaded", f"Expected fastq_uploaded, got {row[0]}"


@pytest.mark.asyncio
async def test_reconcile_moves_stuck_ingest_files_to_raw(client, admin_token, session, admin_user):
    """Files stuck in ingest bucket with an experiment_id should be moved to raw."""
    from unittest.mock import patch

    from app.models.experiment import Experiment
    from app.models.file import File
    from sqlalchemy import text

    for key, val in [("ingest_bucket_name", "bioaf-ingest-test"), ("raw_bucket_name", "bioaf-raw-test")]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=val)
        )
    await session.commit()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Reconcile Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    # 2 stuck files: in ingest bucket, already linked to experiment
    stuck_files = []
    for i in range(2):
        f = File(
            organization_id=admin_user.organization_id,
            gcs_uri=f"gs://bioaf-ingest-test/uploads/uid{i}/sample_{i}.fastq.gz",
            filename=f"sample_{i}.fastq.gz",
            size_bytes=5000,
            file_type="fastq",
            uploader_user_id=admin_user.id,
            experiment_id=exp.id,
        )
        session.add(f)
        await session.flush()
        stuck_files.append(f)

    # 1 file already in raw bucket (should not be touched)
    ok_file = File(
        organization_id=admin_user.organization_id,
        gcs_uri=f"gs://bioaf-raw-test/experiments/{exp.id}/good.fastq.gz",
        filename="good.fastq.gz",
        size_bytes=5000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
        experiment_id=exp.id,
    )
    session.add(ok_file)
    await session.flush()
    await session.commit()

    move_calls = []

    async def fake_move(source, dest, credentials=None):
        move_calls.append((source, dest))
        return dest

    with patch("app.services.file_organization.GcsStorageService.move_file", side_effect=fake_move):
        resp = await client.post(
            "/api/files/reconcile",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["reconciled"] == 2
    assert data["skipped"] >= 1

    # Verify stuck files now point to raw bucket
    for sf in stuck_files:
        row = (
            await session.execute(text("SELECT gcs_uri FROM files WHERE id = :fid").bindparams(fid=sf.id))
        ).fetchone()
        assert "bioaf-raw-test" in row[0], f"Expected raw bucket, got {row[0]}"
        assert f"experiments/{exp.id}/" in row[0]

    # Verify OK file was not moved
    assert len(move_calls) == 2

    # Verify experiment status advanced
    row = (
        await session.execute(text("SELECT status FROM experiments WHERE id = :eid").bindparams(eid=exp.id))
    ).fetchone()
    assert row[0] == "fastq_uploaded"


@pytest.mark.asyncio
async def test_reconcile_requires_admin(client, viewer_token):
    """Only admins can run reconcile."""
    resp = await client.post(
        "/api/files/reconcile",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


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
