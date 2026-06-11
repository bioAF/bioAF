"""Session output registration service (ADR-040).

Registers files discovered in GCS after a notebook/SSH session shuts down.
Creates File records with source_type=notebook_output and links them to the
session via NotebookSessionFile with access_type=output.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.session_outputs")

# System/hidden files to skip during output registration
_EXCLUDED_FILENAMES = {
    ".bash_history",
    ".Rhistory",
    ".bash_logout",
    ".bashrc",
    ".profile",
    ".gitconfig",
    ".DS_Store",
}
_EXCLUDED_PREFIXES = (".git/", "__pycache__/", ".ipynb_checkpoints/", ".cache/", ".local/")


def _file_type_from_extension(filename: str) -> str:
    """Derive file_type from filename extension."""
    if filename.lower().endswith(".fastq.gz"):
        return "fastq"
    parts = filename.rsplit(".", 1)
    if len(parts) < 2:
        return "unknown"
    return parts[1].lower()


class SessionOutputService:
    @staticmethod
    async def register_outputs(
        session: AsyncSession,
        session_id: int,
        organization_id: int,
        project_id: int | None,
        experiment_id: int | None,
        user_id: int,
        gcs_files: list[dict],
        source_type: str = "notebook_output",
    ) -> int:
        """Register output files from a completed session.

        Creates File records and NotebookSessionFile records with
        access_type=output.

        Returns the number of files registered.
        """
        from app.models.file import File
        from app.models.notebook_session_file import NotebookSessionFile

        registered = 0
        for f in gcs_files:
            filename = f["filename"]
            # Skip excluded files
            if filename in _EXCLUDED_FILENAMES or filename.startswith("."):
                if filename in _EXCLUDED_FILENAMES:
                    continue
                base = filename.lstrip(".")
                if not base or "." not in base:
                    continue
            if any(filename.startswith(p.rstrip("/")) for p in _EXCLUDED_PREFIXES):
                continue

            file_record = File(
                organization_id=organization_id,
                gcs_uri=f["gcs_uri"],
                filename=filename,
                size_bytes=f.get("size_bytes"),
                file_type=_file_type_from_extension(filename),
                experiment_id=experiment_id,
                project_id=project_id,
                source_type=source_type,
                source_notebook_session_id=session_id,
                uploader_user_id=user_id,
            )
            session.add(file_record)
            await session.flush()

            session.add(
                NotebookSessionFile(
                    session_id=session_id,
                    file_id=file_record.id,
                    access_type="output",
                )
            )
            registered += 1

        if registered:
            logger.info("Registered %d output files for session %d", registered, session_id)

        return registered

    @staticmethod
    async def move_outputs_to_results_bucket(
        db: AsyncSession,
        session_id: int,
        working_bucket: str,
        results_bucket: str,
    ) -> str:
        """Copy session outputs from working to results bucket, then delete from working.

        Updates File.storage_uri for all output files to point to the results bucket.
        Returns the new GCS output prefix in the results bucket.
        """
        from sqlalchemy import text as sa_text

        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()

        src_prefix = f"sessions/{session_id}/"
        dst_prefix = f"sessions/{session_id}/"
        src_uri_prefix = adapter.build_uri(working_bucket, src_prefix)

        copied = 0
        src_uris: list[str] = []
        objs = await adapter.list_objects(src_uri_prefix)
        for obj in objs:
            src_uri = obj.storage_uri
            key = adapter.parse_uri(src_uri)[1]
            dst_name = dst_prefix + key[len(src_prefix) :]
            dst_uri = adapter.build_uri(results_bucket, dst_name)
            await adapter.copy(src_uri, dst_uri)
            src_uris.append(src_uri)
            copied += 1

        # Update File.gcs_uri to point to results bucket
        if copied:
            old_uri_prefix = adapter.build_uri(working_bucket, src_prefix)
            new_uri_prefix = adapter.build_uri(results_bucket, dst_prefix)
            await db.execute(
                sa_text(
                    "UPDATE files SET gcs_uri = REPLACE(gcs_uri, :old, :new), storage_uri = REPLACE(gcs_uri, :old, :new) "
                    "WHERE source_notebook_session_id = :sid AND gcs_uri LIKE :pattern"
                ),
                {
                    "old": old_uri_prefix,
                    "new": new_uri_prefix,
                    "sid": session_id,
                    "pattern": f"{old_uri_prefix}%",
                },
            )

        # Delete from working bucket
        for src_uri in src_uris:
            await adapter.delete(src_uri)

        logger.info(
            "Moved %d output files for session %d from %s to %s",
            copied,
            session_id,
            working_bucket,
            results_bucket,
        )

        return adapter.build_uri(results_bucket, dst_prefix)
