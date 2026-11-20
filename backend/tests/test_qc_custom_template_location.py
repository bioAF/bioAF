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


async def _custom_run(session, admin_user, *, scope: str, qc_template: str | None = None) -> PipelineRun:
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
        qc_template=qc_template,
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
        assert await custom.extract(session, run) == metrics


# --- Through the dashboard: what the monitor runs when a custom run completes ---


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["experiment", "project"])
async def test_the_dashboard_reads_custom_metrics_with_no_bucket_configured(session, admin_user, tmp_path, scope):
    """No results bucket setting: the location is the adapter's results store (here NFS), and the
    dashboard must still reach the custom extractor rather than return empty metrics."""
    from app.services.qc_dashboard_service import QCDashboardService

    storage = NfsStorageProvider(root=str(tmp_path))
    run = await _custom_run(session, admin_user, scope=scope)
    location = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert location.startswith("file://results/")
    await storage.write_text(f"{location}/qc_metrics.json", json.dumps({"my_metric": 7}))

    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        dashboard = await QCDashboardService.generate_qc_dashboard(session, admin_user.organization_id, run.id)
    assert dashboard.status == "ready", dashboard.summary_text
    assert dashboard.metrics_json["my_metric"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["generic", "scrnaseq", "bulk_rnaseq", "atacseq", "chipseq"])
async def test_a_project_scoped_custom_run_with_a_built_in_template_reads_its_own_directory(
    session, admin_user, tmp_path, template
):
    """A custom pipeline version may pick a built-in QC template. Its cached metrics sit in the
    run's own directory, which for a project-scoped run is under the project, not `experiments/None`."""
    from app.services.qc_dashboard_service import QCDashboardService

    session.add(PlatformConfig(key="results_bucket_name", value="res"))
    await session.flush()
    storage = NfsStorageProvider(root=str(tmp_path))
    run = await _custom_run(session, admin_user, scope="project", qc_template=template)
    location = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert "/projects/" in location
    await storage.write_text(f"{location}/qc_metrics.json", json.dumps({"my_metric": 7}))

    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        dashboard = await QCDashboardService.generate_qc_dashboard(session, admin_user.organization_id, run.id)
    assert dashboard.status == "ready", dashboard.summary_text
    assert dashboard.metrics_json.get("my_metric") == 7


@pytest.mark.asyncio
async def test_plots_are_collected_from_a_project_scoped_runs_own_directory(session, admin_user, tmp_path):
    from app.services.qc.templates import bulk_rnaseq
    from app.services.qc_dashboard_service import QCDashboardService

    session.add(PlatformConfig(key="results_bucket_name", value="res"))
    await session.flush()
    storage = NfsStorageProvider(root=str(tmp_path))
    run = await _custom_run(session, admin_user, scope="project", qc_template="bulk_rnaseq")
    location = await RunOutputLocationService.authorized_uri(session, run, storage)
    png, title, plot_type = bulk_rnaseq.MULTIQC_PLOTS[0]
    await storage.write_bytes(f"{location}/multiqc/star_salmon/{png}", b"\x89PNG")
    # A same-named plot in another run's directory is not this run's.
    await storage.write_bytes(f"file://res/projects/0/pipeline-runs/{run.id}/multiqc/{png}", b"\x89PNG")

    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        plots = await QCDashboardService._collect_plots(
            session, admin_user.organization_id, run, template_name="bulk_rnaseq"
        )
    assert [(p["plot_type"], p["title"]) for p in plots] == [(plot_type, title)]
