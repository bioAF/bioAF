import asyncio
import logging
import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.services.event_bus import event_bus
from app.services.event_types import DATA_UPLOADED
from app.services.file_service import FileService

logger = logging.getLogger("bioaf.upload_service")

# In-memory pending uploads (in production, use Redis or DB table)
_pending_uploads: dict[str, dict] = {}

# Illumina filename pattern: SampleName_S1_L001_R1_001.fastq.gz
ILLUMINA_PATTERN = re.compile(
    r"^(?P<sample_name>.+?)_S(?P<sample_number>\d+)_L(?P<lane>\d{3})_(?P<read>R[12I])_(?P<set_number>\d{3})\.fastq\.gz$"
)


class UploadService:
    @staticmethod
    def parse_illumina_filename(filename: str) -> dict | None:
        """Extract sample name, lane, read number, set number from Illumina filename."""
        match = ILLUMINA_PATTERN.match(filename)
        if not match:
            return None
        return {
            "sample_name": match.group("sample_name"),
            "sample_number": int(match.group("sample_number")),
            "lane": int(match.group("lane")),
            "read": match.group("read"),
            "set_number": int(match.group("set_number")),
        }

    @staticmethod
    def validate_fastq_filename(filename: str) -> bool:
        """Check if filename has valid FASTQ extension."""
        lower = filename.lower()
        return lower.endswith(".fastq.gz") or lower.endswith(".fq.gz")

    @staticmethod
    async def _get_gcs_credentials(session: AsyncSession):
        """Return GCS credentials from platform_config, or None to use ADC.

        When gcp_credential_source is 'service_account_key', parses the stored
        JSON key and returns service_account.Credentials so the GCS client
        bypasses the VM's OAuth scopes entirely. Returns None otherwise.
        """
        import json as _json

        result = await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('gcp_credential_source', 'gcp_service_account_key')"
            )
        )
        config = {r[0]: r[1] for r in result.fetchall()}

        if config.get("gcp_credential_source") != "service_account_key":
            return None

        key_json = config.get("gcp_service_account_key")
        if not key_json or key_json == "null":
            return None

        try:
            from google.oauth2 import service_account

            key_data = _json.loads(key_json)
            return service_account.Credentials.from_service_account_info(
                key_data,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as e:
            logger.warning("Failed to load GCS credentials from platform_config: %s", e)
            return None

    @staticmethod
    async def _get_ingest_bucket(session: AsyncSession) -> str:
        """Read ingest bucket name from platform_config."""
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'ingest_bucket_name'"))
        name = result.scalar_one_or_none()
        if not name or name == "null":
            raise ValueError("Ingest bucket not configured. Deploy storage infrastructure first.")
        return name

    @staticmethod
    async def initiate_upload(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        filename: str,
        expected_size: int | None,
        expected_md5: str | None,
        experiment_id: int | None,
        sample_ids: list[int] | None = None,
        project_id: int | None = None,
        is_global: bool = False,
    ) -> dict:
        """Initiate an upload, returning upload_id and signed URL."""
        upload_id = str(uuid.uuid4())
        bucket_name = await UploadService._get_ingest_bucket(session)
        gcs_path = f"uploads/{upload_id}/{filename}"
        gcs_uri = f"gs://{bucket_name}/{gcs_path}"

        # Generate signed URL via GCS client
        credentials = await UploadService._get_gcs_credentials(session)
        signed_url = await UploadService._generate_signed_upload_url(bucket_name, gcs_path, credentials=credentials)

        _pending_uploads[upload_id] = {
            "org_id": org_id,
            "user_id": user_id,
            "filename": filename,
            "gcs_uri": gcs_uri,
            "expected_size": expected_size,
            "expected_md5": expected_md5,
            "project_id": project_id,
            "experiment_id": experiment_id,
            "sample_ids": sample_ids or [],
            "is_global": is_global,
        }

        return {
            "upload_id": upload_id,
            "signed_url": signed_url,
            "gcs_uri": gcs_uri,
        }

    @staticmethod
    async def complete_upload(
        session: AsyncSession,
        org_id: int,
        upload_id: str,
        actual_md5: str,
    ) -> File:
        """Complete an upload: verify MD5, create file record, link to experiment/samples."""
        pending = _pending_uploads.pop(upload_id, None)
        if not pending or pending["org_id"] != org_id:
            raise ValueError("Invalid or expired upload_id")

        # Verify MD5 if expected
        if pending["expected_md5"] and pending["expected_md5"] != actual_md5:
            raise ValueError(f"MD5 mismatch: expected {pending['expected_md5']}, got {actual_md5}")

        # Determine file type from extension
        filename = pending["filename"]
        file_type = UploadService._detect_file_type(filename)

        # Parse Illumina filename for tags
        illumina_info = UploadService.parse_illumina_filename(filename)
        tags = []
        if illumina_info:
            tags.append(f"lane:{illumina_info['lane']}")
            tags.append(f"read:{illumina_info['read']}")
            tags.append(f"sample:{illumina_info['sample_name']}")

        # Create file record
        file = await FileService.create_file_record(
            session,
            org_id=org_id,
            user_id=pending["user_id"],
            filename=filename,
            gcs_uri=pending["gcs_uri"],
            size_bytes=pending["expected_size"],
            md5_checksum=actual_md5,
            file_type=file_type,
            tags=tags,
            project_id=pending.get("project_id"),
            experiment_id=pending["experiment_id"],
            is_global=pending.get("is_global", False),
        )

        # Link to samples
        for sample_id in pending["sample_ids"]:
            await FileService.link_file_to_sample(session, file.id, sample_id)

        # Move file from ingest to raw bucket under experiment prefix
        experiment_id = pending["experiment_id"]
        if experiment_id:
            from app.services.file_organization import FileOrganizationService

            await FileOrganizationService.assign_file_to_experiment(session, file.id, experiment_id, pending["user_id"])

        # Auto-update experiment status if FASTQs uploaded
        if experiment_id and file_type == "fastq":
            await UploadService._auto_update_experiment_status(session, experiment_id, org_id, pending["user_id"])

        asyncio.create_task(
            event_bus.emit(
                DATA_UPLOADED,
                {
                    "event_type": DATA_UPLOADED,
                    "org_id": org_id,
                    "user_id": pending["user_id"],
                    "entity_type": "file",
                    "entity_id": file.id,
                    "title": f"File uploaded: {filename}",
                    "message": f"File '{filename}' ({file_type}) uploaded successfully",
                    "summary": f"File '{filename}' uploaded",
                },
            )
        )

        return file

    @staticmethod
    async def simple_upload(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        filename: str,
        file_obj,
        size_bytes: int | None = None,
        file_type: str | None = None,
        project_id: int | None = None,
        experiment_id: int | None = None,
        sample_ids: list[int] | None = None,
    ) -> File:
        """Stream a file directly to GCS without buffering the full content in memory."""
        upload_id = str(uuid.uuid4())
        bucket_name = await UploadService._get_ingest_bucket(session)
        gcs_path = f"uploads/{upload_id}/{filename}"
        gcs_uri = f"gs://{bucket_name}/{gcs_path}"

        # Upload to GCS -- raises on failure so no dangling DB records are created
        credentials = await UploadService._get_gcs_credentials(session)
        await UploadService._upload_file_to_gcs(bucket_name, gcs_path, file_obj, credentials=credentials)

        if not file_type:
            file_type = UploadService._detect_file_type(filename)

        file = await FileService.create_file_record(
            session,
            org_id=org_id,
            user_id=user_id,
            filename=filename,
            gcs_uri=gcs_uri,
            size_bytes=size_bytes,
            md5_checksum=None,
            file_type=file_type,
            project_id=project_id,
            experiment_id=experiment_id,
        )

        if sample_ids:
            for sample_id in sample_ids:
                await FileService.link_file_to_sample(session, file.id, sample_id)

        # Move file from ingest to raw bucket under experiment prefix
        if experiment_id:
            from app.services.file_organization import FileOrganizationService

            await FileOrganizationService.assign_file_to_experiment(session, file.id, experiment_id, user_id)

        if experiment_id and file_type == "fastq":
            await UploadService._auto_update_experiment_status(session, experiment_id, org_id, user_id)

        return file

    @staticmethod
    async def _auto_update_experiment_status(
        session: AsyncSession, experiment_id: int, org_id: int, user_id: int
    ) -> None:
        """Auto-transition experiment to fastq_uploaded if appropriate.

        Walks through intermediate statuses (registered -> library_prep ->
        sequencing -> fastq_uploaded) because uploading FASTQs implies the
        earlier steps already happened externally.
        """
        from app.services.experiment_service import ExperimentService

        # Statuses that precede fastq_uploaded, in order
        path_to_fastq = ["library_prep", "sequencing", "fastq_uploaded"]
        exp = await ExperimentService.get_experiment(session, experiment_id, org_id)
        if not exp or exp.status not in ("registered", "library_prep", "sequencing"):
            return

        try:
            # Find where we are in the path and advance from there
            current = exp.status
            for target in path_to_fastq:
                if current == "fastq_uploaded":
                    break
                await ExperimentService.update_status(session, experiment_id, org_id, user_id, target)
                current = target
        except Exception as e:
            logger.warning("Could not auto-update experiment status: %s", e)

    @staticmethod
    def _detect_file_type(filename: str) -> str:
        lower = filename.lower()
        if lower.endswith((".fastq.gz", ".fq.gz")):
            return "fastq"
        if lower.endswith(".bam"):
            return "bam"
        if lower.endswith(".h5ad"):
            return "h5ad"
        if lower.endswith(".pdf"):
            return "pdf"
        if lower.endswith(".png"):
            return "png"
        if lower.endswith(".svg"):
            return "svg"
        if lower.endswith(".csv"):
            return "csv"
        return "other"

    @staticmethod
    async def _generate_signed_upload_url(bucket_name: str, gcs_path: str, credentials=None) -> str:
        """Generate a signed URL for uploading to GCS.

        Requires credentials with signing capability (service_account.Credentials).
        ADC / VM default service account will not work here -- configure
        gcp_credential_source=service_account_key in platform_config.
        """
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)
        return blob.generate_signed_url(
            version="v4",
            expiration=3600,
            method="PUT",
            content_type="application/octet-stream",
        )

    @staticmethod
    async def _upload_file_to_gcs(bucket_name: str, gcs_path: str, file_obj, credentials=None) -> None:
        """Stream a file-like object to GCS. Raises on failure.

        Uses upload_from_file so the full content is never held in memory at once.
        """
        from google.cloud import storage as gcs_storage

        def _do_upload() -> None:
            client = gcs_storage.Client(credentials=credentials)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(gcs_path)
            blob.upload_from_file(file_obj, rewind=True)

        await asyncio.to_thread(_do_upload)
