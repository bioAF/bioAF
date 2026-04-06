"""Manifest-driven ingest service.

Processes manifest files (md5.txt or CSV) to create SequencingBatch and
ManifestEntry records, resolve filenames to samples via naming profiles,
and prepare for file verification.
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.manifest_parser import parse_manifest
from app.services.naming_profile_parser import match_filename, resolve_entities
from app.services.naming_profile_service import NamingProfileService

logger = logging.getLogger("bioaf.manifest_ingest")


async def process_manifest_ingest(
    manifest_content: str,
    manifest_format: str,
    org_id: int,
    source_bucket: str,
    db: AsyncSession,
    user_id: int | None = None,
) -> SequencingBatch:
    """Process a manifest file through the 6-step ingest flow.

    Steps:
    1. Parse manifest
    2. Create or find SequencingBatch
    3. Resolve samples from filenames
    4. Create ManifestEntry records (all start as 'pending')
    5. Update batch status
    6. Emit events (deferred to caller)

    Returns the SequencingBatch record.
    """
    # Step 1: Parse manifest
    parse_result = parse_manifest(manifest_content, manifest_format)
    batch_number = parse_result.batch_number or f"UNKNOWN-{org_id}"

    # Step 2: Create or find SequencingBatch
    existing = await db.execute(
        select(SequencingBatch).where(
            SequencingBatch.code == batch_number,
            SequencingBatch.organization_id == org_id,
        )
    )
    seq_batch = existing.scalar_one_or_none()

    if seq_batch and seq_batch.status == "complete":
        logger.warning(
            "Duplicate manifest for completed batch %s (org %d)",
            batch_number,
            org_id,
        )
        return seq_batch

    if not seq_batch:
        seq_batch = SequencingBatch(
            organization_id=org_id,
            name=f"Sequencing batch {batch_number}",
            code=batch_number,
            status="ingesting",
            expected_file_count=len(parse_result.entries),
        )
        db.add(seq_batch)
        await db.flush()
    else:
        seq_batch.status = "ingesting"
        seq_batch.expected_file_count = len(parse_result.entries)
        await db.flush()

    # Step 3 & 4: Resolve samples and create ManifestEntry records
    profiles = await NamingProfileService.list_profiles(db, org_id, status_filter="active")

    for entry in parse_result.entries:
        # Resolve via naming profiles
        resolved_sample_id = None
        resolved_experiment_id = None
        resolved_project_id = None

        match_result = match_filename(entry.filename, profiles)
        if match_result.status == "matched" and match_result.parse_result:
            parse = match_result.parse_result
            profile = await NamingProfileService.get_profile(db, parse.profile_id) if parse.profile_id else None
            if profile:
                resolution = await resolve_entities(parse, profile, org_id, db)
                resolved_sample_id = resolution.sample_id
                resolved_experiment_id = resolution.experiment_id
                resolved_project_id = resolution.project_id

        manifest_entry = ManifestEntry(
            sequencing_batch_id=seq_batch.id,
            expected_filename=entry.filename,
            expected_md5=entry.md5,
            resolved_sample_id=resolved_sample_id,
            resolved_experiment_id=resolved_experiment_id,
            resolved_project_id=resolved_project_id,
            status="pending",
        )
        db.add(manifest_entry)

        # Set sample.sequencing_batch_id if resolved and not already set
        if resolved_sample_id:
            from app.models.sample import Sample

            sample_result = await db.execute(select(Sample).where(Sample.id == resolved_sample_id))
            sample = sample_result.scalar_one_or_none()
            if sample and not sample.sequencing_batch_id:
                sample.sequencing_batch_id = seq_batch.id

    await db.flush()

    # Step 5: Batch status stays 'ingesting' since all entries are 'pending'
    # File verification happens separately (Chunk 5 retry mechanism or inline)

    return seq_batch


def is_manifest_filename(filename: str, config_filename: str = "md5.txt") -> bool:
    """Check if a filename matches the configured manifest filename."""
    return filename.strip().lower() == config_filename.strip().lower()


async def read_manifest_config(db: AsyncSession) -> dict[str, str]:
    """Read manifest-related config keys from platform_config."""
    keys = [
        "manifest_filename",
        "manifest_format",
        "manifest_retry_interval_minutes",
        "manifest_max_retries",
    ]
    rows = (
        await db.execute(text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys))
    ).fetchall()
    config = {r[0]: r[1] for r in rows}
    return {
        "manifest_filename": config.get("manifest_filename", "md5.txt"),
        "manifest_format": config.get("manifest_format", "md5sum"),
        "manifest_retry_interval_minutes": int(config.get("manifest_retry_interval_minutes", "15")),
        "manifest_max_retries": int(config.get("manifest_max_retries", "48")),
    }
