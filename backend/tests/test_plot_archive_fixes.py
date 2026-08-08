"""Tests for plot archive bug fixes (#151 and related).

Covers:
- platform_config unique constraint enforcement (issue #151)
- Scanner uses app SA credentials, not bare ADC
- File content endpoint returns correct content-type for SVG and PDF
- PDF thumbnail generation and serving
- Thumbnail cleanup on file delete
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import text


@pytest_asyncio.fixture
async def experiment_for_plots(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Plot Fix Test Experiment",
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
        gcs_uri="gs://test-bucket/plots/umap_fix.png",
        filename="umap_fix.png",
        size_bytes=25000,
        file_type="image",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="UMAP Fix Test",
        experiment_id=experiment_for_plots.id,
        tags_json=["umap"],
        indexed_at=datetime.now(timezone.utc),
    )
    session.add(plot)
    await session.flush()
    await session.commit()
    return plot


@pytest_asyncio.fixture
async def sample_svg_file(session, admin_user):
    from app.models.file import File

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/plots/heatmap.svg",
        filename="heatmap.svg",
        size_bytes=8000,
        file_type="svg",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return f


@pytest_asyncio.fixture
async def sample_pdf_file(session, admin_user):
    from app.models.file import File

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/plots/stats_table.pdf",
        filename="stats_table.pdf",
        size_bytes=15000,
        file_type="pdf",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return f


# -- Issue #151: platform_config unique constraint --


@pytest.mark.asyncio
async def test_platform_config_rejects_duplicate_keys(session):
    """After migration 065, inserting a duplicate key into platform_config
    must raise an IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES ('_test_unique_key', 'first') ON CONFLICT DO NOTHING")
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(text("INSERT INTO platform_config (key, value) VALUES ('_test_unique_key', 'second')"))
        await session.commit()

    await session.rollback()

    # Cleanup
    await session.execute(text("DELETE FROM platform_config WHERE key = '_test_unique_key'"))
    await session.commit()


# -- Scanner uses app SA credentials --


@pytest.mark.asyncio
async def test_scan_and_index_lists_through_storage_adapter(session, admin_user):
    """scan_and_index must enumerate objects via the BAL storage adapter
    (Phase 3), which owns credential resolution -- not a bare GCS client.

    admin_user seeds an organization so the per-org scan loop actually runs.
    """
    from app.services.plot_archive_service import PlotArchiveService

    adapter = AsyncMock()
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key.lstrip('/')}")
    adapter.list_objects.return_value = []

    # Insert results_bucket_name so the scanner doesn't bail early
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) "
            "VALUES ('results_bucket_name', 'test-results-bucket') "
            "ON CONFLICT (key) DO UPDATE SET value = 'test-results-bucket'"
        )
    )
    await session.commit()

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await PlotArchiveService.scan_and_index(session)

    # Listing routed through the adapter against the configured results bucket.
    adapter.list_objects.assert_awaited()
    called_uri = adapter.list_objects.call_args.args[0]
    assert called_uri == "gs://test-results-bucket/"


# -- File content endpoint: SVG content type --


