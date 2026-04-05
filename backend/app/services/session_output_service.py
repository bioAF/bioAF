"""Session output registration service (ADR-040).

Registers files discovered in GCS after a notebook/SSH session shuts down.
Creates File records with source_type=notebook_output and links them to the
session via NotebookSessionFile with access_type=output.
"""

from __future__ import annotations

import logging
import re
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


def parse_gsutil_ls_output(raw_output: str) -> list[dict]:
    """Parse gsutil ls -l -r output into a list of {gcs_uri, size_bytes}.

    Each line looks like:
       1234567  2026-04-04T12:00:00Z  gs://bucket/path/to/file.txt
    The final summary line starts with TOTAL: and is skipped.
    """
    files: list[dict] = []
    for line in raw_output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("TOTAL:"):
            continue
        match = re.match(r"^\s*(\d+)\s+\S+\s+(gs://.+)$", line)
        if match:
            size_bytes = int(match.group(1))
            gcs_uri = match.group(2)
            filename = gcs_uri.rsplit("/", 1)[-1] if "/" in gcs_uri else gcs_uri
            if not filename or filename in _EXCLUDED_FILENAMES:
                continue
            if any(filename.startswith(p.rstrip("/")) for p in _EXCLUDED_PREFIXES):
                continue
            files.append({"gcs_uri": gcs_uri, "size_bytes": size_bytes, "filename": filename})
    return files


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
    ) -> int:
        """Register output files from a completed session.

        Creates File records with source_type=notebook_output and
        NotebookSessionFile records with access_type=output.

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
                source_type="notebook_output",
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

        Updates File.gcs_uri for all output files to point to the results bucket.
        Returns the new GCS output prefix in the results bucket.
        """
        from google.cloud import storage
        from sqlalchemy import text as sa_text

        # Load GCS credentials
        config_result = await db.execute(
            sa_text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('gcp_credential_source', 'gcp_service_account_key')"
            )
        )
        config = {r[0]: r[1] for r in config_result.fetchall()}

        from app.services.credential_injector import load_gcp_credentials

        credentials = load_gcp_credentials(config)
        client = storage.Client(credentials=credentials)

        src_prefix = f"sessions/{session_id}/"
        dst_prefix = f"sessions/{session_id}/"

        src_bucket = client.bucket(working_bucket)
        dst_bucket = client.bucket(results_bucket)

        copied = 0
        blobs = list(src_bucket.list_blobs(prefix=src_prefix))
        for blob in blobs:
            dst_name = dst_prefix + blob.name[len(src_prefix) :]
            src_bucket.copy_blob(blob, dst_bucket, new_name=dst_name)
            copied += 1

        # Update File.gcs_uri to point to results bucket
        if copied:
            old_uri_prefix = f"gs://{working_bucket}/{src_prefix}"
            new_uri_prefix = f"gs://{results_bucket}/{dst_prefix}"
            await db.execute(
                sa_text(
                    "UPDATE files SET gcs_uri = REPLACE(gcs_uri, :old, :new) "
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
        for blob in blobs:
            blob.delete()

        logger.info(
            "Moved %d output files for session %d from %s to %s",
            copied,
            session_id,
            working_bucket,
            results_bucket,
        )

        return f"gs://{results_bucket}/{dst_prefix}"
