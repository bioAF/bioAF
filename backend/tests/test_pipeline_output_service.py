"""Tests for PipelineOutputService - registers pipeline outputs as File records."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from sqlalchemy import select, text

from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.project import Project
from app.models.sample import Sample
from app.services.pipeline_output_service import PipelineOutputService


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    """Create a test experiment."""
    exp = Experiment(
        name="Test Experiment",
        organization_id=admin_user.organization_id,
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()
    return exp


@pytest_asyncio.fixture
async def samples(session, admin_user, experiment):
    """Create two test samples linked to the experiment."""
    s1 = Sample(
        external_id="Sample-001",
        experiment_id=experiment.id,
    )
    s2 = Sample(
        external_id="Sample-002",
        experiment_id=experiment.id,
    )
    session.add_all([s1, s2])
    await session.flush()
    return [s1, s2]


@pytest_asyncio.fixture
async def pipeline_run(session, admin_user, experiment, samples):
    """Create a pipeline run linked to the experiment and samples."""
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        status="completed",
        k8s_job_name="nf-scrnaseq-abc123",
        compute_job_ref="nf-scrnaseq-abc123",
    )
    session.add(run)
    await session.flush()

    for s in samples:
        session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=s.id))
    await session.flush()
    await session.commit()
    return run


def _make_collected(run_id: int, experiment_id: int) -> list[dict]:
    """Build a sample collect_outputs() result."""
    base = f"gs://bioaf-results-testorg/experiments/{experiment_id}/pipeline-runs/{run_id}"
    return [
        {
            "filename": "filtered.h5ad",
            "gcs_uri": f"{base}/filtered.h5ad",
            "size_bytes": 50_000_000,
            "md5_hash": "abc123",
            "experiment_id": experiment_id,
            "pipeline_run_id": run_id,
        },
        {
            "filename": "aligned.bam",
            "gcs_uri": f"{base}/aligned.bam",
            "size_bytes": 200_000_000,
            "md5_hash": "def456",
            "experiment_id": experiment_id,
            "pipeline_run_id": run_id,
        },
        {
            "filename": "qc_plot.png",
            "gcs_uri": f"{base}/qc_plot.png",
            "size_bytes": 50_000,
            "md5_hash": "ghi789",
            "experiment_id": experiment_id,
            "pipeline_run_id": run_id,
        },
    ]


@pytest.mark.asyncio
async def test_register_outputs_creates_file_records(session, pipeline_run, experiment):
    """File records are created with correct source_type, run ID, and types."""
    collected = _make_collected(pipeline_run.id, experiment.id)

    files = await PipelineOutputService.register_outputs(session, pipeline_run, collected)
    await session.commit()

    assert len(files) == 3

    h5ad = next(f for f in files if f.filename == "filtered.h5ad")
    assert h5ad.source_type == "pipeline_output"
    assert h5ad.source_pipeline_run_id == pipeline_run.id
    assert h5ad.experiment_id == experiment.id
    assert h5ad.file_type == "h5ad"
    assert h5ad.artifact_type == "anndata"

    bam = next(f for f in files if f.filename == "aligned.bam")
    assert bam.file_type == "bam"
    assert bam.artifact_type == "alignment"

    png = next(f for f in files if f.filename == "qc_plot.png")
    assert png.file_type == "image"
    assert png.artifact_type == "image"


@pytest.mark.asyncio
async def test_register_outputs_links_files_to_samples(session, pipeline_run, experiment, samples):
    """Each output file is linked to all samples from the pipeline run."""
    collected = _make_collected(pipeline_run.id, experiment.id)

    files = await PipelineOutputService.register_outputs(session, pipeline_run, collected)
    await session.commit()

    sample_ids = {s.id for s in samples}

    for f in files:
        rows = await session.execute(
            text("SELECT sample_id FROM sample_files WHERE file_id = :fid"),
            {"fid": f.id},
        )
        linked_ids = {row[0] for row in rows.all()}
        assert linked_ids == sample_ids, f"File {f.filename} not linked to all samples"


@pytest.mark.asyncio
async def test_register_outputs_skips_duplicates(session, pipeline_run, experiment, admin_user):
    """Files with an already-existing gcs_uri are not duplicated."""
    collected = _make_collected(pipeline_run.id, experiment.id)

    # Pre-create one file with the same gcs_uri
    existing = File(
        organization_id=admin_user.organization_id,
        gcs_uri=collected[0]["gcs_uri"],
        filename="filtered.h5ad",
        size_bytes=50_000_000,
        file_type="h5ad",
        source_type="upload",
    )
    session.add(existing)
    await session.flush()
    await session.commit()

    files = await PipelineOutputService.register_outputs(session, pipeline_run, collected)
    await session.commit()

    # Only 2 new files created (the duplicate was skipped)
    assert len(files) == 2

    # Verify only one file with that gcs_uri exists
    result = await session.execute(select(File).where(File.gcs_uri == collected[0]["gcs_uri"]))
    all_matches = result.scalars().all()
    assert len(all_matches) == 1


@pytest.mark.asyncio
async def test_register_outputs_handles_empty_list(session, pipeline_run):
    """Empty collected list returns empty result without errors."""
    files = await PipelineOutputService.register_outputs(session, pipeline_run, [])
    assert files == []


@pytest_asyncio.fixture
async def project(session, admin_user):
    p = Project(
        organization_id=admin_user.organization_id,
        name="Output Project",
        owner_user_id=admin_user.id,
    )
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest_asyncio.fixture
async def project_scoped_run(session, admin_user, project):
    """Create a pipeline run scoped to a project only (no experiment, no samples)."""
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=None,
        project_id=project.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="custom/project-pipeline",
        pipeline_version="1",
        status="completed",
        k8s_job_name="custom-projscope-xyz",
        compute_job_ref="custom-projscope-xyz",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


def _make_project_collected(run_id: int, project_id: int) -> list[dict]:
    base = f"gs://bioaf-results-testorg/projects/{project_id}/pipeline-runs/{run_id}"
    return [
        {
            "filename": "summary.tsv",
            "gcs_uri": f"{base}/summary.tsv",
            "size_bytes": 1234,
            "md5_hash": "aaa111",
        },
        {
            "filename": "result.h5ad",
            "gcs_uri": f"{base}/result.h5ad",
            "size_bytes": 5_000_000,
            "md5_hash": "bbb222",
        },
    ]


@pytest.mark.asyncio
async def test_register_outputs_project_scoped(session, project_scoped_run, project):
    """Project-scoped runs register files with project_id and no sample/experiment links."""
    collected = _make_project_collected(project_scoped_run.id, project.id)

    files = await PipelineOutputService.register_outputs(session, project_scoped_run, collected)
    await session.commit()

    assert len(files) == 2
    for f in files:
        assert f.project_id == project.id
        assert f.experiment_id is None
        assert f.source_type == "pipeline_output"
        assert f.source_pipeline_run_id == project_scoped_run.id

        # No sample links should exist
        rows = await session.execute(
            text("SELECT sample_id FROM sample_files WHERE file_id = :fid"),
            {"fid": f.id},
        )
        assert rows.all() == []


@pytest.mark.asyncio
async def test_register_outputs_experiment_scoped_does_not_set_project_id(session, pipeline_run, experiment):
    """Experiment-scoped runs should not set File.project_id (project is derived via experiment)."""
    collected = _make_collected(pipeline_run.id, experiment.id)

    files = await PipelineOutputService.register_outputs(session, pipeline_run, collected)
    await session.commit()

    assert len(files) == 3
    for f in files:
        assert f.experiment_id == experiment.id
        assert f.project_id is None


@pytest.mark.asyncio
async def test_register_nextflow_metadata_project_scoped(session, project_scoped_run, project):
    """Nextflow metadata for project-scoped runs is registered with project_id, not experiment_id."""
    with _mock_storage_adapter_metadata(exists=True, size=8000):
        files = await PipelineOutputService.register_nextflow_metadata(session, project_scoped_run)
        await session.commit()

    assert len(files) == 2
    for f in files:
        assert f.project_id == project.id
        assert f.experiment_id is None
        assert f.source_pipeline_run_id == project_scoped_run.id


@pytest.mark.asyncio
async def test_register_outputs_no_samples(session, admin_user, experiment):
    """Works when the pipeline run has no linked samples."""
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
    )
    session.add(run)
    await session.flush()
    await session.commit()

    collected = _make_collected(run.id, experiment.id)
    files = await PipelineOutputService.register_outputs(session, run, collected)
    await session.commit()

    assert len(files) == 3
    # No sample links, but files are still created
    for f in files:
        rows = await session.execute(
            text("SELECT sample_id FROM sample_files WHERE file_id = :fid"),
            {"fid": f.id},
        )
        assert rows.all() == []


# --- Nextflow metadata registration ---


def _mock_storage_adapter_metadata(*, exists: bool = True, size: int = 1000):
    """Patch the storage adapter so get_object_metadata reports object presence.

    The metadata check routes through the BAL storage adapter (Phase 3): an
    existing object returns ObjectMetadata(size_bytes=...); a missing one
    raises StorageObjectNotFound. The report/trace URIs are resolved via the
    adapter's RAW store (Phase 5), so resolve_uri is stubbed to the raw bucket."""
    from app.adapters.models import ObjectMetadata, StorageObjectNotFound

    adapter = AsyncMock()
    adapter.resolve_uri.side_effect = lambda store, key: f"gs://bioaf-raw-testorg/{key}"
    if exists:
        adapter.get_object_metadata.side_effect = lambda uri: ObjectMetadata(uri=uri, size_bytes=size)
    else:
        adapter.get_object_metadata.side_effect = StorageObjectNotFound("missing")
    return patch("app.adapters.registry.get_storage_adapter", return_value=adapter)


