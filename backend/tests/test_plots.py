import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def experiment_for_plots(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Plot Test Experiment",
        owner_user_id=admin_user.id,
        status="analysis",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def sample_plot(session, admin_user, experiment_for_plots):
    from app.models.file import File
    from app.models.plot_archive_entry import PlotArchiveEntry
    from datetime import datetime, timezone

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/plots/umap.png",
        filename="umap.png",
        size_bytes=25000,
        file_type="image",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="UMAP Clustering",
        experiment_id=experiment_for_plots.id,
        tags_json=["umap", "clustering"],
        indexed_at=datetime.now(timezone.utc),
    )
    session.add(plot)
    await session.flush()
    await session.commit()
    return plot


@pytest.mark.asyncio
async def test_search_plots(client, admin_token, sample_plot):
    resp = await client.get(
        "/api/plots",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(p["title"] == "UMAP Clustering" for p in data["plots"])


@pytest.mark.asyncio
async def test_search_plots_includes_storage_deleted_flag(
    client, admin_token, session, admin_user, experiment_for_plots
):
    from app.models.file import File
    from app.models.plot_archive_entry import PlotArchiveEntry
    from datetime import datetime, timezone

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/plots/deleted-plot.png",
        filename="deleted-plot.png",
        size_bytes=10000,
        file_type="image",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="Deleted Plot",
        experiment_id=experiment_for_plots.id,
        tags_json=["deleted"],
        indexed_at=datetime.now(timezone.utc),
    )
    session.add(plot)
    await session.flush()
    await session.commit()

    # Mark as storage_deleted after insert to avoid server_default override
    await session.execute(text("UPDATE files SET storage_deleted = true WHERE id = :fid").bindparams(fid=f.id))
    await session.commit()

    # Verify the update took effect
    check = await session.execute(text("SELECT storage_deleted FROM files WHERE id = :fid").bindparams(fid=f.id))
    assert check.scalar() is True

    resp = await client.get(
        "/api/plots",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    deleted_plot = next((p for p in data["plots"] if p["title"] == "Deleted Plot"), None)
    assert deleted_plot is not None
    assert deleted_plot["file"]["storage_deleted"] is True


@pytest.mark.asyncio
async def test_search_plots_with_query(client, admin_token, sample_plot):
    resp = await client.get(
        "/api/plots?query=umap",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_get_plot(client, admin_token, sample_plot):
    resp = await client.get(
        f"/api/plots/{sample_plot.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "UMAP Clustering"
    assert "umap" in resp.json()["tags"]


@pytest.mark.asyncio
async def test_update_plot(client, admin_token, sample_plot):
    resp = await client.patch(
        f"/api/plots/{sample_plot.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "Updated UMAP", "tags": ["umap", "updated"]},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated UMAP"


@pytest.mark.asyncio
async def test_get_thumbnail(client, admin_token, sample_plot):
    resp = await client.get(
        f"/api/plots/{sample_plot.id}/thumbnail",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    # Should return the file's GCS URI as fallback since no thumbnail_gcs_uri set
    assert resp.json()["thumbnail_url"] is not None


@pytest.mark.asyncio
async def test_parse_ids_from_path():
    from app.services.plot_archive_service import PlotArchiveService

    # Standard pipeline-runs path
    exp_id, run_id = PlotArchiveService._parse_ids_from_path(
        "gs://bucket/experiments/1/pipeline-runs/15/multiqc/plots/fastqc.png"
    )
    assert exp_id == 1
    assert run_id == 15

    # Legacy runs path
    exp_id, run_id = PlotArchiveService._parse_ids_from_path("gs://bucket/experiments/3/runs/7/output.png")
    assert exp_id == 3
    assert run_id == 7

    # No context in path
    exp_id, run_id = PlotArchiveService._parse_ids_from_path("gs://bucket/misc/plot.png")
    assert exp_id is None
    assert run_id is None


@pytest.mark.asyncio
async def test_backfill_metadata(session, admin_user, experiment_for_plots):
    from app.models.file import File
    from app.models.pipeline_run import PipelineRun
    from app.models.plot_archive_entry import PlotArchiveEntry
    from app.services.plot_archive_service import PlotArchiveService
    from datetime import datetime, timezone

    # Create a pipeline run so FK is valid
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment_for_plots.id,
        pipeline_name="nf-core/rnaseq",
        status="completed",
        submitted_by_user_id=admin_user.id,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    exp_id = experiment_for_plots.id
    run_id = run.id

    # Create a plot with a GCS URI that embeds the experiment/run IDs but NULL FK columns
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri=f"gs://test-bucket/experiments/{exp_id}/pipeline-runs/{run_id}/plots/heatmap.png",
        filename="heatmap.png",
        size_bytes=5000,
        file_type="png",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="heatmap.png",
        experiment_id=None,
        pipeline_run_id=None,
        indexed_at=datetime.now(timezone.utc),
    )
    session.add(plot)
    await session.flush()
    await session.commit()

    updated = await PlotArchiveService.backfill_metadata(session)
    assert updated >= 1

    await session.refresh(plot)
    assert plot.experiment_id == exp_id
    assert plot.pipeline_run_id == run_id