@pytest.mark.asyncio
async def test_content_endpoint_returns_svg_content_type(client, admin_token, sample_svg_file):
    """GET /api/files/{id}/content for an SVG file must return
    Content-Type: image/svg+xml."""
    adapter = AsyncMock()
    adapter.read_bytes.return_value = b"<svg></svg>"

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.get(
            f"/api/files/{sample_svg_file.id}/content",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"


# -- File content endpoint: PDF content type --


@pytest.mark.asyncio
async def test_content_endpoint_returns_pdf_content_type(client, admin_token, sample_pdf_file):
    """GET /api/files/{id}/content for a PDF file must return
    Content-Type: application/pdf."""
    adapter = AsyncMock()
    adapter.read_bytes.return_value = b"%PDF-1.4 fake"

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.get(
            f"/api/files/{sample_pdf_file.id}/content",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


# -- Thumbnail generation --


@pytest.mark.asyncio
async def test_render_pdf_thumbnail_returns_png_bytes():
    """ThumbnailService.render_pdf_thumbnail produces valid PNG bytes from a simple PDF."""
    from app.services.thumbnail_service import ThumbnailService
    import fitz

    # Create a minimal one-page PDF in memory
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((50, 100), "Test")
    pdf_bytes = doc.tobytes()
    doc.close()

    result = ThumbnailService.render_pdf_thumbnail(pdf_bytes)
    assert result is not None
    assert result[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_render_pdf_thumbnail_returns_none_for_invalid_input():
    """ThumbnailService.render_pdf_thumbnail returns None for non-PDF input."""
    from app.services.thumbnail_service import ThumbnailService

    result = ThumbnailService.render_pdf_thumbnail(b"not a pdf")
    assert result is None


# -- Scanner skips _thumbnails/ prefix --


@pytest.mark.asyncio
async def test_scanner_skips_thumbnails_prefix(session):
    """scan_and_index must skip blobs under the _thumbnails/ prefix."""
    from app.services.plot_archive_service import PlotArchiveService

    mock_creds = MagicMock()
    mock_get_creds = AsyncMock(return_value=mock_creds)
    mock_client_cls = MagicMock()
    mock_bucket = mock_client_cls.return_value.bucket.return_value

    # Create two blobs: one real plot, one thumbnail
    mock_real_blob = MagicMock()
    mock_real_blob.name = "experiments/1/plots/heatmap.png"
    mock_real_blob.updated = None
    mock_real_blob.size = 5000

    mock_thumb_blob = MagicMock()
    mock_thumb_blob.name = "_thumbnails/plot_1.png"
    mock_thumb_blob.updated = None
    mock_thumb_blob.size = 2000

    mock_bucket.list_blobs.return_value = [mock_real_blob, mock_thumb_blob]

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) "
            "VALUES ('results_bucket_name', 'test-results-bucket') "
            "ON CONFLICT (key) DO UPDATE SET value = 'test-results-bucket'"
        )
    )
    await session.commit()

    with (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            mock_get_creds,
        ),
    ):
        indexed = await PlotArchiveService.scan_and_index(session)

    # Only the real plot should be indexed, not the thumbnail
    assert indexed <= 1  # May be 0 if already indexed


# -- Thumbnail content endpoint --


