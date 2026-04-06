"""Sample completeness evaluation for pipeline triggers.

Checks whether all manifest entries for a given sample within a
sequencing batch have been verified, enabling sample-complete
pipeline triggers.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry

logger = logging.getLogger("bioaf.sample_completeness")


async def check_sample_completeness(
    db: AsyncSession,
    sample_id: int,
    sequencing_batch_id: int,
) -> bool:
    """Check if all manifest entries for a sample in a batch are verified.

    Returns True if every ManifestEntry for this sample in this
    sequencing batch has status 'verified'.
    """
    # Count total entries for this sample in this batch
    total_result = await db.execute(
        select(func.count(ManifestEntry.id)).where(
            ManifestEntry.resolved_sample_id == sample_id,
            ManifestEntry.sequencing_batch_id == sequencing_batch_id,
        )
    )
    total = total_result.scalar() or 0

    if total == 0:
        return False

    # Count verified entries
    verified_result = await db.execute(
        select(func.count(ManifestEntry.id)).where(
            ManifestEntry.resolved_sample_id == sample_id,
            ManifestEntry.sequencing_batch_id == sequencing_batch_id,
            ManifestEntry.status == "verified",
        )
    )
    verified = verified_result.scalar() or 0

    return verified == total
