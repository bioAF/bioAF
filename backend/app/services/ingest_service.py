"""Auto-ingest service for cataloging files and auto-creating entities.

Processes file arrival events (from GCS Pub/Sub or local simulate),
parses filenames against naming profiles, resolves or auto-creates
entities, and catalogs files in the database.
"""

import asyncio
import logging
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.file_parse_result import FileParseResult
from app.models.ingest_event import IngestEvent
from app.models.project import Project
from app.models.sample import Sample
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import (
    DUPLICATE_FILE,
    FILES_CATALOGED,
    UNCLAIMED_ENTITY,
    UNMATCHED_FILE,
)
from app.services.file_type_utils import detect_file_type
from app.services.naming_profile_parser import match_filename, resolve_entities
from app.services.naming_profile_service import NamingProfileService

logger = logging.getLogger("bioaf.ingest_service")


async def check_duplicate(md5_checksum: str | None, org_id: int, db: AsyncSession) -> File | None:
    """Check if a file with the same MD5 already exists."""
    if not md5_checksum:
        return None
    result = await db.execute(select(File).where(File.md5_checksum == md5_checksum, File.organization_id == org_id))
    return result.scalar_one_or_none()


async def resolve_or_create_project(
    project_code: str | None,
    project_id: int | None,
    org_id: int,
    user_id: int | None,
    db: AsyncSession,
) -> int | None:
    """Resolve an existing project or auto-create an unclaimed one."""
    if project_id:
        return project_id
    if not project_code:
        return None

    # Try to find by name
    result = await db.execute(select(Project).where(Project.name == project_code, Project.organization_id == org_id))
    existing = result.scalar_one_or_none()
    if existing:
        return existing.id

    # Auto-create unclaimed project
    project = Project(
        organization_id=org_id,
        name=project_code,
        status="active",
        is_unclaimed=True,
        owner_user_id=None,
    )
    db.add(project)
    await db.flush()

    await log_action(
        db,
        user_id=user_id,
        entity_type="project",
        entity_id=project.id,
        action="auto_create",
        details={"name": project_code, "is_unclaimed": True, "source": "auto_ingest"},
    )
    return project.id


