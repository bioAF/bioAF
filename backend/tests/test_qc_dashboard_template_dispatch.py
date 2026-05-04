"""Tests for QCDashboardService dispatching through the template registry.

After generation:
- dashboard.qc_config_json holds the resolved render config snapshot
- quality_rating comes from the template, not a hardcoded scRNA-seq path
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def setup(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry
    from app.models.pipeline_run import PipelineRun

    org_id = admin_user.organization_id

    entry = PipelineCatalogEntry(
        organization_id=org_id,
        pipeline_key="nf-core/scrnaseq",
        name="scRNA-seq",
        source_type="builtin",
        qc_template="scrnaseq",
    )
    session.add(entry)
    await session.flush()

    exp = Experiment(organization_id=org_id, name="DispatchExp", owner_user_id=admin_user.id, status="processing")
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
        work_dir="/tmp/x",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return org_id, run


@pytest.mark.asyncio
async def test_generate_snapshots_qc_config_json_for_scrnaseq(session, setup):
    from app.services.qc_dashboard_service import QCDashboardService

    org_id, run = setup

    extracted_metrics = {
        "cell_count": 5000,
        "median_genes_per_cell": 2500,
        "median_reads_per_cell": 30000,
        "mito_pct_median": 3.0,
        "saturation": 0.85,
    }

    with (
        patch.object(
            QCDashboardService,
            "_extract_metrics",
            new=AsyncMock(return_value=extracted_metrics),
        ),
        patch.object(QCDashboardService, "_collect_plots", new=AsyncMock(return_value=[])),
    ):
        dashboard = await QCDashboardService.generate_qc_dashboard(session, org_id, run.id)

    assert dashboard.status == "ready"
    # qc_config_json snapshot was persisted with the scrnaseq render config
    assert dashboard.qc_config_json is not None
    assert dashboard.qc_config_json["template"] == "scrnaseq"
    section_ids = {s["id"] for s in dashboard.qc_config_json["sections"]}
    assert "hero" in section_ids
    # Quality rating dispatched through scrnaseq template -- excellent given inputs
    assert dashboard.metrics_json["quality_rating"] == "excellent"


@pytest.mark.asyncio
async def test_generate_uses_custom_template_when_run_is_custom_pipeline(session, admin_user):
    """A run linked to a CustomPipelineVersion uses the 'custom' template,
    and the version's qc_config_json override is applied."""
    from app.models.custom_pipeline import CustomPipeline
    from app.models.custom_pipeline_version import CustomPipelineVersion
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun
    from app.services.qc_dashboard_service import QCDashboardService

    org_id = admin_user.organization_id

    env = Environment(organization_id=org_id, name="default", created_by_user_id=admin_user.id)
    session.add(env)
    await session.flush()
    env_v = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        definition_format="dockerfile",
        definition_content="FROM python:3.12",
        created_by_user_id=admin_user.id,
    )
    session.add(env_v)
    await session.flush()

    cp = CustomPipeline(
        organization_id=org_id,
        name="My Custom",
        pipeline_key="my-custom-disp",
        created_by_user_id=admin_user.id,
    )
    session.add(cp)
    await session.flush()
    override = {
        "template": "custom",
        "sections": [{"id": "main", "metrics": ["my_metric"]}],
        "metrics": {"my_metric": {"label": "My Metric", "format": "decimal"}},
    }
    v = CustomPipelineVersion(
        custom_pipeline_id=cp.id,
        version_number=1,
        code_source_type="inline",
        code_content="echo hi",
        entrypoint_command="bash run.sh",
        environment_version_id=env_v.id,
        created_by_user_id=admin_user.id,
        qc_template="custom",
        qc_config_json=override,
    )
    session.add(v)
    await session.flush()

    exp = Experiment(organization_id=org_id, name="CustomExp", owner_user_id=admin_user.id, status="processing")
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="my-custom-disp",
        status="completed",
        work_dir="/tmp/x",
        custom_pipeline_version_id=v.id,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    custom_metrics = {"my_metric": 42.0, "quality_rating": "good"}

    with (
        patch.object(
            QCDashboardService,
            "_extract_metrics",
            new=AsyncMock(return_value=custom_metrics),
        ),
        patch.object(QCDashboardService, "_collect_plots", new=AsyncMock(return_value=[])),
    ):
        dashboard = await QCDashboardService.generate_qc_dashboard(session, org_id, run.id)

    assert dashboard.qc_config_json is not None
    assert dashboard.qc_config_json["template"] == "custom"
    assert dashboard.qc_config_json["sections"][0]["metrics"] == ["my_metric"]
    # custom template honors emitted quality_rating
    assert dashboard.metrics_json["quality_rating"] == "good"