@pytest.mark.asyncio
async def test_register_nextflow_metadata_creates_records(session, pipeline_run, experiment):
    """Report and trace files are registered when objects exist in storage."""
    with _mock_storage_adapter_metadata(exists=True, size=5000):
        files = await PipelineOutputService.register_nextflow_metadata(session, pipeline_run)
        await session.commit()

    assert len(files) == 2
    filenames = {f.filename for f in files}
    assert filenames == {"report.html", "trace.tsv"}

    report = next(f for f in files if f.filename == "report.html")
    assert report.artifact_type == "pipeline_report"
    assert report.source_type == "pipeline_output"
    assert report.source_pipeline_run_id == pipeline_run.id

    trace = next(f for f in files if f.filename == "trace.tsv")
    assert trace.artifact_type == "pipeline_trace"


@pytest.mark.asyncio
async def test_register_nextflow_metadata_resolves_raw_store(session, pipeline_run):
    """Report/trace URIs are resolved via the storage adapter's RAW store, not a
    compute-adapter bucket name (Phase 5: get_raw_bucket_name retired)."""
    from app.adapters.models import StorageStore

    with _mock_storage_adapter_metadata(exists=True, size=100) as p:
        adapter = p.return_value
        await PipelineOutputService.register_nextflow_metadata(session, pipeline_run)
        await session.commit()

    stores = {call.args[0] for call in adapter.resolve_uri.call_args_list}
    keys = {call.args[1] for call in adapter.resolve_uri.call_args_list}
    assert stores == {StorageStore.RAW}
    assert keys == {
        f"nextflow-reports/{pipeline_run.k8s_job_name}/report.html",
        f"nextflow-traces/{pipeline_run.k8s_job_name}/trace.tsv",
    }


@pytest.mark.asyncio
async def test_register_nextflow_metadata_skips_when_raw_store_unconfigured(session, pipeline_run):
    """No RAW store configured (resolve_uri raises ValueError) -> no metadata."""
    from app.adapters.models import ObjectMetadata  # noqa: F401

    adapter = AsyncMock()
    adapter.resolve_uri.side_effect = ValueError("No bucket configured for store 'raw'")
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        files = await PipelineOutputService.register_nextflow_metadata(session, pipeline_run)
    assert files == []


@pytest.mark.asyncio
async def test_register_nextflow_metadata_skips_missing_blobs(session, pipeline_run):
    """No records created when objects do not exist."""
    with _mock_storage_adapter_metadata(exists=False):
        files = await PipelineOutputService.register_nextflow_metadata(session, pipeline_run)

    assert files == []


@pytest.mark.asyncio
async def test_register_nextflow_metadata_skips_without_k8s_job(session, admin_user, experiment):
    """No records created when run has no k8s_job_name."""
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
        k8s_job_name=None,
        compute_job_ref=None,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    files = await PipelineOutputService.register_nextflow_metadata(session, run)
    assert files == []
