"""File Organization Service.

Handles experiment-based file organization within the raw store. An organized object's key carries
the File's permanent UUID (approved D1), so two files with one display name never share an object:
``experiments/{experiment_id}/files/{file_uuid}/{filename}`` or ``unlinked/files/{file_uuid}/{filename}``.
The display filename is kept as the last segment and never used as identity.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid as uuid_pkg

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.platform.platform_config_service import PlatformConfigService
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.file_organization")

# One key segment on every backend: S3 and GCS allow 1024 bytes per key, a filesystem 255 per name.
_FILENAME_MAX_BYTES = 255


def validate_object_filename(filename: str) -> str:
    """Return ``filename`` unchanged if it is safe as the last segment of an object key.

    Refused (ValidationError) rather than rewritten: an empty name, ``.``/``..``, a path separator,
    a NUL or other control character, or more than 255 bytes of UTF-8. Anything else, including
    spaces and glob characters, is the scientist's name for the file and is kept exactly.
    """
    if not isinstance(filename, str) or filename in ("", ".", ".."):
        raise ValidationError("A file name is required and cannot be '.' or '..'.")
    if "/" in filename or "\\" in filename:
        raise ValidationError("A file name cannot contain '/' or '\\'.")
    if any(unicodedata.category(c) == "Cc" for c in filename):
        raise ValidationError("A file name cannot contain control characters.")
    if len(filename.encode("utf-8")) > _FILENAME_MAX_BYTES:
        raise ValidationError(f"A file name can be at most {_FILENAME_MAX_BYTES} bytes long.")
    return filename


def organized_key(file_uuid: uuid_pkg.UUID, experiment_id: int | None, filename: str) -> str:
    """The canonical raw-store key for a File: under its experiment, or unlinked, by its UUID."""
    if not isinstance(file_uuid, uuid_pkg.UUID):
        raise TypeError("An organized key needs the File's UUID")
    filename = validate_object_filename(filename)
    if experiment_id is None:
        return f"unlinked/files/{file_uuid}/{filename}"
    return f"experiments/{int(experiment_id)}/files/{file_uuid}/{filename}"


class FileOrganizationService:
    """Manage file-to-experiment assignments with GCS moves."""

    @staticmethod
    async def _get_raw_bucket(session: AsyncSession) -> str | None:
        """Read raw_bucket_name from platform_config."""
        val = await PlatformConfigService.get(session, "raw_bucket_name")
        if val and val != "null":
            return val
        return None

    @staticmethod
    async def planned_location(
        session: AsyncSession,
        source_uri: str,
        *,
        file_uuid: uuid_pkg.UUID,
        experiment_id: int | None,
        filename: str,
    ) -> str:
        """Where a File is organized: its UUID-keyed key (``organized_key``) in the raw bucket, or in
        the source's bucket when no raw bucket is configured. The same File, experiment and name
        always give the same location, so every retry reaches the destination it reserved."""
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        raw_bucket = await FileOrganizationService._get_raw_bucket(session)
        source_bucket, _ = adapter.parse_uri(source_uri)
        dest_bucket = raw_bucket or source_bucket
        return adapter.build_uri(dest_bucket, organized_key(file_uuid, experiment_id, filename))

    @staticmethod
    def _owner_metadata(file_uuid: uuid_pkg.UUID, org_id: int) -> dict[str, str]:
        return {"bioaf-file-uuid": str(file_uuid), "bioaf-organization-id": str(org_id)}

    @staticmethod
    async def _relocate(
        session: AsyncSession,
        *,
        org_id: int,
        file_id: int,
        experiment_id: int | None,
        user_id: int,
        commit: bool,
    ) -> None:
        """Move one of the organization's live Files to its organized place for ``experiment_id``
        (None: unlinked), and record the association, in the caller's transaction.

        The File and the target are checked against the trusted ``org_id`` before any storage call;
        a foreign or missing one is "not found" and changes nothing. The bytes are copied create-only
        to the File's UUID-keyed destination with its server-owned tags; the old object is recorded
        for removal after the commit (``storage_cleanup``), never deleted here. An object already at
        the destination is reused only if it is provably this File's copy (an interrupted earlier move
        to the same target). An object other live records also name is refused: moving it would break
        them, and copying its current bytes would not recover theirs.
        """
        from app.adapters.models import DestinationExists, ObjectChanged, StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter
        from app.exceptions import ConflictError, StateError
        from app.models.experiment import Experiment
        from app.models.file import File
        from app.services import storage_cleanup
        from app.services.entity_references import verify_association_targets
        from app.services.storage_write_admission import STORAGE_PAUSED, StorageWritesPaused, admitted
        from sqlalchemy import func, or_, select

        file = await storage_cleanup.lock_file_for_storage_change(session, file_id, org_id)
        if file is None:
            raise NotFoundError("File not found")
        if experiment_id is not None:
            await verify_association_targets(session, org_id, experiment_id=experiment_id)
        old_uri = file.storage_uri or file.gcs_uri
        old_experiment_id = file.experiment_id
        destination_uri = await FileOrganizationService.planned_location(
            session, old_uri, file_uuid=file.uuid, experiment_id=experiment_id, filename=file.filename
        )
        previous = destination = None
        if destination_uri != old_uri:
            shared = await session.scalar(
                select(func.count(File.id)).where(
                    File.id != file.id,
                    or_(File.storage_uri == old_uri, File.gcs_uri == old_uri),
                    File.deleted_at.is_(None),
                    File.storage_deleted.is_(False),
                )
            )
            if shared:
                logger.warning("File %s shares its stored object with other records; not moved", file.id)
                raise ConflictError(
                    "This file's stored copy is shared with other files, so it cannot be moved. "
                    "An administrator needs to check it."
                )
            adapter = get_storage_adapter()
            owner = FileOrganizationService._owner_metadata(file.uuid, org_id)
            async with admitted(session, error=StorageWritesPaused, message=STORAGE_PAUSED):
                try:
                    previous = await adapter.object_identity(old_uri)
                except StorageObjectNotFound:
                    previous = None
                for _attempt in range(2):
                    try:
                        observed = await adapter.inspect_object(destination_uri)
                    except StorageObjectNotFound:
                        observed = None
                    if observed is None:
                        if previous is None:
                            raise StateError("This file's stored copy could not be found, so it was not moved.")
                        try:
                            destination = await adapter.copy_create_only(
                                old_uri, destination_uri, source_identity=previous, metadata=owner
                            )
                        except DestinationExists:
                            continue
                        except ObjectChanged:
                            raise ConflictError("This file's stored copy changed while it was being moved. Try again.")
                        break
                    tags = observed.metadata or {}
                    same_size = previous is None or observed.identity.size_bytes == previous.size_bytes
                    if all(tags.get(k) == v for k, v in owner.items()) and same_size:
                        destination = observed.identity
                        break
                    raise ConflictError("Something else is already stored where this file would go. Nothing was moved.")
                if destination is None:
                    raise ConflictError("Something else is already stored where this file would go. Nothing was moved.")
            await storage_cleanup.record_relocation(session, file, destination=destination, previous=previous)

        exp_project_id = None
        if experiment_id is not None:
            exp_project_id = await session.scalar(
                select(Experiment.project_id).where(
                    Experiment.id == experiment_id, Experiment.organization_id == org_id
                )
            )
        file.experiment_id = experiment_id
        if exp_project_id is not None:
            file.project_id = exp_project_id
        await session.flush()

        new_uri = file.storage_uri
        if experiment_id is None:
            action, details = "unlinked_from_experiment", {"old_experiment_id": old_experiment_id}
        elif old_experiment_id is not None and old_experiment_id != experiment_id:
            action = "reassigned_to_experiment"
            details = {"old_experiment_id": old_experiment_id, "new_experiment_id": experiment_id}
        else:
            action, details = "assigned_to_experiment", {"experiment_id": experiment_id}
        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file.id,
            action=action,
            details={**details, "old_uri": old_uri, "new_uri": new_uri},
        )
        if commit:
            await session.commit()
            if previous is not None:
                await storage_cleanup.cleanup_after_commit(file.id, org_id)

    @staticmethod
    async def assign_file_to_experiment(
        session: AsyncSession,
        file_id: int,
        experiment_id: int,
        user_id: int,
        *,
        org_id: int,
        commit: bool = True,
    ) -> None:
        """Assign (or reassign) one of ``org_id``'s files to an experiment, moving its bytes to the
        experiment's UUID-keyed place. ``commit=False`` leaves the transaction to the caller; the old
        object is then removed by the cleanup pass after the caller commits."""
        await FileOrganizationService._relocate(
            session, org_id=org_id, file_id=file_id, experiment_id=experiment_id, user_id=user_id, commit=commit
        )

    @staticmethod
    async def reassign_file_to_experiment(
        session: AsyncSession,
        file_id: int,
        new_experiment_id: int,
        user_id: int,
        *,
        org_id: int,
        commit: bool = True,
    ) -> None:
        """Move one of ``org_id``'s files from one experiment to another."""
        await FileOrganizationService._relocate(
            session, org_id=org_id, file_id=file_id, experiment_id=new_experiment_id, user_id=user_id, commit=commit
        )

    @staticmethod
    async def unlink_file_from_experiment(
        session: AsyncSession,
        file_id: int,
        user_id: int,
        *,
        org_id: int,
        commit: bool = True,
    ) -> None:
        """Unlink one of ``org_id``'s files from its experiment, moving it to ``unlinked/files/{uuid}/``."""
        await FileOrganizationService._relocate(
            session, org_id=org_id, file_id=file_id, experiment_id=None, user_id=user_id, commit=commit
        )
