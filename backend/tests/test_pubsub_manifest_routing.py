"""Tests for pub-sub listener manifest file detection and routing."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.pubsub_listener import PubSubListener


@pytest.fixture(autouse=True)
def _patch_get_credentials():
    """All routing tests use ADC (credentials=None) since GCS calls are mocked."""
    with patch(
        "app.services.gcs_storage.GcsStorageService.get_credentials",
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield


async def _setup_config(session: AsyncSession, manifest_filename: str = "md5.txt"):
    """Insert platform_config keys needed by the listener."""
    for key, value in [
        ("auto_ingest_enabled", "true"),
        ("pubsub_subscription_name", "test-sub"),
        ("gcp_project_id", "test-project"),
        ("manifest_filename", manifest_filename),
        ("manifest_format", "md5sum"),
        ("manifest_retry_interval_minutes", "15"),
        ("manifest_max_retries", "48"),
        ("default_org_id", "1"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:key, :value) "
                "ON CONFLICT (key) DO UPDATE SET value = :value"
            ).bindparams(key=key, value=value)
        )
    await session.flush()


@pytest.mark.asyncio
async def test_manifest_routes_to_manifest_ingest(session: AsyncSession):
    """A file named md5.txt should route to process_manifest_ingest, not process_ingest_event."""
    await _setup_config(session)

    manifest_content = "# batch: CCB0001\nabc123  sample_S1_L001_R1_001.fastq.gz\n"

    listener = PubSubListener()
    msg_data = {
        "bucket": "my-ingest-bucket",
        "name": "delivery/md5.txt",
        "size": "100",
        "md5Hash": "abc",
    }

    with (
        patch(
            "app.services.gcs_storage.GcsStorageService.read_object_text",
            new_callable=AsyncMock,
            return_value=manifest_content,
        ) as mock_read,
        patch(
            "app.services.manifest_ingest_service.process_manifest_ingest",
            new_callable=AsyncMock,
        ) as mock_manifest,
        patch(
            "app.services.ingest_service.process_ingest_event",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        await listener._handle_message(msg_data, session)

        mock_read.assert_called_once_with("my-ingest-bucket", "delivery/md5.txt", credentials=None)
        mock_manifest.assert_called_once()
        mock_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_fastq_routes_to_ingest_event(session: AsyncSession):
    """A FASTQ file should route to process_ingest_event, not manifest ingest."""
    await _setup_config(session)

    listener = PubSubListener()
    msg_data = {
        "bucket": "my-ingest-bucket",
        "name": "delivery/sample_S1_L001_R1_001.fastq.gz",
        "size": "1000000",
        "md5Hash": "def456",
    }

    with (
        patch(
            "app.services.ingest_service.process_ingest_event",
            new_callable=AsyncMock,
        ) as mock_ingest,
        patch(
            "app.services.manifest_ingest_service.process_manifest_ingest",
            new_callable=AsyncMock,
        ) as mock_manifest,
    ):
        await listener._handle_message(msg_data, session)

        mock_ingest.assert_called_once()
        mock_manifest.assert_not_called()


@pytest.mark.asyncio
async def test_custom_manifest_filename(session: AsyncSession):
    """Custom manifest filename (md5s.txt) is respected."""
    await _setup_config(session, manifest_filename="md5s.txt")

    listener = PubSubListener()

    # md5s.txt should route to manifest
    msg_manifest = {
        "bucket": "bucket",
        "name": "data/md5s.txt",
        "size": "50",
    }

    with (
        patch(
            "app.services.gcs_storage.GcsStorageService.read_object_text",
            new_callable=AsyncMock,
            return_value="# batch: B1\nhash  file.fastq.gz\n",
        ),
        patch(
            "app.services.manifest_ingest_service.process_manifest_ingest",
            new_callable=AsyncMock,
        ) as mock_manifest,
        patch(
            "app.services.ingest_service.process_ingest_event",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        await listener._handle_message(msg_manifest, session)
        mock_manifest.assert_called_once()
        mock_ingest.assert_not_called()

    # md5.txt (the old default) should NOT route to manifest with this config
    msg_other = {
        "bucket": "bucket",
        "name": "data/md5.txt",
        "size": "50",
        "md5Hash": "xyz",
    }

    with (
        patch(
            "app.services.ingest_service.process_ingest_event",
            new_callable=AsyncMock,
        ) as mock_ingest2,
        patch(
            "app.services.manifest_ingest_service.process_manifest_ingest",
            new_callable=AsyncMock,
        ) as mock_manifest2,
    ):
        await listener._handle_message(msg_other, session)
        mock_ingest2.assert_called_once()
        mock_manifest2.assert_not_called()


@pytest.mark.asyncio
async def test_manifest_detection_case_insensitive(session: AsyncSession):
    """MD5.TXT should still route to manifest ingest."""
    await _setup_config(session)

    listener = PubSubListener()
    msg_data = {
        "bucket": "bucket",
        "name": "data/MD5.TXT",
        "size": "50",
    }

    with (
        patch(
            "app.services.gcs_storage.GcsStorageService.read_object_text",
            new_callable=AsyncMock,
            return_value="# batch: B1\nhash  file.fastq.gz\n",
        ),
        patch(
            "app.services.manifest_ingest_service.process_manifest_ingest",
            new_callable=AsyncMock,
        ) as mock_manifest,
        patch(
            "app.services.ingest_service.process_ingest_event",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        await listener._handle_message(msg_data, session)
        mock_manifest.assert_called_once()
        mock_ingest.assert_not_called()
