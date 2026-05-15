import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def experiment_with_run(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="QC Test Experiment",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
        work_dir="/data/working/nextflow/run-qc",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return exp, run


@pytest_asyncio.fixture
async def qc_dashboard(session, admin_user, experiment_with_run):
    from app.models.qc_dashboard import QCDashboard
    from datetime import datetime, timezone

    exp, run = experiment_with_run
    d = QCDashboard(
        organization_id=admin_user.organization_id,
        pipeline_run_id=run.id,
        experiment_id=exp.id,
        metrics_json={
            "cell_count": 5000,
            "median_genes_per_cell": 2000,
            "median_umi_per_cell": 8000,
            "mito_pct_median": 3.5,
            "quality_rating": "good",
        },
        summary_text="Good quality dataset with 5000 cells.",
        plots_json=[],
        status="ready",
        generated_at=datetime.now(timezone.utc),
    )
    session.add(d)
    await session.flush()
    await session.commit()
    return d


@pytest.mark.asyncio
async def test_list_qc_dashboards(client, admin_token, qc_dashboard):
    resp = await client.get(
        "/api/qc-dashboards",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["quality_rating"] == "good"


@pytest.mark.asyncio
async def test_list_qc_dashboards_includes_context(client, admin_token, session, admin_user):
    """The list response must surface enough context for users to tell runs
    apart at a glance: project, experiment, sample external IDs, pipeline."""
    from datetime import datetime, timezone

    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun, PipelineRunSample
    from app.models.project import Project
    from app.models.qc_dashboard import QCDashboard
    from app.models.sample import Sample

    project = Project(
        organization_id=admin_user.organization_id,
        name="Project Alpha",
        code="ALPH",
    )
    session.add(project)
    await session.flush()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Alpha Exp 1",
        owner_user_id=admin_user.id,
        status="processing",
        project_id=project.id,
    )
    session.add(exp)
    await session.flush()

    s1 = Sample(
        experiment_id=exp.id,
        external_id="SAMPLE-001",
    )
    s2 = Sample(
        experiment_id=exp.id,
        external_id="SAMPLE-002",
    )
    session.add_all([s1, s2])
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        project_id=project.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.6.0",
        status="completed",
        work_dir="/data/working/nextflow/run-ctx",
    )
    session.add(run)
    await session.flush()
    session.add_all(
        [
            PipelineRunSample(pipeline_run_id=run.id, sample_id=s1.id),
            PipelineRunSample(pipeline_run_id=run.id, sample_id=s2.id),
        ]
    )

    d = QCDashboard(
        organization_id=admin_user.organization_id,
        pipeline_run_id=run.id,
        experiment_id=exp.id,
        metrics_json={"quality_rating": "good"},
        plots_json=[],
        status="ready",
        generated_at=datetime.now(timezone.utc),
    )
    session.add(d)
    await session.commit()

    resp = await client.get(
        "/api/qc-dashboards",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    row = next(r for r in rows if r["id"] == d.id)
    assert row["pipeline_run_id"] == run.id
    assert row["project_name"] == "Project Alpha"
    assert row["experiment_name"] == "Alpha Exp 1"
    assert row["pipeline_name"] == "nf-core/scrnaseq"
    assert sorted(row["sample_external_ids"]) == ["SAMPLE-001", "SAMPLE-002"]


@pytest.mark.asyncio
async def test_get_qc_dashboard(client, admin_token, qc_dashboard):
    resp = await client.get(
        f"/api/qc-dashboards/{qc_dashboard.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"]["cell_count"] == 5000
    assert data["metrics"]["quality_rating"] == "good"
    assert "5000 cells" in data["summary_text"]


@pytest.mark.asyncio
async def test_get_qc_dashboard_by_run(client, admin_token, qc_dashboard, experiment_with_run):
    _, run = experiment_with_run
    resp = await client.get(
        f"/api/qc-dashboards/by-run/{run.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["pipeline_run_id"] == run.id


@pytest.mark.asyncio
async def test_get_qc_dashboard_not_found(client, admin_token):
    resp = await client.get(
        "/api/qc-dashboards/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


# --- Service unit tests ---


def test_quality_rating_logic():
    from app.services.qc_dashboard_service import QCDashboardService

    metrics = {
        "cell_count": 10000,
        "median_genes_per_cell": 3000,
        "mito_pct_median": 2.0,
    }
    rating = QCDashboardService._compute_quality_rating(metrics)
    assert rating in ("excellent", "good", "acceptable", "concerning")


def test_quality_rating_high_mito():
    from app.services.qc_dashboard_service import QCDashboardService

    metrics = {
        "cell_count": 5000,
        "median_genes_per_cell": 2000,
        "mito_pct_median": 25.0,
    }
    rating = QCDashboardService._compute_quality_rating(metrics)
    # High mito should not be excellent
    assert rating in ("acceptable", "concerning")
