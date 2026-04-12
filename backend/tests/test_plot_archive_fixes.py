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
async def test_scan_and_index_uses_app_credentials(session):
    """scan_and_index must use GcsStorageService.get_credentials, not bare
    ADC, to authenticate with GCS."""
    from app.services.plot_archive_service import PlotArchiveService

    mock_creds = MagicMock()
    mock_get_creds = AsyncMock(return_value=mock_creds)
    mock_client_cls = MagicMock()
    mock_bucket = mock_client_cls.return_value.bucket.return_value
    mock_bucket.list_blobs.return_value = []

    # Insert results_bucket_name so the scanner doesn't bail early
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
        await PlotArchiveService.scan_and_index(session)

    # Verify get_credentials was called
    mock_get_creds.assert_called_once()

    # Verify Client was instantiated with those credentials
    mock_client_cls.assert_called_once_with(credentials=mock_creds)


# -- File content endpoint: SVG content type --


@pytest.mark.asyncio
async def test_content_endpoint_returns_svg_content_type(client, admin_token, sample_svg_file):
    """GET /api/files/{id}/content for an SVG file must return
    Content-Type: image/svg+xml."""
    mock_client_cls = MagicMock()
    mock_blob = mock_client_cls.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"<svg></svg>"

    with (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
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
    mock_client_cls = MagicMock()
    mock_blob = mock_client_cls.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"%PDF-1.4 fake"

    with (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
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

    mock_client_cls = MagicMock()
    mock_blob = mock_client_cls.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"\x89PNG fake thumbnail"

    with (
        patch("google.cloud.storage.Client", mock_client_cls),
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
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
