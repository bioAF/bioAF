"""Manifest-driven ingest service.

Processes manifest files (md5.txt or CSV) to create SequencingBatch and
ManifestEntry records, resolve filenames to samples via naming profiles,
and prepare for file verification.

Also provides the shared reconcile_manifest_entry() helper used by both
the retroactive path (manifest arrives after files) and the forward path
(files arrive after manifest, in ingest_service.py step 5b).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.manifest_parser import parse_manifest
from app.services.naming_profile_parser import match_filename, resolve_entities
from app.services.naming_profile_service import NamingProfileService

logger = logging.getLogger("bioaf.manifest_ingest")

# Window for retroactive reconciliation: files created within this many
# hours of the manifest processing are eligible for matching.
RETROACTIVE_WINDOW_HOURS = 2


async def reconcile_manifest_entry(
    db: AsyncSession,
    entry: ManifestEntry,
    file_record: File,
    content_md5: str | None,
) -> None:
    """Link a file to a manifest entry and perform all side-effect updates.

    Shared by the forward path (ingest_service.py step 5b) and the
    retroactive path (below). Caller must flush/commit after.
    """
    from app.models.sample import sample_files

    entry.file_id = file_record.id
    entry.last_check_at = datetime.now(timezone.utc)

    # MD5 comparison
    if content_md5 and entry.expected_md5:
        if content_md5 == entry.expected_md5:
            entry.status = "verified"
        else:
            entry.status = "checksum_mismatch"
            entry.error_message = f"Expected {entry.expected_md5}, got {content_md5}"
    else:
        entry.status = "verified"

    # Link file to sample via junction table
    if entry.resolved_sample_id:
        existing_link = await db.execute(
            sample_files.select().where(
                sample_files.c.sample_id == entry.resolved_sample_id,
                sample_files.c.file_id == file_record.id,
            )
        )
        if not existing_link.fetchone():
            await db.execute(
                sample_files.insert().values(
                    sample_id=entry.resolved_sample_id,
                    file_id=file_record.id,
                )
            )

    # Set file.experiment_id and file.sequencing_batch_id
    if entry.resolved_experiment_id and not file_record.experiment_id:
        file_record.experiment_id = entry.resolved_experiment_id
    file_record.sequencing_batch_id = entry.sequencing_batch_id

    # Increment batch ingested_file_count
    batch_result = await db.execute(
        select(SequencingBatch).where(
            SequencingBatch.id == entry.sequencing_batch_id,
        )
    )
    seq_batch = batch_result.scalar_one_or_none()
    if seq_batch:
        seq_batch.ingested_file_count = (seq_batch.ingested_file_count or 0) + 1

    await db.flush()

    # Auto-run trigger evaluation
    if entry.resolved_sample_id:
        try:
            from app.services.auto_run_service import AutoRunService

            if entry.status == "verified":
                await AutoRunService.check_and_queue_auto_runs(
                    db,
                    sample_id=entry.resolved_sample_id,
                    sequencing_batch_id=entry.sequencing_batch_id,
                )
            elif entry.status == "checksum_mismatch":
                await AutoRunService.cancel_pending_runs_for_sample(
                    db,
                    sample_id=entry.resolved_sample_id,
                    reason="checksum_mismatch",
                )
            await db.flush()
        except Exception:
            logger.exception(
                "Auto-run evaluation failed for manifest entry %d",
                entry.id,
            )


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
    3. Content-aware redelivery guard
    4. Resolve samples from filenames
    5. Create ManifestEntry records (all start as 'pending')
    6. Retroactive reconciliation against already-ingested files

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

    # Step 3: Content-aware redelivery guard
    incoming_set = {(e.filename, e.md5) for e in parse_result.entries}

    if seq_batch:
        existing_entries_result = await db.execute(
            select(ManifestEntry).where(
                ManifestEntry.sequencing_batch_id == seq_batch.id,
            )
        )
        existing_entries = list(existing_entries_result.scalars().all())

        if existing_entries:
            existing_set = {(e.expected_filename, e.expected_md5) for e in existing_entries}

            if existing_set == incoming_set:
                # True redelivery: exact same content. Run retroactive
                # reconciliation in case files arrived since last processing.
                logger.info(
                    "Manifest redelivery for batch %s (org %d), running retroactive reconciliation only",
                    batch_number,
                    org_id,
                )
                await _retroactive_reconcile(db, existing_entries, org_id)
                return seq_batch

            # Updated manifest: add missing entries, preserve verified ones.
            # Delete pending entries that are no longer in the manifest.
            for entry in existing_entries:
                key = (entry.expected_filename, entry.expected_md5)
                if key not in incoming_set and entry.status == "pending":
                    await db.delete(entry)

            # Filter incoming to only entries that don't already exist
            already_present = {
                (e.expected_filename, e.expected_md5)
                for e in existing_entries
                if (e.expected_filename, e.expected_md5) in incoming_set
            }
            entries_to_create = [e for e in parse_result.entries if (e.filename, e.md5) not in already_present]
            await db.flush()

            if not entries_to_create:
                logger.info(
                    "Manifest for batch %s (org %d): all entries already exist",
                    batch_number,
                    org_id,
                )
                await _retroactive_reconcile(db, existing_entries, org_id)
                return seq_batch

            logger.info(
                "Updated manifest for batch %s (org %d): adding %d new entries",
                batch_number,
                org_id,
                len(entries_to_create),
            )
        else:
            entries_to_create = parse_result.entries
    else:
        entries_to_create = parse_result.entries

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

    # Step 4 & 5: Resolve samples and create ManifestEntry records
    profiles = await NamingProfileService.list_profiles(db, org_id, status_filter="active")

    new_entries: list[ManifestEntry] = []

    for entry in entries_to_create:
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

            # If naming profile extracted a sample_index but didn't resolve
            # a sample (no sample_id segment), try batch-position lookup
            sample_index_str = parse.segments.get("sample_index")
            if sample_index_str and not resolved_sample_id:
                from app.services.sample_service import SampleService

                sample = await SampleService.resolve_by_batch_position(db, seq_batch.id, int(sample_index_str))
                if sample:
                    resolved_sample_id = sample.id
                    resolved_experiment_id = resolved_experiment_id or sample.experiment_id

        # If we resolved a sample but not its experiment, derive from sample
        if resolved_sample_id and not resolved_experiment_id:
            from app.models.sample import Sample

            sample_result = await db.execute(select(Sample).where(Sample.id == resolved_sample_id))
            sample = sample_result.scalar_one_or_none()
            if sample:
                resolved_experiment_id = sample.experiment_id

        # If we have an experiment but not a project, derive from experiment
        if resolved_experiment_id and not resolved_project_id:
            exp_result = await db.execute(select(Experiment).where(Experiment.id == resolved_experiment_id))
            exp = exp_result.scalar_one_or_none()
            if exp:
                resolved_project_id = exp.project_id

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
        new_entries.append(manifest_entry)

        # Set sample.sequencing_batch_id if resolved and not already set
        if resolved_sample_id:
            from app.models.sample import Sample

            sample_result = await db.execute(select(Sample).where(Sample.id == resolved_sample_id))
            sample = sample_result.scalar_one_or_none()
            if sample and not sample.sequencing_batch_id:
                sample.sequencing_batch_id = seq_batch.id

    await db.flush()

    # Step 6: Retroactive reconciliation
    # Check if any of the newly created entries match already-ingested files.
    all_entries = new_entries
    if seq_batch.id:
        # Include any pre-existing pending entries that survived the guard
        existing_pending_result = await db.execute(
            select(ManifestEntry).where(
                ManifestEntry.sequencing_batch_id == seq_batch.id,
                ManifestEntry.status == "pending",
            )
        )
        all_entries = list(existing_pending_result.scalars().all())

    await _retroactive_reconcile(db, all_entries, org_id)

    return seq_batch


async def _retroactive_reconcile(
    db: AsyncSession,
    entries: list[ManifestEntry],
    org_id: int,
) -> None:
    """Try to match pending manifest entries against already-ingested files.

    For each pending entry, search for a File record where:
    - md5_checksum matches expected_md5 (primary key)
    - filename matches expected_filename (verification)
    - organization_id matches
    - created_at is within RETROACTIVE_WINDOW_HOURS of now
    - file is not already linked to another ManifestEntry
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=RETROACTIVE_WINDOW_HOURS)

    pending = [e for e in entries if e.status == "pending"]
    if not pending:
        return

    # Collect file_ids already claimed by any ManifestEntry
    claimed_result = await db.execute(
        select(ManifestEntry.file_id).where(
            ManifestEntry.file_id.isnot(None),
        )
    )
    claimed_file_ids = {row[0] for row in claimed_result.fetchall()}

    for entry in pending:
        if not entry.expected_md5:
            continue

        # Primary match: MD5 + filename + org + time window
        file_result = await db.execute(
            select(File).where(
                File.md5_checksum == entry.expected_md5,
                File.filename == entry.expected_filename,
                File.organization_id == org_id,
                File.created_at >= cutoff,
            )
        )
        candidate = file_result.scalar_one_or_none()

        if candidate and candidate.id not in claimed_file_ids:
            logger.info(
                "Retroactive reconciliation: matched file %d (%s) to manifest entry %d (batch %s)",
                candidate.id,
                candidate.filename,
                entry.id,
                entry.sequencing_batch_id,
            )
            await reconcile_manifest_entry(db, entry, candidate, candidate.md5_checksum)
            claimed_file_ids.add(candidate.id)


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
