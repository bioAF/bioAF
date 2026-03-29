"""Tests for the register-outputs CLI module."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.cli.register_outputs import register_outputs_for_run
from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample


@pytest_asyncio.fixture
async def completed_run(session, admin_user):
    """Create a completed pipeline run with a sample."""
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="CLI Test Experiment",
        owner_user_id=admin_user.id,
        status="analysis",
    )
    session.add(exp)
    await session.flush()

    sample = Sample(
        sample_id_external="CLI-Sample-001",
        experiment_id=exp.id,
    )
    session.add(sample)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        status="completed",
    )
    session.add(run)
    await session.flush()

    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))
    await session.flush()
    await session.commit()
    return run


@pytest.mark.asyncio
async def test_register_outputs_for_run_creates_files(session, completed_run):
    """CLI helper creates File records from collected outputs."""
    run = completed_run
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = [
        {
            "filename": "results.h5ad",
            "gcs_uri": f"gs://bucket/experiments/{run.experiment_id}/pipeline-runs/{run.id}/results.h5ad",
            "size_bytes": 10_000_000,
            "md5_hash": "aaa111",
            "experiment_id": run.experiment_id,
            "pipeline_run_id": run.id,
        },
    ]

    with patch("app.cli.register_outputs.get_storage_adapter", return_value=mock_storage):
        count = await register_outputs_for_run(session, run)

    await session.commit()
    assert count == 1

    result = await session.execute(
        select(File).where(
            File.source_pipeline_run_id == run.id,
            File.source_type == "pipeline_output",
        )
    )
    files = list(result.scalars().all())
    assert len(files) == 1
    assert files[0].filename == "results.h5ad"
    assert files[0].artifact_type == "anndata"


@pytest.mark.asyncio
async def test_register_outputs_for_run_no_files(session, completed_run):
    """Returns 0 when no outputs are found in GCS."""
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with patch("app.cli.register_outputs.get_storage_adapter", return_value=mock_storage):
        count = await register_outputs_for_run(session, completed_run)

    assert count == 0
