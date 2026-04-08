"""Tests for ingest pipeline with real GCS operations (Phase 21).

Tests 8-14: File copy, cleanup, gcs_uri, experiment prefix, unlinked prefix,
MD5 from Pub/Sub, pipeline trigger evaluation.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text


async def _seed_config(session, overrides=None):
    defaults = {
        "storage_deployed": "true",
        "ingest_bucket_name": "bioaf-ingest-testorg",
        "raw_bucket_name": "bioaf-raw-testorg",
        "working_bucket_name": "bioaf-working-testorg",
        "results_bucket_name": "bioaf-results-testorg",
        "config_backups_bucket_name": "bioaf-config-backups-testorg",
        "auto_ingest_enabled": "true",
        "ingest_cleanup_policy": "delete_after_copy",
        "pubsub_topic_name": "bioaf-ingest-events-testorg",
        "pubsub_subscription_name": "bioaf-ingest-worker-testorg",
    }
    if overrides:
        defaults.update(overrides)
    for key, value in defaults.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


@pytest_asyncio.fixture
async def _setup_org_and_profile(client, admin_token, session):
    """Create a naming profile and seed platform_config for GCS ingest tests."""
    await _seed_config(session)
    await client.post(
        "/api/naming-profiles",
        json={
            "name": "GCS Test Profile",
            "segments": [
                {"position": 0, "field": "project_code", "required": True},
                {"position": 1, "field": "experiment_code", "required": True},
                {"position": 2, "field": "sample_id", "required": True},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )


@pytest.mark.asyncio
async def test_ingest_copies_file_to_raw_bucket(client, admin_token, session, _setup_org_and_profile):
    """Process an ingest event and verify file is copied from ingest to raw bucket."""
    from app.services.ingest_service import process_ingest_event

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/experiments/1/PROJ1_EXP1_S001.fastq.gz")
    mock_delete = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_delete),
    ):
        await process_ingest_event(
            filename="PROJ1_EXP1_S001.fastq.gz",
            source_bucket="bioaf-ingest-testorg",
            source_path="PROJ1_EXP1_S001.fastq.gz",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1048576,
            content_md5="abc123",
            ingest_source="auto_ingest",
        )
        await session.commit()

    mock_copy.assert_called_once()
    call_args = mock_copy.call_args
    assert "bioaf-ingest-testorg" in str(call_args)
    assert "bioaf-raw-testorg" in str(call_args)


@pytest.mark.asyncio
async def test_ingest_skips_cleanup_with_delete_after_copy(client, admin_token, session, _setup_org_and_profile):
    """With delete_after_copy policy, cleanup is skipped because move_file already deletes the source."""
    from app.services.ingest_service import process_ingest_event

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/experiments/1/PROJ1_EXP1_S001.fastq.gz")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        await process_ingest_event(
            filename="PROJ1_EXP1_S001.fastq.gz",
            source_bucket="bioaf-ingest-testorg",
            source_path="PROJ1_EXP1_S001.fastq.gz",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1048576,
            content_md5="def456",
            ingest_source="auto_ingest",
        )
        await session.commit()

    mock_copy.assert_called_once()
    # cleanup_ingest_file should NOT be called because move_file already
    # deletes the source file as part of the copy+verify+delete sequence
    mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_retains_on_retain_policy(client, admin_token, session, _setup_org_and_profile):
    """With retain_7d policy, ingest bucket object is NOT deleted."""
    await _seed_config(session, {"ingest_cleanup_policy": "retain_7d"})

    from app.services.ingest_service import process_ingest_event

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/experiments/1/PROJ1_EXP1_S001.fastq.gz")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        await process_ingest_event(
            filename="PROJ1_EXP1_S001.fastq.gz",
            source_bucket="bioaf-ingest-testorg",
            source_path="PROJ1_EXP1_S001.fastq.gz",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1048576,
            content_md5="ghi789",
            ingest_source="auto_ingest",
        )
        await session.commit()

    # cleanup should be called with retain policy
    mock_cleanup.assert_called_once()
    call_args = mock_cleanup.call_args
    assert "retain_7d" in str(call_args)


@pytest.mark.asyncio
async def test_ingest_sets_gcs_uri_to_raw_location(client, admin_token, session, _setup_org_and_profile):
    """File record's gcs_uri points to the raw bucket, not the ingest bucket."""
    from app.models.file import File
    from app.services.ingest_service import process_ingest_event
    from sqlalchemy import select

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/experiments/1/PROJ1_EXP1_S001.fastq.gz")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        event = await process_ingest_event(
            filename="PROJ1_EXP1_S001.fastq.gz",
            source_bucket="bioaf-ingest-testorg",
            source_path="PROJ1_EXP1_S001.fastq.gz",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1048576,
            content_md5="jkl012",
            ingest_source="auto_ingest",
        )
        await session.commit()

    # Check the file record
    result = await session.execute(select(File).where(File.id == event.file_id))
    file_record = result.scalar_one()
    assert "bioaf-raw-testorg" in file_record.gcs_uri
    assert "bioaf-ingest" not in file_record.gcs_uri


@pytest.mark.asyncio
async def test_ingest_uses_experiment_prefix(client, admin_token, session, _setup_org_and_profile):
    """When experiment resolves, raw bucket path includes experiments/{id}/."""
    from app.services.ingest_service import process_ingest_event

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/experiments/1/file.fastq.gz")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        await process_ingest_event(
            filename="PROJ1_EXP1_S001.fastq.gz",
            source_bucket="bioaf-ingest-testorg",
            source_path="PROJ1_EXP1_S001.fastq.gz",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1048576,
            content_md5="mno345",
            ingest_source="auto_ingest",
        )
        await session.commit()

    mock_copy.assert_called_once()
    call_args = mock_copy.call_args
    # The destination should include an experiment prefix
    assert "experiments/" in str(call_args)


@pytest.mark.asyncio
async def test_ingest_uses_unlinked_prefix_when_no_experiment(client, admin_token, session, _setup_org_and_profile):
    """When no experiment resolves, raw bucket path includes unlinked/."""
    from app.services.ingest_service import process_ingest_event

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/unlinked/random.bam")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        await process_ingest_event(
            filename="random_file.bam",
            source_bucket="bioaf-ingest-testorg",
            source_path="random_file.bam",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1024,
            content_md5="pqr678",
            ingest_source="auto_ingest",
        )
        await session.commit()

    mock_copy.assert_called_once()
    call_args = mock_copy.call_args
    assert "unlinked/" in str(call_args)


@pytest.mark.asyncio
async def test_ingest_md5_from_pubsub_message(client, admin_token, session, _setup_org_and_profile):
    """MD5 from Pub/Sub message is used for duplicate detection."""
    from app.services.ingest_service import process_ingest_event

    md5_hash = "abc123hash"

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-testorg/unlinked/file1.fastq")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        # First ingest
        await process_ingest_event(
            filename="file1.fastq",
            source_bucket="bioaf-ingest-testorg",
            source_path="file1.fastq",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1024,
            content_md5=md5_hash,
            ingest_source="auto_ingest",
        )
        await session.commit()

        # Second ingest with same MD5
        event2 = await process_ingest_event(
            filename="file1_copy.fastq",
            source_bucket="bioaf-ingest-testorg",
            source_path="file1_copy.fastq",
            org_id=1,
            db=session,
            user_id=None,
            file_size_bytes=1024,
            content_md5=md5_hash,
            ingest_source="auto_ingest",
        )
        await session.commit()

    assert event2.ingest_status == "duplicate"
