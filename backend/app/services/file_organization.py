"""File Organization Service.

Handles experiment-based file organization within GCS buckets.
Files are organized under experiments/{id}/ prefixes or unlinked/.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit_service import log_action
from app.services.gcs_storage import GcsStorageService

logger = logging.getLogger("bioaf.file_organization")


class FileOrganizationService:
    """Manage file-to-experiment assignments with GCS moves."""

    @staticmethod
    async def _get_raw_bucket(session: AsyncSession) -> str | None:
        """Read raw_bucket_name from platform_config."""
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'raw_bucket_name'"))
        val = result.scalar_one_or_none()
        if val and val != "null":
            return val
        return None

    @staticmethod
    async def assign_file_to_experiment(
        session: AsyncSession,
        file_id: int,
        experiment_id: int,
        user_id: int,
    ) -> None:
        """Assign (or reassign) a file to an experiment.

        If the file is already assigned to another experiment, this acts
        as a reassignment, moving the file between experiment prefixes.
        """
        row = (
            await session.execute(
                text("SELECT gcs_uri, experiment_id, filename FROM files WHERE id = :fid").bindparams(fid=file_id)
            )
        ).fetchone()

        if not row:
            raise ValueError(f"File {file_id} not found")

        old_uri, current_exp_id, filename = row[0], row[1], row[2]

        if current_exp_id is not None and current_exp_id != experiment_id:
            # Already assigned to a different experiment - treat as reassign
            await FileOrganizationService.reassign_file_to_experiment(session, file_id, experiment_id, user_id)
            return

        # Use raw bucket as destination; fall back to source bucket
        raw_bucket = await FileOrganizationService._get_raw_bucket(session)
        source_bucket, _ = _parse_gcs_uri(old_uri)
        dest_bucket = raw_bucket or source_bucket
        new_prefix = GcsStorageService.build_experiment_prefix(experiment_id)
        new_uri = f"gs://{dest_bucket}/{new_prefix}{filename}"

        # Move file in GCS if URIs differ
        if old_uri != new_uri:
            credentials = await GcsStorageService.get_credentials(session)
            new_uri = await GcsStorageService.move_file(old_uri, new_uri, credentials=credentials)

        # Update DB
        await session.execute(
            text("UPDATE files SET experiment_id = :exp_id, gcs_uri = :uri WHERE id = :fid").bindparams(
                exp_id=experiment_id, uri=new_uri, fid=file_id
            )
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file_id,
            action="assigned_to_experiment",
            details={
                "experiment_id": experiment_id,
                "old_uri": old_uri,
                "new_uri": new_uri,
            },
        )
        await session.commit()

    @staticmethod
    async def reassign_file_to_experiment(
        session: AsyncSession,
        file_id: int,
        new_experiment_id: int,
        user_id: int,
    ) -> None:
        """Move a file from one experiment to another."""
        row = (
            await session.execute(
                text("SELECT gcs_uri, experiment_id, filename FROM files WHERE id = :fid").bindparams(fid=file_id)
            )
        ).fetchone()

        if not row:
            raise ValueError(f"File {file_id} not found")

        old_uri, old_exp_id, filename = row[0], row[1], row[2]

        # Use raw bucket as destination; fall back to source bucket
        raw_bucket = await FileOrganizationService._get_raw_bucket(session)
        source_bucket, _ = _parse_gcs_uri(old_uri)
        dest_bucket = raw_bucket or source_bucket
        new_prefix = GcsStorageService.build_experiment_prefix(new_experiment_id)
        new_uri = f"gs://{dest_bucket}/{new_prefix}{filename}"

        # Move in GCS
        if old_uri != new_uri:
            credentials = await GcsStorageService.get_credentials(session)
            new_uri = await GcsStorageService.move_file(old_uri, new_uri, credentials=credentials)

        # Update DB
        await session.execute(
            text("UPDATE files SET experiment_id = :exp_id, gcs_uri = :uri WHERE id = :fid").bindparams(
                exp_id=new_experiment_id, uri=new_uri, fid=file_id
            )
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file_id,
            action="reassigned_to_experiment",
            details={
                "old_experiment_id": old_exp_id,
                "new_experiment_id": new_experiment_id,
                "old_uri": old_uri,
                "new_uri": new_uri,
            },
        )
        await session.commit()

    @staticmethod
    async def unlink_file_from_experiment(
        session: AsyncSession,
        file_id: int,
        user_id: int,
    ) -> None:
        """Unlink a file from its experiment, moving to unlinked prefix."""
        row = (
            await session.execute(
                text("SELECT gcs_uri, experiment_id, filename FROM files WHERE id = :fid").bindparams(fid=file_id)
            )
        ).fetchone()

        if not row:
            raise ValueError(f"File {file_id} not found")

        old_uri, old_exp_id, filename = row[0], row[1], row[2]

        # Use raw bucket as destination; fall back to source bucket
        raw_bucket = await FileOrganizationService._get_raw_bucket(session)
        source_bucket, _ = _parse_gcs_uri(old_uri)
        dest_bucket = raw_bucket or source_bucket
        unlinked_prefix = GcsStorageService.build_unlinked_prefix()
        new_uri = f"gs://{dest_bucket}/{unlinked_prefix}{filename}"

        # Move in GCS
        if old_uri != new_uri:
            credentials = await GcsStorageService.get_credentials(session)
            new_uri = await GcsStorageService.move_file(old_uri, new_uri, credentials=credentials)

        # Update DB
        await session.execute(
            text("UPDATE files SET experiment_id = NULL, gcs_uri = :uri WHERE id = :fid").bindparams(
                uri=new_uri, fid=file_id
            )
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file_id,
            action="unlinked_from_experiment",
            details={
                "old_experiment_id": old_exp_id,
                "old_uri": old_uri,
                "new_uri": new_uri,
            },
        )
        await session.commit()


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Parse gs://bucket/path into (bucket_name, blob_path)."""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")
