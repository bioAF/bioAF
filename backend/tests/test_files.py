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
async def test_file_source_metadata_upload(client, admin_token, sample_file):
    """Uploaded files should expose source_type='upload' in API response."""
    resp = await client.get(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "upload"
    assert data["source_pipeline_run_id"] is None


@pytest.mark.asyncio
async def test_file_source_metadata_qc_dashboard(client, admin_token, session, admin_user):
    """Files created by QC dashboard should have source_type='qc_dashboard' and a pipeline run link."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.pipeline_run import PipelineRun

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="QC Source Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="multiqc",
        status="complete",
    )
    session.add(run)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/qc-plot.png",
        filename="qc-plot.png",
        size_bytes=4096,
        file_type="png",
        source_type="qc_dashboard",
        source_pipeline_run_id=run.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    resp = await client.get(
        f"/api/files/{f.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "qc_dashboard"
    assert data["source_pipeline_run_id"] == run.id


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
async def test_delete_file_with_plot_archive(client, admin_token, session, admin_user):
    """Deleting a file referenced by plot_archive should succeed by cascading."""
    from app.models.file import File
    from app.models.plot_archive_entry import PlotArchiveEntry

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/qc-plot.png",
        filename="qc-plot.png",
        size_bytes=2048,
        file_type="png",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    entry = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="QC Plot",
    )
    session.add(entry)
    await session.flush()
    await session.commit()

    resp = await client.delete(
        f"/api/files/{f.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    from sqlalchemy import text

    row = (
        await session.execute(text("SELECT count(*) FROM plot_archive WHERE file_id = :fid").bindparams(fid=f.id))
    ).scalar()
    assert row == 0


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
async def test_link_already_linked_file_does_not_move_gcs(client, admin_token, session, admin_user):
    """Re-linking a file to the same experiment must not attempt a GCS move."""
    from unittest.mock import AsyncMock, patch

    from app.models.experiment import Experiment
    from app.models.file import File

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Already Linked Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    # File already linked to the experiment (e.g. a pipeline output)
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri=f"gs://bioaf-results/experiments/{exp.id}/pipeline-runs/1/plot.png",
        filename="plot.png",
        size_bytes=1000,
        file_type="png",
        uploader_user_id=admin_user.id,
        experiment_id=exp.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    mock_move = AsyncMock()
    with patch("app.services.file_organization.GcsStorageService.move_file", mock_move):
        resp = await client.post(
            f"/api/files/{f.id}/link",
            json={"experiment_id": exp.id, "sample_id": None},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    mock_move.assert_not_called()


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


# --- Sample association tests ---


@pytest.mark.asyncio
async def test_link_file_to_sample_returns_sample_ids(client, admin_token, session, admin_user):
    """After linking a file to a sample, GET /api/files/{id} should include that sample_id."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.sample import Sample

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Sample Link Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    sample = Sample(
        experiment_id=exp.id,
        sample_id_unique="SMP-001",
    )
    session.add(sample)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/reads.fastq.gz",
        filename="reads.fastq.gz",
        size_bytes=1000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
        experiment_id=exp.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    resp = await client.post(
        f"/api/files/{f.id}/link",
        json={"sample_id": sample.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    resp2 = await client.get(
        f"/api/files/{f.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = resp2.json()
    assert sample.id in data["sample_ids"]


@pytest.mark.asyncio
async def test_link_file_to_sample_idempotent(client, admin_token, session, admin_user):
    """Linking the same file to the same sample twice should not create duplicates."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.sample import Sample
    from sqlalchemy import text

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Dedup Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    sample = Sample(
        experiment_id=exp.id,
        sample_id_unique="SMP-002",
    )
    session.add(sample)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/reads2.fastq.gz",
        filename="reads2.fastq.gz",
        size_bytes=1000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    for _ in range(3):
        await client.post(
            f"/api/files/{f.id}/link",
            json={"sample_id": sample.id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM sample_files WHERE file_id = :fid AND sample_id = :sid").bindparams(
                fid=f.id, sid=sample.id
            )
        )
    ).scalar_one()
    assert count == 1, f"Expected 1 sample_files row, got {count}"


@pytest.mark.asyncio
async def test_list_files_includes_sample_ids(client, admin_token, session, admin_user):
    """GET /api/files should include sample_ids for each file."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.sample import Sample
    from sqlalchemy import text

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="List Sample Ids Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    sample = Sample(
        experiment_id=exp.id,
        sample_id_unique="SMP-003",
    )
    session.add(sample)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/reads3.fastq.gz",
        filename="reads3.fastq.gz",
        size_bytes=1000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    await session.execute(
        text("INSERT INTO sample_files (sample_id, file_id) VALUES (:sid, :fid)").bindparams(sid=sample.id, fid=f.id)
    )
    await session.commit()

    resp = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    file_data = next((x for x in resp.json()["files"] if x["id"] == f.id), None)
    assert file_data is not None
    assert sample.id in file_data["sample_ids"]


@pytest.mark.asyncio
async def test_list_files_filter_by_experiment_id(client, admin_token, sample_file, sample_experiment, session):
    """GET /api/files?experiment_id=N should return only files for that experiment."""
    # Link sample_file to the experiment
    sample_file.experiment_id = sample_experiment.id
    await session.commit()

    # Create a second file NOT linked to the experiment
    from app.models.file import File

    unlinked = File(
        organization_id=sample_file.organization_id,
        gcs_uri="gs://test-bucket/other.csv",
        filename="other.csv",
        size_bytes=500,
        file_type="csv",
        uploader_user_id=sample_file.uploader_user_id,
    )
    session.add(unlinked)
    await session.flush()
    await session.commit()

    resp = await client.get(
        f"/api/files?experiment_id={sample_experiment.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["files"][0]["id"] == sample_file.id
    assert data["files"][0]["experiment_id"] == sample_experiment.id


@pytest.mark.asyncio
async def test_get_experiment_files(client, admin_token, sample_file, sample_experiment, session):
    """GET /api/experiments/{id}/files should return files for that experiment."""
    sample_file.experiment_id = sample_experiment.id
    await session.commit()

    resp = await client.get(
        f"/api/experiments/{sample_experiment.id}/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["files"][0]["id"] == sample_file.id


@pytest.mark.asyncio
async def test_get_experiment_files_empty(client, admin_token, sample_experiment):
    """GET /api/experiments/{id}/files returns empty list when no files linked."""
    resp = await client.get(
        f"/api/experiments/{sample_experiment.id}/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["files"] == []


@pytest.mark.asyncio
async def test_get_experiment_files_not_found(client, admin_token):
    """GET /api/experiments/99999/files should return 404."""
    resp = await client.get(
        "/api/experiments/99999/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_link_file_to_project(client, admin_token, session, admin_user, sample_file):
    """Linking a file to a project should set project_id on the file record."""
    from app.models.project import Project
    from sqlalchemy import text

    proj = Project(
        organization_id=admin_user.organization_id,
        name="Link Test Project",
        owner_user_id=admin_user.id,
    )
    session.add(proj)
    await session.flush()
    await session.commit()

    resp = await client.post(
        f"/api/files/{sample_file.id}/link",
        json={"project_id": proj.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    row = (
        await session.execute(text("SELECT project_id FROM files WHERE id = :fid").bindparams(fid=sample_file.id))
    ).fetchone()
    assert row[0] == proj.id


@pytest.mark.asyncio
async def test_file_response_includes_project_id(client, admin_token, sample_file):
    """FileResponse must include a project_id field (null when not linked)."""
    resp = await client.get(
        f"/api/files/{sample_file.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "project_id" in data
    assert data["project_id"] is None


# --- Upload Service Unit Tests ---


@pytest.mark.asyncio
async def test_link_file_to_experiment_inherits_project_id(client, admin_token, session, admin_user):
    """Linking a file to an experiment should set project_id from the experiment's project."""
    from unittest.mock import AsyncMock, patch

    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.project import Project

    project = Project(
        organization_id=admin_user.organization_id,
        name="Inherit Project",
        status="active",
        owner_user_id=admin_user.id,
    )
    session.add(project)
    await session.flush()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="Inherit Exp",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/inherit.fastq.gz",
        filename="inherit.fastq.gz",
        size_bytes=1000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    with patch(
        "app.services.file_organization.GcsStorageService.move_file",
        new_callable=AsyncMock,
        return_value=f"gs://test-bucket/experiments/{exp.id}/inherit.fastq.gz",
    ):
        resp = await client.post(
            f"/api/files/{f.id}/link",
            json={"experiment_id": exp.id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200

    resp2 = await client.get(
        f"/api/files/{f.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = resp2.json()
    assert data["project_id"] == project.id


@pytest.mark.asyncio
async def test_list_files_filter_by_project_id(client, admin_token, session, admin_user, sample_file):
    """GET /api/files?project_id=N should return only files linked to that project."""
    from app.models.project import Project

    proj = Project(
        organization_id=admin_user.organization_id,
        name="Filter Project",
        owner_user_id=admin_user.id,
        created_by_user_id=admin_user.id,
    )
    session.add(proj)
    await session.flush()

    sample_file.project_id = proj.id
    await session.commit()

    # Create a second file NOT linked to the project
    from app.models.file import File

    other = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/other.csv",
        filename="other.csv",
        size_bytes=200,
        file_type="csv",
        uploader_user_id=admin_user.id,
    )
    session.add(other)
    await session.commit()

    resp = await client.get(
        f"/api/files?project_id={proj.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["files"][0]["id"] == sample_file.id
    assert data["files"][0]["project_id"] == proj.id


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
