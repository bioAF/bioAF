"""Background retry service for manifest file verification.

Checks pending ManifestEntry records, increments retry counts,
marks entries as failed when max retries exceeded, and updates
SequencingBatch status accordingly.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch

logger = logging.getLogger("bioaf.manifest_retry")


async def process_manifest_retries(
    db: AsyncSession,
    max_retries: int = 48,
) -> None:
    """Process all pending manifest entries, updating retries and batch statuses.

    For each pending entry:
    - If retry_count >= max_retries, mark as 'failed'
    - Otherwise, increment retry_count and update last_check_at

    Then update each affected SequencingBatch's status based on its entries.
    """
    now = datetime.now(timezone.utc)

    # Find all pending entries
    result = await db.execute(select(ManifestEntry).where(ManifestEntry.status == "pending"))
    pending_entries = list(result.scalars().all())

    affected_batch_ids: set[int] = set()

    for entry in pending_entries:
        entry.retry_count += 1
        entry.last_check_at = now

        if entry.retry_count >= max_retries:
            entry.status = "failed"
            entry.error_message = f"Max retries ({max_retries}) exceeded"

        affected_batch_ids.add(entry.sequencing_batch_id)

    await db.flush()

    # Update batch statuses for all affected batches
    # Also check batches that already have all entries resolved
    batch_ids_result = await db.execute(select(ManifestEntry.sequencing_batch_id).distinct())
    all_batch_ids = {row[0] for row in batch_ids_result.fetchall()}
    affected_batch_ids.update(all_batch_ids)

    for batch_id in affected_batch_ids:
        await _update_batch_status(db, batch_id)

    await db.flush()


async def _update_batch_status(db: AsyncSession, batch_id: int) -> None:
    """Recalculate and set the SequencingBatch status from its ManifestEntry statuses."""
    result = await db.execute(select(SequencingBatch).where(SequencingBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        return

    # Count entries by status
    counts_result = await db.execute(
        select(ManifestEntry.status, func.count(ManifestEntry.id))
        .where(ManifestEntry.sequencing_batch_id == batch_id)
        .group_by(ManifestEntry.status)
    )
    counts = {row[0]: row[1] for row in counts_result.fetchall()}

    verified = counts.get("verified", 0)
    pending = counts.get("pending", 0)
    failed = counts.get("failed", 0)
    total = sum(counts.values())

    if total == 0:
        return

    if verified == total:
        batch.status = "complete"
        batch.ingested_file_count = verified
    elif failed > 0 and pending == 0:
        # All non-verified are failed, no more pending
        if verified > 0:
            batch.status = "partial_complete"
        else:
            batch.status = "failed"
        batch.ingested_file_count = verified
    elif pending > 0:
        batch.status = "ingesting"
        batch.ingested_file_count = verified