@pytest.mark.asyncio
async def test_thumbnail_content_endpoint_returns_png(client, admin_token, session, admin_user, experiment_for_plots):
    """GET /api/plots/{id}/thumbnail/content returns PNG bytes when thumbnail exists."""
    from app.models.file import File
    from app.models.plot_archive_entry import PlotArchiveEntry
    from datetime import datetime, timezone

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/plots/stats.pdf",
        filename="stats.pdf",
        size_bytes=20000,
        file_type="pdf",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="stats.pdf",
        experiment_id=experiment_for_plots.id,
        thumbnail_gcs_uri="gs://test-bucket/_thumbnails/plot_99.png",
        indexed_at=datetime.now(timezone.utc),
    )
    session.add(plot)
    await session.flush()
    await session.commit()

    adapter = AsyncMock()
    adapter.read_bytes.return_value = b"\x89PNG fake thumbnail"

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.get(
            f"/api/plots/{plot.id}/thumbnail/content",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG fake thumbnail"


@pytest.mark.asyncio
async def test_thumbnail_content_endpoint_404_when_no_thumbnail(client, admin_token, sample_plot):
    """GET /api/plots/{id}/thumbnail/content returns 404 when no thumbnail exists."""
    resp = await client.get(
        f"/api/plots/{sample_plot.id}/thumbnail/content",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


# -- Backfill endpoint returns thumbnail count --


@pytest.mark.asyncio
async def test_backfill_endpoint_returns_thumbnail_count(client, admin_token):
    """POST /api/plots/backfill returns both metadata_updated and thumbnails_generated."""
    mock_client_cls = MagicMock()

    with (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.post(
            "/api/plots/backfill",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "metadata_updated" in data
    assert "thumbnails_generated" in data


# -- Delete cleanup --


@pytest.mark.asyncio
async def test_file_delete_cleans_up_thumbnails(client, admin_token, session, admin_user, experiment_for_plots):
    """Deleting a file with an associated plot thumbnail must delete the
    thumbnail blob from GCS."""
    from app.models.file import File
    from app.models.plot_archive_entry import PlotArchiveEntry
    from datetime import datetime, timezone

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/plots/cleanup-test.pdf",
        filename="cleanup-test.pdf",
        size_bytes=10000,
        file_type="pdf",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    plot = PlotArchiveEntry(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="cleanup-test.pdf",
        experiment_id=experiment_for_plots.id,
        thumbnail_gcs_uri="gs://test-bucket/_thumbnails/plot_cleanup.png",
        indexed_at=datetime.now(timezone.utc),
    )
    session.add(plot)
    await session.flush()
    await session.commit()

    mock_delete_thumb = AsyncMock(return_value=True)

    with patch(
        "app.services.thumbnail_service.ThumbnailService.delete_thumbnail",
        mock_delete_thumb,
    ):
        resp = await client.delete(
            f"/api/files/{f.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    mock_delete_thumb.assert_called_once()
    call_args = mock_delete_thumb.call_args
    assert call_args[0][1] == "gs://test-bucket/_thumbnails/plot_cleanup.png"


# -- Scanner skips HTML report chrome ----------------------------------------
#
# 52 of the 188 entries in the deployed archive were Qualimap report theme
# assets: `up.png`, `plus.png`, `bgtop.png`, `comment-bright.png` and friends,
# 13 filenames across 4 samples. They were indexed because the scanner's whole
# definition of "plot" was "an image file somewhere in the results bucket".
#
# The discriminator is the DIRECTORY, not the filename. Qualimap writes its
# chrome to `<sample>/css/` and its real plots to the sibling
# `<sample>/images_qualimapReport/`, so a filename blocklist would be endless
# and would still let the next report tool's icons through. The paths below are
# verbatim from the deployed bucket.

REAL_CHROME = [
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/up.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/up-pressed.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/down.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/down-pressed.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/plus.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/minus.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/file.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/comment.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/comment-bright.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/comment-close.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/bgtop.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/bgfooter.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/css/qualimap_logo_small.png",
]

REAL_PLOTS = [
    # The sibling directory, one path segment away from the chrome above.
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/images_qualimapReport/"
    "Transcript coverage histogram.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/images_qualimapReport/"
    "Junction Analysis.png",
    "experiments/12/pipeline-runs/17/star_salmon/qualimap/SRX30659361/images_qualimapReport/"
    "Coverage Profile Along Genes (High).png",
    "experiments/12/pipeline-runs/17/multiqc/multiqc_plots/png/fastqc_sequence_counts_plot-cnt.png",
    "experiments/12/pipeline-runs/17/multiqc/multiqc_plots/svg/fastqc_per_base_sequence_quality_plot.svg",
    "experiments/12/pipeline-runs/17/multiqc/multiqc_plots/pdf/general_stats_table.pdf",
    "experiments/12/pipeline-runs/17/star_salmon/rseqc/junction_annotation/pdf/SAMPLE.junction.pdf",
    "experiments/12/pipeline-runs/17/star_salmon/dupradar/box_plot/SAMPLE_duprateExpBoxplot.pdf",
    "experiments/12/pipeline-runs/17/star_salmon/dupradar/scatter_plot/SAMPLE_duprateExpDens.pdf",
    "experiments/12/pipeline-runs/17/star_salmon/dupradar/histogram/SAMPLE_expressionHist.pdf",
    "experiments/12/pipeline-runs/17/star_salmon/deseq2_qc/deseq2.plots.pdf",
    "experiments/12/pipeline-runs/17/star/SAMPLE-101/cellbender_removebackground/SAMPLE-101.pdf",
]


def test_report_chrome_is_not_a_plot():
    """Every asset directory seen in the deployed bucket is rejected."""
    from app.services.plot_archive_service import is_report_asset

    missed = [p for p in REAL_CHROME if not is_report_asset(p)]
    assert missed == []


def test_real_plots_survive_the_filter():
    """The filter must not cost us a single genuine plot.

    `images_qualimapReport` starts with the word "images" and sits beside the
    chrome; a substring match rather than a whole-segment match would delete 24
    real Qualimap plots while fixing 52 icons.
    """
    from app.services.plot_archive_service import is_report_asset

    rejected = [p for p in REAL_PLOTS if is_report_asset(p)]
    assert rejected == []


def test_filter_matches_whole_segments_only():
    from app.services.plot_archive_service import is_report_asset

    # A directory that merely CONTAINS an asset word is not an asset directory.
    assert not is_report_asset("experiments/1/pipeline-runs/2/cssplots/heatmap.png")
    assert not is_report_asset("experiments/1/pipeline-runs/2/my_js_report/plot.png")
    # ...and a file whose NAME matches is still a plot; only directories count.
    assert not is_report_asset("experiments/1/pipeline-runs/2/plots/css.png")
    # Case does not matter, and the directory can sit at any depth.
    assert is_report_asset("experiments/1/CSS/icon.png")
    assert is_report_asset("a/b/c/d/_static/sphinx/arrow.png")
