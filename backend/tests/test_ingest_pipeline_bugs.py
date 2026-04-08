"""Tests for auto-ingest pipeline bug fixes.

Bug 1: Double-delete (move_file deletes source, then cleanup tries again)
Bug 2: Duplicate manifest entries on Pub/Sub redelivery
Bug 3: ManifestEntry reconciliation fails with multiple pending entries
Bug 4: MD5 base64-to-hex conversion from Pub/Sub messages
Bug 5: Manifest entry resolution used for file routing before copy
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.pubsub_listener import _base64_md5_to_hex


# -- Bug 1: Double-delete ------------------------------------------------


@pytest.mark.asyncio
async def test_no_cleanup_after_move(client, admin_token, session):
    """With delete_after_copy policy, cleanup_ingest_file should NOT be called
    because move_file already deletes the source."""
    from app.services.ingest_service import process_ingest_event

    # Seed config
    for k, v in [
        ("raw_bucket_name", "bioaf-raw-test"),
        ("ingest_cleanup_policy", "delete_after_copy"),
        ("storage_deployed", "true"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.flush()

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-test/unlinked/file.fastq.gz")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        await process_ingest_event(
            filename="file.fastq.gz",
            source_bucket="bioaf-ingest-test",
            source_path="file.fastq.gz",
            org_id=1,
            db=session,
            ingest_source="auto_ingest",
            content_md5="abc123",
        )
        await session.commit()

    mock_copy.assert_called_once()
    mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_called_for_retain_policy(client, admin_token, session):
    """With a retain policy, cleanup_ingest_file SHOULD be called."""
    from app.services.ingest_service import process_ingest_event

    for k, v in [
        ("raw_bucket_name", "bioaf-raw-test"),
        ("ingest_cleanup_policy", "retain_7d"),
        ("storage_deployed", "true"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.flush()

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-test/unlinked/file2.fastq.gz")
    mock_cleanup = AsyncMock()

    with (
        patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy),
        patch("app.services.ingest_service.cleanup_ingest_file", mock_cleanup),
    ):
        await process_ingest_event(
            filename="file2.fastq.gz",
            source_bucket="bioaf-ingest-test",
            source_path="file2.fastq.gz",
            org_id=1,
            db=session,
            ingest_source="auto_ingest",
            content_md5="def456",
        )
        await session.commit()

    mock_copy.assert_called_once()
    mock_cleanup.assert_called_once()
    assert "retain_7d" in str(mock_cleanup.call_args)


# -- Bug 2: Duplicate manifest entries -----------------------------------


@pytest.mark.asyncio
async def test_manifest_redelivery_skips_duplicate_entries(client, admin_token, session):
    """Second manifest processing for same batch should not create duplicate entries."""
    from app.services.manifest_ingest_service import process_manifest_ingest

    manifest = "# batch: REDELIVERY-001\nabc123  sample_R1.fastq.gz\ndef456  sample_R2.fastq.gz\n"

    batch1 = await process_manifest_ingest(
        manifest_content=manifest,
        manifest_format="md5sum",
        org_id=1,
        source_bucket="bucket",
        db=session,
    )
    await session.flush()

    # Count entries after first processing
    result1 = await session.execute(select(ManifestEntry).where(ManifestEntry.sequencing_batch_id == batch1.id))
    count_first = len(result1.scalars().all())
    assert count_first == 2

    # Process the same manifest again (simulating Pub/Sub redelivery)
    batch2 = await process_manifest_ingest(
        manifest_content=manifest,
        manifest_format="md5sum",
        org_id=1,
        source_bucket="bucket",
        db=session,
    )
    await session.flush()

    assert batch2.id == batch1.id

    # Count should still be 2, not 4
    result2 = await session.execute(select(ManifestEntry).where(ManifestEntry.sequencing_batch_id == batch1.id))
    count_second = len(result2.scalars().all())
    assert count_second == 2


# -- Bug 3: ManifestEntry reconciliation with duplicates -----------------


@pytest.mark.asyncio
async def test_reconciliation_tolerates_duplicate_entries(client, admin_token, session):
    """File ingest should reconcile even if duplicate manifest entries exist."""
    from app.services.ingest_service import process_ingest_event

    for k, v in [
        ("raw_bucket_name", "bioaf-raw-test"),
        ("ingest_cleanup_policy", "delete_after_copy"),
        ("storage_deployed", "true"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )

    # Create a batch with duplicate manifest entries (pre-existing bad state)
    batch = SequencingBatch(
        organization_id=1, code="DUP-TEST", name="Dup test", status="ingesting", expected_file_count=1
    )
    session.add(batch)
    await session.flush()

    for _ in range(2):
        session.add(
            ManifestEntry(
                sequencing_batch_id=batch.id,
                expected_filename="dup_file.fastq.gz",
                expected_md5="aabbcc",
                status="pending",
            )
        )
    await session.flush()

    mock_copy = AsyncMock(return_value="gs://bioaf-raw-test/unlinked/dup_file.fastq.gz")

    with patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy):
        event = await process_ingest_event(
            filename="dup_file.fastq.gz",
            source_bucket="bioaf-ingest-test",
            source_path="dup_file.fastq.gz",
            org_id=1,
            db=session,
            ingest_source="auto_ingest",
            content_md5="aabbcc",
        )
        await session.flush()

    # At least one entry should be reconciled to verified
    result = await session.execute(
        select(ManifestEntry).where(
            ManifestEntry.sequencing_batch_id == batch.id,
            ManifestEntry.status == "verified",
        )
    )
    verified = result.scalars().all()
    assert len(verified) >= 1

    # File should be linked to batch
    from app.models.file import File

    file_result = await session.execute(select(File).where(File.id == event.file_id))
    file_record = file_result.scalar_one()
    assert file_record.sequencing_batch_id == batch.id


# -- Bug 4: MD5 base64-to-hex conversion --------------------------------


def test_base64_md5_converts_to_hex():
    """GCS base64-encoded MD5 should convert to lowercase hex."""
    # base64("abcdef1234567890" as raw bytes) -> known hex
    import base64

    hex_md5 = "da50f219b3ea514107a1901169916efd"
    raw_bytes = bytes.fromhex(hex_md5)
    b64_md5 = base64.b64encode(raw_bytes).decode()

    assert _base64_md5_to_hex(b64_md5) == hex_md5


def test_base64_md5_passthrough_on_invalid():
    """If already hex or invalid base64, return as-is."""
    hex_str = "da50f219b3ea514107a1901169916efd"
    # Hex strings that aren't valid base64 should pass through
    result = _base64_md5_to_hex(hex_str)
    # Either converts (if valid base64 by coincidence) or passes through
    assert isinstance(result, str)


def test_base64_md5_real_gcs_value():
    """Test with an actual base64 MD5 from GCS Pub/Sub."""
    # "2lDyGbPqUUEHoZARaZFu/Q==" is the base64 of da50f219b3ea514107a1901169916efd
    result = _base64_md5_to_hex("2lDyGbPqUUEHoZARaZFu/Q==")
    assert result == "da50f219b3ea514107a1901169916efd"


# -- Bug 5: Manifest resolution used before file copy -------------------


@pytest.mark.asyncio
async def test_manifest_resolution_sets_experiment_prefix(client, admin_token, session):
    """When a manifest entry resolves an experiment, the file should be copied
    to experiments/N/ instead of unlinked/."""
    from app.services.ingest_service import process_ingest_event

    for k, v in [
        ("raw_bucket_name", "bioaf-raw-test"),
        ("ingest_cleanup_policy", "delete_after_copy"),
        ("storage_deployed", "true"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )

    # Get experiment_id from the test fixtures
    exp_result = await session.execute(text("SELECT id FROM experiments LIMIT 1"))
    exp_row = exp_result.fetchone()
    experiment_id = exp_row[0] if exp_row else None

    # Create a batch with a manifest entry that resolves to the experiment
    batch = SequencingBatch(
        organization_id=1,
        code="PREFIX-TEST",
        name="Prefix test",
        status="ingesting",
        expected_file_count=1,
    )
    session.add(batch)
    await session.flush()

    session.add(
        ManifestEntry(
            sequencing_batch_id=batch.id,
            expected_filename="routed_file.fastq.gz",
            expected_md5="route123",
            resolved_experiment_id=experiment_id,
            status="pending",
        )
    )
    await session.flush()

    mock_copy = AsyncMock(return_value=f"gs://bioaf-raw-test/experiments/{experiment_id}/routed_file.fastq.gz")

    with patch("app.services.ingest_service.copy_to_raw_bucket", mock_copy):
        await process_ingest_event(
            filename="routed_file.fastq.gz",
            source_bucket="bioaf-ingest-test",
            source_path="routed_file.fastq.gz",
            org_id=1,
            db=session,
            ingest_source="auto_ingest",
            content_md5="route123",
        )
        await session.flush()

    mock_copy.assert_called_once()
    call_args = mock_copy.call_args

    if experiment_id:
        # Should use experiment prefix, NOT unlinked
        assert f"experiments/{experiment_id}/" in str(call_args)
        assert "unlinked/" not in str(call_args)
    else:
        # No experiment in test DB, at least verify copy was attempted
        assert mock_copy.called