async def resolve_or_create_experiment(
    experiment_code: str | None,
    experiment_id: int | None,
    project_id: int | None,
    org_id: int,
    user_id: int | None,
    db: AsyncSession,
) -> int | None:
    """Resolve an existing experiment or auto-create an unclaimed one."""
    if experiment_id:
        return experiment_id
    if not experiment_code:
        return None

    result = await db.execute(
        select(Experiment).where(Experiment.name == experiment_code, Experiment.organization_id == org_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing.id

    experiment = Experiment(
        organization_id=org_id,
        name=experiment_code,
        project_id=project_id,
        status="registered",
        is_unclaimed=True,
        owner_user_id=None,
    )
    db.add(experiment)
    await db.flush()

    await log_action(
        db,
        user_id=user_id,
        entity_type="experiment",
        entity_id=experiment.id,
        action="auto_create",
        details={"name": experiment_code, "project_id": project_id, "is_unclaimed": True},
    )
    return experiment.id


async def resolve_or_create_sample(
    sample_id_unique: str | None,
    experiment_id: int | None,
    org_id: int,
    user_id: int | None,
    db: AsyncSession,
) -> int | None:
    """Resolve an existing sample or auto-create an unclaimed one."""
    if not sample_id_unique:
        return None
    if not experiment_id:
        return None

    result = await db.execute(
        select(Sample).where(
            Sample.sample_id_unique == sample_id_unique,
            Sample.experiment_id == experiment_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing.id

    sample = Sample(
        experiment_id=experiment_id,
        sample_id_unique=sample_id_unique,
        status="registered",
        is_unclaimed=True,
    )
    db.add(sample)
    await db.flush()

    await log_action(
        db,
        user_id=user_id,
        entity_type="sample",
        entity_id=sample.id,
        action="auto_create",
        details={"sample_id_unique": sample_id_unique, "experiment_id": experiment_id, "is_unclaimed": True},
    )
    return sample.id


async def copy_to_raw_bucket(
    source_bucket: str,
    source_path: str,
    raw_bucket: str,
    destination_prefix: str,
    filename: str,
    credentials=None,
) -> str:
    """Copy a file from the ingest bucket to the raw bucket.

    Returns the destination GCS URI.
    """
    from app.services.gcs_storage import GcsStorageService

    source_uri = f"gs://{source_bucket}/{source_path}"
    destination_uri = f"gs://{raw_bucket}/{destination_prefix}{filename}"

    await GcsStorageService.move_file(source_uri, destination_uri, credentials=credentials)
    return destination_uri


async def cleanup_ingest_file(
    source_bucket: str,
    source_path: str,
    policy: str,
    credentials=None,
) -> None:
    """Apply the cleanup policy to the ingest bucket file.

    With delete_after_copy, delete the object immediately.
    With retain_* policies, leave it in place.
    """
    if policy == "delete_after_copy":
        from google.cloud import storage

        client = storage.Client(credentials=credentials)
        bucket = client.get_bucket(source_bucket)
        blob = bucket.blob(source_path)
        blob.delete()
        logger.info("Deleted ingest file gs://%s/%s", source_bucket, source_path)
    else:
        logger.info(
            "Retaining ingest file gs://%s/%s (policy=%s)",
            source_bucket,
            source_path,
            policy,
        )


async def process_ingest_event(
    filename: str,
    source_bucket: str,
    source_path: str,
    org_id: int,
    db: AsyncSession,
    user_id: int | None = None,
    file_size_bytes: int | None = None,
    content_md5: str | None = None,
    ingest_source: str = "simulate",
    credentials=None,
) -> IngestEvent:
    """Main ingest pipeline orchestrator.

    Steps:
    1. Parse filename against active naming profiles
    2. Resolve entities (project, experiment, sample)
    3. Duplicate detection
    4. File type detection
    5. Create file record
    6. Create file_parse_result
    7. Create ingest_event record
    8. Update experiment status if applicable
    9. Emit events for pipeline triggers
    """
    auto_created = {"projects": [], "experiments": [], "samples": []}

    # Step 1: Parse filename
    profiles = await NamingProfileService.list_profiles(db, org_id, status_filter="active")
    match_result = match_filename(filename, profiles)

    parsed_project_code = None
    parsed_experiment_code = None
    parsed_sample_id = None
    naming_profile_id = None
    resolved_project_id = None
    resolved_experiment_id = None
    resolved_sample_id = None

    if match_result.status == "matched" and match_result.parse_result:
        parse = match_result.parse_result
        naming_profile_id = parse.profile_id
        parsed_project_code = parse.segments.get("project_code")
        parsed_experiment_code = parse.segments.get("experiment_code")
        parsed_sample_id = parse.segments.get("sample_id")

        # Step 2: Resolve entities
        profile = await NamingProfileService.get_profile(db, naming_profile_id) if naming_profile_id else None
        if profile:
            resolution = await resolve_entities(parse, profile, org_id, db)

            # Track original IDs before auto-creation
            orig_project_id = resolution.project_id
            orig_experiment_id = resolution.experiment_id
            orig_sample_id = resolution.sample_id

            resolved_project_id = await resolve_or_create_project(
                parsed_project_code, resolution.project_id, org_id, user_id, db
            )
            if resolved_project_id and not orig_project_id:
                auto_created["projects"].append(resolved_project_id)

            resolved_experiment_id = await resolve_or_create_experiment(
                parsed_experiment_code, resolution.experiment_id, resolved_project_id, org_id, user_id, db
            )
            if resolved_experiment_id and not orig_experiment_id:
                auto_created["experiments"].append(resolved_experiment_id)

            resolved_sample_id = await resolve_or_create_sample(
                parsed_sample_id, resolved_experiment_id, org_id, user_id, db
            )
            if resolved_sample_id and not orig_sample_id:
                auto_created["samples"].append(resolved_sample_id)

    # Step 3: Duplicate detection
    existing_file = await check_duplicate(content_md5, org_id, db)
    if existing_file:
        event = IngestEvent(
            file_id=existing_file.id,
            source_bucket=source_bucket,
            source_path=source_path,
            naming_profile_id=naming_profile_id,
            parsed_project_code=parsed_project_code,
            parsed_experiment_code=parsed_experiment_code,
            parsed_sample_id=parsed_sample_id,
            resolved_project_id=resolved_project_id,
            resolved_experiment_id=resolved_experiment_id,
            resolved_sample_id=resolved_sample_id,
            auto_created_entities=auto_created,
            ingest_status="duplicate",
        )
        db.add(event)
        await db.flush()
        asyncio.create_task(
            event_bus.emit(
                DUPLICATE_FILE,
                {
                    "event_type": DUPLICATE_FILE,
                    "org_id": org_id,
                    "entity_type": "file",
                    "entity_id": existing_file.id,
                    "title": f"Duplicate file detected: {filename}",
                    "message": f"File '{filename}' matches existing file #{existing_file.id}",
                    "severity": "info",
                },
            )
        )
        return event

    # Step 4: File type detection
    file_type = detect_file_type(filename)

    # Step 5: Create file record
    # Parse date and version from segments if available
    file_date = None
    file_version = None
    if match_result.status == "matched" and match_result.parse_result:
        file_version = match_result.parse_result.segments.get("version")

    gcs_uri = f"gs://{source_bucket}/{source_path}"
    file_record = File(
        organization_id=org_id,
        gcs_uri=gcs_uri,
        filename=filename,
        size_bytes=file_size_bytes,
        md5_checksum=content_md5,
        file_type=file_type,
        project_id=resolved_project_id,
        ingest_source=ingest_source,
        version=file_version,
        file_date=file_date,
        uploader_user_id=user_id,
    )
    db.add(file_record)
    await db.flush()

    # Step 5b: ManifestEntry reconciliation (before copy, so resolution
    # from the manifest can determine the correct experiment prefix)
    from datetime import datetime, timezone

    from app.models.manifest_entry import ManifestEntry
    from app.models.sequencing_batch import SequencingBatch

    manifest_entry_result = await db.execute(
        select(ManifestEntry)
        .where(
            ManifestEntry.expected_filename == filename,
            ManifestEntry.status == "pending",
        )
        .limit(1)
    )
    manifest_entry = manifest_entry_result.scalar_one_or_none()
    if manifest_entry:
        manifest_entry.file_id = file_record.id
        manifest_entry.last_check_at = datetime.now(timezone.utc)

        if content_md5 and manifest_entry.expected_md5:
            if content_md5 == manifest_entry.expected_md5:
                manifest_entry.status = "verified"
            else:
                manifest_entry.status = "checksum_mismatch"
                manifest_entry.error_message = f"Expected {manifest_entry.expected_md5}, got {content_md5}"
        else:
            manifest_entry.status = "verified"

        # Enhance resolution from manifest entry if naming profile didn't resolve
        if manifest_entry.resolved_sample_id and not resolved_sample_id:
            resolved_sample_id = manifest_entry.resolved_sample_id
        if manifest_entry.resolved_experiment_id and not resolved_experiment_id:
            resolved_experiment_id = manifest_entry.resolved_experiment_id

        # If we have a sample but still no experiment, derive from sample
        if resolved_sample_id and not resolved_experiment_id:
            sample_result = await db.execute(select(Sample).where(Sample.id == resolved_sample_id))
            sample_obj = sample_result.scalar_one_or_none()
            if sample_obj:
                resolved_experiment_id = sample_obj.experiment_id

        # Link file to sample via junction table
        if resolved_sample_id:
            from app.models.sample import sample_files

            existing_link = await db.execute(
                sample_files.select().where(
                    sample_files.c.sample_id == resolved_sample_id,
                    sample_files.c.file_id == file_record.id,
                )
            )
            if not existing_link.fetchone():
                await db.execute(sample_files.insert().values(sample_id=resolved_sample_id, file_id=file_record.id))

        # Link file to experiment and sequencing batch
        if resolved_experiment_id and not file_record.experiment_id:
            file_record.experiment_id = resolved_experiment_id
        file_record.sequencing_batch_id = manifest_entry.sequencing_batch_id

        # Increment batch ingested_file_count
        batch_result = await db.execute(
            select(SequencingBatch).where(SequencingBatch.id == manifest_entry.sequencing_batch_id)
        )
        seq_batch = batch_result.scalar_one_or_none()
        if seq_batch:
            seq_batch.ingested_file_count = (seq_batch.ingested_file_count or 0) + 1

        await db.flush()

    # Step 5c: Copy file to raw bucket (real GCS only)
    if ingest_source == "auto_ingest":
        config = await _read_ingest_config(db)
        raw_bucket = config.get("raw_bucket_name", "")
        if raw_bucket and raw_bucket != "null":
            if resolved_experiment_id:
                from app.services.gcs_storage import GcsStorageService

                prefix = GcsStorageService.build_experiment_prefix(resolved_experiment_id)
            else:
                from app.services.gcs_storage import GcsStorageService

                prefix = GcsStorageService.build_unlinked_prefix()

            new_uri = await copy_to_raw_bucket(
                source_bucket,
                source_path,
                raw_bucket,
                prefix,
                filename,
                credentials=credentials,
            )
            file_record.gcs_uri = new_uri
            await db.flush()

            # move_file already deletes the source after copy+verify.
            # Only call cleanup for retain policies (which skip deletion).
            cleanup_policy = config.get("ingest_cleanup_policy", "delete_after_copy")
            if cleanup_policy != "delete_after_copy":
                await cleanup_ingest_file(source_bucket, source_path, policy=cleanup_policy, credentials=credentials)

    # Step 6: Create file_parse_result
    parse_result_record = FileParseResult(
        file_id=file_record.id,
        naming_profile_id=naming_profile_id,
        parsed_segments_json=match_result.parse_result.segments if match_result.parse_result else None,
        match_status=match_result.status,
        auto_linked=match_result.status == "matched",
    )
    db.add(parse_result_record)
    await db.flush()

    # Step 7: Determine ingest status
    if match_result.status == "matched":
        ingest_status = "cataloged"
    elif match_result.status == "multiple_matches":
        ingest_status = "multiple_matches"
    else:
        ingest_status = "unmatched"

    # Step 7: Create ingest event
    event = IngestEvent(
        file_id=file_record.id,
        source_bucket=source_bucket,
        source_path=source_path,
        naming_profile_id=naming_profile_id,
        parsed_project_code=parsed_project_code,
        parsed_experiment_code=parsed_experiment_code,
        parsed_sample_id=parsed_sample_id,
        resolved_project_id=resolved_project_id,
        resolved_experiment_id=resolved_experiment_id,
        resolved_sample_id=resolved_sample_id,
        auto_created_entities=auto_created,
        ingest_status=ingest_status,
    )
    db.add(event)
    await db.flush()

    # Step 8: Update experiment status if first FASTQ
    if resolved_experiment_id and file_type == "fastq":
        exp_result = await db.execute(select(Experiment).where(Experiment.id == resolved_experiment_id))
        experiment = exp_result.scalar_one_or_none()
        if experiment and experiment.status == "registered":
            experiment.status = "fastq_uploaded"
            await db.flush()

    # Step 9: Emit events for notifications and pipeline triggers
    if ingest_status == "cataloged":
        asyncio.create_task(
            event_bus.emit(
                FILES_CATALOGED,
                {
                    "event_type": FILES_CATALOGED,
                    "org_id": org_id,
                    "entity_type": "ingest_event",
                    "entity_id": event.id,
                    "title": f"File cataloged: {filename}",
                    "message": f"File '{filename}' successfully cataloged as {file_type}",
                    "severity": "info",
                    "metadata": {"file_id": file_record.id, "file_type": file_type},
                },
            )
        )
    elif ingest_status == "unmatched":
        asyncio.create_task(
            event_bus.emit(
                UNMATCHED_FILE,
                {
                    "event_type": UNMATCHED_FILE,
                    "org_id": org_id,
                    "entity_type": "file",
                    "entity_id": file_record.id,
                    "title": f"Unmatched file: {filename}",
                    "message": f"File '{filename}' did not match any naming profile",
                    "severity": "warning",
                },
            )
        )

    # Emit unclaimed entity notifications
    if auto_created["projects"] or auto_created["experiments"] or auto_created["samples"]:
        asyncio.create_task(
            event_bus.emit(
                UNCLAIMED_ENTITY,
                {
                    "event_type": UNCLAIMED_ENTITY,
                    "org_id": org_id,
                    "entity_type": "ingest_event",
                    "entity_id": event.id,
                    "title": "Unclaimed entities created",
                    "message": f"Auto-created: {len(auto_created['projects'])} projects, "
                    f"{len(auto_created['experiments'])} experiments, "
                    f"{len(auto_created['samples'])} samples",
                    "severity": "warning",
                    "metadata": auto_created,
                },
            )
        )

    await log_action(
        db,
        user_id=user_id,
        entity_type="file",
        entity_id=file_record.id,
        action="auto_ingest",
        details={
            "filename": filename,
            "ingest_status": ingest_status,
            "file_type": file_type,
            "auto_created": auto_created,
        },
    )

    # Step 10: Evaluate pipeline triggers for cataloged files
    if ingest_status == "cataloged" and event.file_id:
        try:
            from app.services.trigger_service import TriggerService

            await TriggerService.evaluate_event_triggers(event, db)
        except Exception:
            logger.exception("Trigger evaluation failed for event %d", event.id)

    return event


async def _read_ingest_config(db: AsyncSession) -> dict[str, str]:
    """Read ingest-related config from platform_config."""
    keys = [
        "raw_bucket_name",
        "ingest_cleanup_policy",
        "storage_deployed",
    ]
    rows = (
        await db.execute(text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys))
    ).fetchall()
    return {r[0]: r[1] for r in rows}


async def get_unclaimed_entities(org_id: int, db: AsyncSession) -> list[dict]:
    """Return all unclaimed entities (projects, experiments, samples)."""
    unclaimed = []

    projects = await db.execute(
        select(Project).where(Project.organization_id == org_id, Project.is_unclaimed.is_(True))
    )
    for p in projects.scalars().all():
        unclaimed.append(
            {
                "entity_type": "project",
                "entity_id": p.id,
                "name": p.name,
                "created_at": p.created_at,
            }
        )

    experiments = await db.execute(
        select(Experiment).where(Experiment.organization_id == org_id, Experiment.is_unclaimed.is_(True))
    )
    for e in experiments.scalars().all():
        unclaimed.append(
            {
                "entity_type": "experiment",
                "entity_id": e.id,
                "name": e.name,
                "created_at": e.created_at,
            }
        )

    samples = await db.execute(select(Sample).where(Sample.is_unclaimed.is_(True)))
    for s in samples.scalars().all():
        unclaimed.append(
            {
                "entity_type": "sample",
                "entity_id": s.id,
                "name": s.sample_id_unique or f"Sample {s.id}",
                "created_at": s.created_at,
            }
        )

    return unclaimed
