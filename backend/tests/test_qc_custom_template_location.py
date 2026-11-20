"""The custom QC template reads ``qc_metrics.json`` from the run's one output location.

A custom run's wrapper uploads ``/outputs`` to ``RunOutputLocationService.authorized_uri``; its QC
must read from that same directory, for project-scoped runs as well as experiment-scoped ones.
Database-backed, with a real filesystem storage adapter (NFS) so the read is a real read.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.adapters.storage.nfs import NfsStorageProvider
from app.models.component import PlatformConfig
from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.services.qc.templates import custom
from app.services.run_output_location_service import RunOutputLocationService


async def _custom_run(session, admin_user, *, scope: str) -> PipelineRun:
    org_id, uid = admin_user.organization_id, admin_user.id
    env = Environment(name="e", organization_id=org_id, created_by_user_id=uid, environment_type="pipeline")
    session.add(env)
    await session.flush()
    env_version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: env\n",
        created_by_user_id=uid,
    )
    session.add(env_version)
    cp = CustomPipeline(organization_id=org_id, name="cp", pipeline_key=f"cp-qc-{uid}", created_by_user_id=uid)
    session.add(cp)
    await session.flush()
    version = CustomPipelineVersion(
        custom_pipeline_id=cp.id,
        version_number=1,
        code_source_type="inline",
        entrypoint_command="run",
        environment_version_id=env_version.id,
        created_by_user_id=uid,
    )
    session.add(version)
    project = Project(organization_id=org_id, name="P", owner_user_id=uid)
    session.add(project)
    await session.flush()
    experiment = Experiment(organization_id=org_id, name="E", owner_user_id=uid, project_id=project.id)
    session.add(experiment)
    await session.flush()
    run = PipelineRun(
        organization_id=org_id,
        experiment_id=experiment.id if scope == "experiment" else None,
        project_id=project.id if scope == "project" else None,
        submitted_by_user_id=uid,
        pipeline_name="cp",
        status="completed",
        custom_pipeline_version_id=version.id,
        parameters_json={"outdir": "file://res/elsewhere"},
    )
    session.add(run)
    await session.flush()
    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["experiment", "project"])
async def test_custom_qc_reads_metrics_from_the_runs_output_location(session, admin_user, tmp_path, scope):
    session.add(PlatformConfig(key="results_bucket_name", value="res"))
    await session.flush()
    storage = NfsStorageProvider(root=str(tmp_path))
    run = await _custom_run(session, admin_user, scope=scope)
    location = await RunOutputLocationService.authorized_uri(session, run, storage)
    metrics = {"my_metric": 7, "quality_rating": "good"}
    await storage.write_text(f"{location}/qc_metrics.json", json.dumps(metrics))

    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        assert await custom.extract(session, run, results_bucket="res") == metrics
