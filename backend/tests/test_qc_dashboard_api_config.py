"""Tests for the QCDashboardResponse including qc_config.

The dashboard GET endpoints surface the resolved render config so the
frontend can render generically. For dashboards generated before this
change, qc_config_json is NULL on the row -- the API substitutes the
template's default config so the page still renders.
"""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def setup(session, admin_user):
    from datetime import datetime, timezone
    from app.models.experiment import Experiment
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry
    from app.models.pipeline_run import PipelineRun
    from app.models.qc_dashboard import QCDashboard

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

    exp = Experiment(organization_id=org_id, name="ApiCfgExp", owner_user_id=admin_user.id, status="processing")
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

    snapshot = {
        "template": "scrnaseq",
        "sections": [{"id": "hero", "metrics": ["cell_count"]}],
        "metrics": {"cell_count": {"label": "Cells", "format": "integer"}},
        "charts": [],
        "plots": [],
    }
    d_with = QCDashboard(
        organization_id=org_id,
        pipeline_run_id=run.id,
        experiment_id=exp.id,
        metrics_json={"cell_count": 1234, "quality_rating": "good"},
        summary_text="ok",
        plots_json=[],
        qc_config_json=snapshot,
        status="ready",
        generated_at=datetime.now(timezone.utc),
    )
    session.add(d_with)
    await session.flush()

    # Pre-existing dashboard predates qc_config_json snapshotting -- column is NULL.
    # Need a separate run because (org, pipeline_run_id) effectively pairs 1:1.
    run_legacy = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
        work_dir="/tmp/legacy",
    )
    session.add(run_legacy)
    await session.flush()

    d_legacy = QCDashboard(
        organization_id=org_id,
        pipeline_run_id=run_legacy.id,
        experiment_id=exp.id,
        metrics_json={"cell_count": 4321, "quality_rating": "good"},
        summary_text="legacy",
        plots_json=[],
        qc_config_json=None,
        status="ready",
        generated_at=datetime.now(timezone.utc),
    )
    session.add(d_legacy)
    await session.flush()
    await session.commit()

    return d_with, d_legacy


@pytest.mark.asyncio
async def test_get_dashboard_returns_qc_config_snapshot(client, admin_token, setup):
    d_with, _ = setup

    resp = await client.get(
        f"/api/qc-dashboards/{d_with.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "qc_config" in body
    assert body["qc_config"]["template"] == "scrnaseq"
    assert body["qc_config"]["sections"][0]["id"] == "hero"


@pytest.mark.asyncio
async def test_get_dashboard_substitutes_default_for_legacy_row(client, admin_token, setup):
    """Pre-migration dashboards have NULL qc_config_json. API substitutes
    the resolved template default so the page still renders."""
    _, d_legacy = setup

    resp = await client.get(
        f"/api/qc-dashboards/{d_legacy.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["qc_config"]["template"] == "scrnaseq"
    # Default scrnaseq render config has hero + cells sections
    section_ids = {s["id"] for s in body["qc_config"]["sections"]}
    assert "hero" in section_ids
    assert "cells" in section_ids
