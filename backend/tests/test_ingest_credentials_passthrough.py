"""Tests for GCS credentials passthrough in auto-ingest pipeline.

Verifies that user-configured service account credentials from
platform_config are passed to all downstream GCS operations,
rather than falling back to the Compute Engine default SA.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.pubsub_listener import PubSubListener


async def _setup_config(session: AsyncSession):
    """Insert platform_config keys including service account credentials."""
    for key, value in [
        ("auto_ingest_enabled", "true"),
        ("pubsub_subscription_name", "test-sub"),
        ("gcp_project_id", "test-project"),
        ("manifest_filename", "md5.txt"),
        ("manifest_format", "md5sum"),
        ("manifest_retry_interval_minutes", "15"),
        ("manifest_max_retries", "48"),
        ("default_org_id", "1"),
        ("gcp_credential_source", "service_account_key"),
        ("gcp_service_account_key", '{"type":"service_account","project_id":"test"}'),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:key, :value) "
                "ON CONFLICT (key) DO UPDATE SET value = :value"
            ).bindparams(key=key, value=value)
        )
    await session.flush()


@pytest.mark.asyncio
async def test_manifest_read_uses_stored_credentials(session: AsyncSession):
    """read_object_text should receive credentials from platform_config, not None."""
    await _setup_config(session)

    fake_creds = MagicMock(name="fake-sa-credentials")
    listener = PubSubListener()
    msg_data = {
        "bucket": "my-ingest-bucket",
        "name": "delivery/md5.txt",
        "size": "100",
        "md5Hash": "abc",
    }

    with (
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ),
        patch(
            "app.services.gcs_storage.GcsStorageService.read_object_text",
            new_callable=AsyncMock,
            return_value="# batch: B1\nhash  file.fastq.gz\n",
        ) as mock_read,
        patch(
            "app.services.manifest_ingest_service.process_manifest_ingest",
            new_callable=AsyncMock,
        ),
    ):
        await listener._handle_message(msg_data, session)

        mock_read.assert_called_once()
        call_kwargs = mock_read.call_args
        # credentials must be passed, not left as default None
        assert fake_creds in call_kwargs.args or call_kwargs.kwargs.get("credentials") is fake_creds, (
            "read_object_text was called without passing stored credentials"
        )


@pytest.mark.asyncio
async def test_file_ingest_receives_credentials(session: AsyncSession):
    """process_ingest_event should receive credentials for downstream GCS ops."""
    await _setup_config(session)

    fake_creds = MagicMock(name="fake-sa-credentials")
    listener = PubSubListener()
    msg_data = {
        "bucket": "my-ingest-bucket",
        "name": "delivery/sample_S1_L001_R1_001.fastq.gz",
        "size": "1000000",
        "md5Hash": "def456",
    }

    with (
        patch(
            "app.services.gcs_storage.GcsStorageService.get_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ),
        patch(
            "app.services.ingest_service.process_ingest_event",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        await listener._handle_message(msg_data, session)

        mock_ingest.assert_called_once()
        call_kwargs = mock_ingest.call_args
        assert call_kwargs.kwargs.get("credentials") is fake_creds, (
            "process_ingest_event was called without passing stored credentials"
        )


@pytest.mark.asyncio
async def test_copy_to_raw_passes_credentials_to_move_file(session: AsyncSession):
    """copy_to_raw_bucket must forward credentials to GcsStorageService.move_file."""
    from app.services.ingest_service import copy_to_raw_bucket

    fake_creds = MagicMock(name="fake-sa-credentials")

    with patch(
        "app.services.gcs_storage.GcsStorageService.move_file",
        new_callable=AsyncMock,
    ) as mock_move:
        await copy_to_raw_bucket(
            source_bucket="ingest-bucket",
            source_path="file.fastq.gz",
            raw_bucket="raw-bucket",
            destination_prefix="experiments/1/",
            filename="file.fastq.gz",
            credentials=fake_creds,
        )

        mock_move.assert_called_once()
        call_kwargs = mock_move.call_args
        assert call_kwargs.kwargs.get("credentials") is fake_creds or fake_creds in call_kwargs.args, (
            "move_file was called without credentials"
        )


@pytest.mark.asyncio
async def test_cleanup_ingest_uses_credentials(session: AsyncSession):
    """cleanup_ingest_file must use stored credentials, not default SA."""
    from app.services.ingest_service import cleanup_ingest_file

    fake_creds = MagicMock(name="fake-sa-credentials")

    with patch("google.cloud.storage") as mock_storage_mod:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage_mod.Client.return_value = mock_client
        mock_client.get_bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        await cleanup_ingest_file(
            source_bucket="ingest-bucket",
            source_path="file.fastq.gz",
            policy="delete_after_copy",
            credentials=fake_creds,
        )

        mock_storage_mod.Client.assert_called_once_with(credentials=fake_creds)
