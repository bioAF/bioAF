"""Activity feed logging for manifest-driven ingest events."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_feed import ActivityFeedEntry


async def log_manifest_detected(db: AsyncSession, org_id: int, batch_number: str, file_count: int) -> None:
    entry = ActivityFeedEntry(
        organization_id=org_id,
        event_type="sequencing_batch.detected",
        entity_type="sequencing_batch",
        summary=f"Sequencing batch {batch_number} found. Ingesting {file_count} files...",
        metadata_json={"batch_number": batch_number, "file_count": file_count, "severity": "info"},
    )
    db.add(entry)
    await db.flush()


async def log_file_verified(
    db: AsyncSession,
    org_id: int,
    batch_number: str,
    filename: str,
    project_name: str | None,
    experiment_name: str | None,
    sample_name: str | None,
) -> None:
    parts = [p for p in [project_name, experiment_name, sample_name] if p]
    association = ", ".join(parts) if parts else "unresolved"
    entry = ActivityFeedEntry(
        organization_id=org_id,
        event_type="sequencing_batch.file_verified",
        entity_type="sequencing_batch",
        summary=f"Sequencing batch {batch_number}: File {filename} associated with {association}",
        metadata_json={
            "batch_number": batch_number,
            "filename": filename,
            "severity": "info",
        },
    )
    db.add(entry)
    await db.flush()


async def log_file_retry(
    db: AsyncSession,
    org_id: int,
    batch_number: str,
    filename: str,
    retry_count: int,
    max_retries: int,
) -> None:
    entry = ActivityFeedEntry(
        organization_id=org_id,
        event_type="sequencing_batch.file_retry",
        entity_type="sequencing_batch",
        summary=(
            f"Sequencing batch {batch_number}: File {filename} not yet available, "
            f"will retry ({retry_count}/{max_retries})"
        ),
        metadata_json={
            "batch_number": batch_number,
            "filename": filename,
            "retry_count": retry_count,
            "severity": "info",
        },
    )
    db.add(entry)
    await db.flush()


async def log_file_failed(
    db: AsyncSession,
    org_id: int,
    batch_number: str,
    filename: str,
    max_retries: int,
) -> None:
    entry = ActivityFeedEntry(
        organization_id=org_id,
        event_type="sequencing_batch.file_failed",
        entity_type="sequencing_batch",
        summary=(f"Sequencing batch {batch_number}: File {filename} failed verification after {max_retries} attempts"),
        metadata_json={
            "batch_number": batch_number,
            "filename": filename,
            "severity": "warning",
        },
    )
    db.add(entry)
    await db.flush()


async def log_batch_complete(db: AsyncSession, org_id: int, batch_number: str, file_count: int) -> None:
    entry = ActivityFeedEntry(
        organization_id=org_id,
        event_type="sequencing_batch.complete",
        entity_type="sequencing_batch",
        summary=f"Sequencing batch {batch_number} complete. All {file_count} files ingested.",
        metadata_json={"batch_number": batch_number, "file_count": file_count, "severity": "info"},
    )
    db.add(entry)
    await db.flush()


async def log_batch_partial(
    db: AsyncSession,
    org_id: int,
    batch_number: str,
    ingested: int,
    total: int,
    failed: int,
) -> None:
    entry = ActivityFeedEntry(
        organization_id=org_id,
        event_type="sequencing_batch.partial",
        entity_type="sequencing_batch",
        summary=(
            f"Sequencing batch {batch_number} partially complete. {ingested}/{total} files ingested, {failed} failed."
        ),
        metadata_json={
            "batch_number": batch_number,
            "ingested": ingested,
            "total": total,
            "failed": failed,
            "severity": "warning",
        },
    )
    db.add(entry)
    await db.flush()
