"""Reference data service — CRUD, governance, and impact assessment for reference datasets."""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_review import PipelineRunReview
from app.models.reference_dataset import (
    ReferenceDataset,
    ReferenceDatasetFile,
    pipeline_run_references,
)
from app.models.reference_import_progress import ReferenceImportProgress
from app.schemas.reference_dataset import (
    ImpactPipelineRun,
    ImpactSummary,
    ReferenceDatasetCreate,
    ReferenceDeprecateRequest,
    ReferenceImportRequest,
    ReferenceImportStatusResponse,
    ReferenceUploadInitRequest,
)
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import REFERENCE_DEPRECATED

logger = logging.getLogger("bioaf.reference_data")

# GCS resumable upload sessions are valid for ~7 days; report a conservative
# 6-day expiry so the UI prompts to re-init before the actual server cutoff.
RESUMABLE_SESSION_TTL = timedelta(days=6)


def _slugify(value: str) -> str:
    """Lowercase, replace runs of non-alphanumeric with `-`, strip ends."""
    out = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return out or "ref"


class ReferenceDataService:
    """Static methods for reference dataset management."""

    @staticmethod
    async def list_references(
        session: AsyncSession,
        org_id: int,
        *,
        category: str | None = None,
        scope: str | None = None,
        status: str | None = None,
        name_search: str | None = None,
    ) -> tuple[list[ReferenceDataset], int]:
        """List reference datasets with optional filters."""
        query: Select = (
            select(ReferenceDataset)
            .where(ReferenceDataset.organization_id == org_id)
            .order_by(ReferenceDataset.created_at.desc())
        )
        count_query = (
            select(func.count()).select_from(ReferenceDataset).where(ReferenceDataset.organization_id == org_id)
        )

        if category:
            query = query.where(ReferenceDataset.category == category)
            count_query = count_query.where(ReferenceDataset.category == category)
        if scope:
            query = query.where(ReferenceDataset.scope == scope)
            count_query = count_query.where(ReferenceDataset.scope == scope)
        if status:
            query = query.where(ReferenceDataset.status == status)
            count_query = count_query.where(ReferenceDataset.status == status)
        if name_search:
            query = query.where(ReferenceDataset.name.ilike(f"%{name_search}%"))
            count_query = count_query.where(ReferenceDataset.name.ilike(f"%{name_search}%"))

        total = (await session.execute(count_query)).scalar() or 0
        result = await session.execute(query)
        return list(result.scalars().all()), total

    @staticmethod
    async def list_versions_by_name(
        session: AsyncSession,
        org_id: int,
        *,
        name: str,
        category: str,
    ) -> tuple[list[ReferenceDataset], int]:
        """Return every version of a (name, category) within an org, newest first.

        Spec §4 versioning UX — drives the version-history view on the
        reference detail page in a single round-trip.
        """
        query = (
            select(ReferenceDataset)
            .where(
                ReferenceDataset.organization_id == org_id,
                ReferenceDataset.name == name,
                ReferenceDataset.category == category,
            )
            .order_by(ReferenceDataset.created_at.desc())
        )
        result = await session.execute(query)
        rows = list(result.scalars().all())
        return rows, len(rows)

    @staticmethod
    async def get_reference(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
    ) -> ReferenceDataset | None:
        """Get reference dataset detail with files and user relationships."""
        result = await session.execute(
            select(ReferenceDataset)
            .options(
                selectinload(ReferenceDataset.files),
                selectinload(ReferenceDataset.uploaded_by),
                selectinload(ReferenceDataset.approved_by),
            )
            .where(
                ReferenceDataset.id == reference_id,
                ReferenceDataset.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_reference(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: ReferenceDatasetCreate,
    ) -> ReferenceDataset:
        """Create a reference dataset with files in one transaction."""
        dataset = ReferenceDataset(
            organization_id=org_id,
            name=data.name,
            category=data.category,
            scope=data.scope,
            version=data.version,
            source_url=data.source_url,
            gcs_prefix=data.gcs_prefix,
            total_size_bytes=data.total_size_bytes,
            file_count=len(data.files),
            md5_manifest_json=data.md5_manifest_json,
            uploaded_by_user_id=user_id,
            status="active",
        )
        session.add(dataset)
        await session.flush()

        for f in data.files:
            file_record = ReferenceDatasetFile(
                reference_dataset_id=dataset.id,
                filename=f.filename,
                gcs_uri=f.gcs_uri,
                size_bytes=f.size_bytes,
                md5_checksum=f.md5_checksum,
                file_type=f.file_type,
            )
            session.add(file_record)

        await log_action(
            session,
            user_id=user_id,
            entity_type="reference_dataset",
            entity_id=dataset.id,
            action="created",
            details={
                "name": data.name,
                "version": data.version,
                "category": data.category,
                "scope": data.scope,
                "file_count": len(data.files),
            },
        )

        return dataset

    @staticmethod
    async def deprecate_reference(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
        user_id: int,
        request: ReferenceDeprecateRequest,
    ) -> ReferenceDataset:
        """Deprecate a reference dataset. Public scope requires admin approval."""
        dataset = await ReferenceDataService.get_reference(session, reference_id, org_id)
        if not dataset:
            raise ValueError("Reference dataset not found")
        if dataset.status != "active":
            raise ValueError(f"Cannot deprecate a dataset with status '{dataset.status}'")

        if request.superseded_by_id:
            successor = await ReferenceDataService.get_reference(session, request.superseded_by_id, org_id)
            if not successor:
                raise ValueError("Superseding reference dataset not found")

        previous = {"status": dataset.status}

        if dataset.scope == "internal":
            # Internal: immediate deprecation
            dataset.status = "deprecated"
            dataset.deprecation_note = request.deprecation_note
            dataset.superseded_by_id = request.superseded_by_id
            await session.flush()

            await log_action(
                session,
                user_id=user_id,
                entity_type="reference_dataset",
                entity_id=dataset.id,
                action="deprecated",
                details={
                    "deprecation_note": request.deprecation_note,
                    "superseded_by_id": request.superseded_by_id,
                },
                previous_value=previous,
            )

            # Fire-and-forget notification
            asyncio.create_task(
                event_bus.emit(
                    REFERENCE_DEPRECATED,
                    {
                        "event_type": REFERENCE_DEPRECATED,
                        "org_id": org_id,
                        "user_id": user_id,
                        "entity_type": "reference_dataset",
                        "entity_id": dataset.id,
                        "title": f"Reference deprecated: {dataset.name} {dataset.version}",
                        "message": request.deprecation_note,
                        "metadata": {
                            "reference_id": dataset.id,
                            "name": dataset.name,
                            "version": dataset.version,
                            "scope": dataset.scope,
                        },
                    },
                )
            )
        else:
            # Public: requires admin approval
            dataset.status = "pending_approval"
            dataset.deprecation_note = request.deprecation_note
            dataset.superseded_by_id = request.superseded_by_id
            await session.flush()

            await log_action(
                session,
                user_id=user_id,
                entity_type="reference_dataset",
                entity_id=dataset.id,
                action="deprecation_requested",
                details={
                    "deprecation_note": request.deprecation_note,
                    "superseded_by_id": request.superseded_by_id,
                },
                previous_value=previous,
            )

        return dataset

    @staticmethod
    async def approve_deprecation(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
        user_id: int,
    ) -> ReferenceDataset:
        """Admin approves a pending public deprecation."""
        dataset = await ReferenceDataService.get_reference(session, reference_id, org_id)
        if not dataset:
            raise ValueError("Reference dataset not found")
        if dataset.status != "pending_approval":
            raise ValueError(f"Cannot approve deprecation: status is '{dataset.status}', expected 'pending_approval'")

        previous = {"status": dataset.status}
        dataset.status = "deprecated"
        dataset.approved_by_user_id = user_id
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="reference_dataset",
            entity_id=dataset.id,
            action="deprecation_approved",
            details={"approved_by_user_id": user_id},
            previous_value=previous,
        )

        asyncio.create_task(
            event_bus.emit(
                REFERENCE_DEPRECATED,
                {
                    "event_type": REFERENCE_DEPRECATED,
                    "org_id": org_id,
                    "user_id": user_id,
                    "entity_type": "reference_dataset",
                    "entity_id": dataset.id,
                    "title": f"Reference deprecation approved: {dataset.name} {dataset.version}",
                    "message": dataset.deprecation_note or "",
                    "metadata": {
                        "reference_id": dataset.id,
                        "name": dataset.name,
                        "version": dataset.version,
                        "scope": dataset.scope,
                    },
                },
            )
        )

        return dataset

    @staticmethod
    async def get_impact(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
    ) -> ImpactSummary:
        """Compute impact assessment: which pipeline runs and experiments used this reference.

        Single query with JOINs — no N+1.
        """
        # Verify reference exists
        ref_exists = await session.execute(
            select(ReferenceDataset.id).where(
                ReferenceDataset.id == reference_id,
                ReferenceDataset.organization_id == org_id,
            )
        )
        if not ref_exists.scalar_one_or_none():
            raise ValueError("Reference dataset not found")

        # Single query joining pipeline_run_references -> pipeline_runs -> experiments + reviews
        active_review_subq = (
            select(
                PipelineRunReview.pipeline_run_id,
                PipelineRunReview.verdict,
            )
            .where(PipelineRunReview.superseded_by_id.is_(None))
            .distinct(PipelineRunReview.pipeline_run_id)
            .subquery()
        )

        query = (
            select(
                PipelineRun.id.label("pipeline_run_id"),
                PipelineRun.pipeline_name,
                PipelineRun.pipeline_version,
                PipelineRun.experiment_id,
                Experiment.name.label("experiment_name"),
                PipelineRun.status,
                active_review_subq.c.verdict.label("review_verdict"),
                PipelineRun.completed_at,
            )
            .select_from(pipeline_run_references)
            .join(PipelineRun, PipelineRun.id == pipeline_run_references.c.pipeline_run_id)
            .outerjoin(Experiment, Experiment.id == PipelineRun.experiment_id)
            .outerjoin(
                active_review_subq,
                active_review_subq.c.pipeline_run_id == PipelineRun.id,
            )
            .where(pipeline_run_references.c.reference_dataset_id == reference_id)
            .order_by(PipelineRun.created_at.desc())
        )

        result = await session.execute(query)
        rows = result.all()

        runs = []
        experiment_ids: set[int] = set()
        for row in rows:
            runs.append(
                ImpactPipelineRun(
                    pipeline_run_id=row.pipeline_run_id,
                    pipeline_name=row.pipeline_name,
                    pipeline_version=row.pipeline_version,
                    experiment_id=row.experiment_id,
                    experiment_name=row.experiment_name,
                    status=row.status,
                    review_verdict=row.review_verdict,
                    completed_at=row.completed_at,
                )
            )
            if row.experiment_id:
                experiment_ids.add(row.experiment_id)

        return ImpactSummary(
            reference_dataset_id=reference_id,
            total_pipeline_runs=len(runs),
            total_experiments=len(experiment_ids),
            pipeline_runs=runs,
        )

    @staticmethod
    async def get_pipeline_run_references(
        session: AsyncSession,
        pipeline_run_id: int,
    ) -> list[ReferenceDataset]:
        """Return reference datasets used by a specific pipeline run."""
        result = await session.execute(
            select(ReferenceDataset)
            .join(
                pipeline_run_references,
                pipeline_run_references.c.reference_dataset_id == ReferenceDataset.id,
            )
            .where(pipeline_run_references.c.pipeline_run_id == pipeline_run_id)
            .order_by(ReferenceDataset.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def link_pipeline_run_to_references(
        session: AsyncSession,
        pipeline_run_id: int,
        reference_ids: list[int],
    ) -> None:
        """Create linkage records between a pipeline run and reference datasets."""
        for ref_id in reference_ids:
            await session.execute(
                pipeline_run_references.insert().values(
                    pipeline_run_id=pipeline_run_id,
                    reference_dataset_id=ref_id,
                )
            )

    # --- Upload (resumable session) flow — spec §2 -----------------------------

    @staticmethod
    async def _get_references_bucket(session: AsyncSession) -> str:
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'references_bucket_name'"))
        name = result.scalar_one_or_none()
        if not name or name == "null":
            raise ValueError("References bucket not configured. Deploy storage infrastructure first.")
        return name

    @staticmethod
    def _create_resumable_session(
        bucket_name: str,
        blob_path: str,
        content_type: str,
        size_bytes: int,
        origin: str | None = None,
        credentials=None,
    ) -> str:
        """Create a GCS resumable upload session and return its session URL.

        Tests monkey-patch this to avoid real GCS calls; production calls
        Google Cloud Storage via the SDK.
        """
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.create_resumable_upload_session(
            content_type=content_type,
            size=size_bytes,
            origin=origin,
        )

    @staticmethod
    async def init_upload(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        request: ReferenceUploadInitRequest,
        *,
        request_origin: str | None = None,
    ) -> tuple[ReferenceDataset, list[dict]]:
        """Initiate a multi-file resumable upload to the references bucket.

        Creates the ReferenceDataset row in status='uploading' and returns
        per-file resumable session URLs the browser can PUT chunks against.
        See spec §2 for the state machine.
        """
        if not request.files:
            raise ValueError("init_upload requires at least one file in `files`")

        # Up-front uniqueness check so we don't create a GCS session before failing
        existing = await session.execute(
            select(ReferenceDataset.id).where(
                ReferenceDataset.organization_id == org_id,
                ReferenceDataset.name == request.name,
                ReferenceDataset.version == request.version,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Reference '{request.name}' version '{request.version}' already exists")

        bucket_name = await ReferenceDataService._get_references_bucket(session)

        # GCS prefix: {category}/{slug-name}/{slug-version}/
        gcs_prefix = f"{request.category}/{_slugify(request.name)}/{_slugify(request.version)}/"

        dataset = ReferenceDataset(
            organization_id=org_id,
            name=request.name,
            category=request.category,
            scope=request.scope,
            version=request.version,
            source_url=request.source_url,
            gcs_prefix=gcs_prefix,
            file_count=len(request.files),
            uploaded_by_user_id=user_id,
            status="uploading",
        )
        session.add(dataset)
        await session.flush()

        from app.services.upload_service import UploadService

        credentials = await UploadService._get_gcs_credentials(session)

        uploads: list[dict] = []
        expires_at = datetime.now(timezone.utc) + RESUMABLE_SESSION_TTL
        for spec in request.files:
            blob_path = f"{gcs_prefix}{spec.filename}"
            # Persist skeleton file row so upload_complete can verify each
            # declared file arrived. md5/size are filled in at finalize.
            session.add(
                ReferenceDatasetFile(
                    reference_dataset_id=dataset.id,
                    filename=spec.filename,
                    gcs_uri=f"gs://{bucket_name}/{blob_path}",
                    size_bytes=spec.size_bytes,
                    md5_checksum=spec.md5_checksum,
                )
            )
            session_url = ReferenceDataService._create_resumable_session(
                bucket_name=bucket_name,
                blob_path=blob_path,
                content_type=spec.content_type or "application/octet-stream",
                size_bytes=spec.size_bytes,
                origin=request_origin,
                credentials=credentials,
            )
            uploads.append(
                {
                    "filename": spec.filename,
                    "session_url": session_url,
                    "expires_at": expires_at,
                }
            )

        await log_action(
            session,
            user_id=user_id,
            entity_type="reference_dataset",
            entity_id=dataset.id,
            action="upload_initiated",
            details={
                "name": request.name,
                "version": request.version,
                "category": request.category,
                "scope": request.scope,
                "file_count": len(request.files),
                "bucket": bucket_name,
                "gcs_prefix": gcs_prefix,
            },
        )

        return dataset, uploads

    # --- Upload finalize / abort -----------------------------------------------

    @staticmethod
    def _list_uploaded_blobs(bucket_name: str, prefix: str, credentials=None):
        """List blobs under `prefix`. Tests monkey-patch this."""
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        return list(bucket.list_blobs(prefix=prefix))

    @staticmethod
    def _delete_blobs(bucket_name: str, prefix: str, credentials=None) -> None:
        """Delete every blob under `prefix`. Tests monkey-patch this."""
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        for blob in bucket.list_blobs(prefix=prefix):
            try:
                blob.delete()
            except Exception as e:
                logger.warning("Failed to delete blob %s: %s", blob.name, e)

    @staticmethod
    async def upload_complete(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
        user_id: int,
    ) -> ReferenceDataset:
        """Finalize a resumable upload — list GCS, verify, persist files, flip status.

        Lifecycle (spec §2):
          - status='uploading' → 'active' (internal scope)
          - status='uploading' → 'pending_approval' (public scope)
          - status='uploading' → 'failed' on md5 mismatch (objects purged)
        """
        dataset = await ReferenceDataService.get_reference(session, reference_id, org_id)
        if not dataset:
            raise ValueError("Reference dataset not found")
        if dataset.status != "uploading":
            raise ValueError(f"Cannot finalize: status is '{dataset.status}', expected 'uploading'")

        bucket_name = await ReferenceDataService._get_references_bucket(session)
        from app.services.upload_service import UploadService

        credentials = await UploadService._get_gcs_credentials(session)

        blobs = ReferenceDataService._list_uploaded_blobs(bucket_name, dataset.gcs_prefix, credentials=credentials)
        # Index blobs by their basename (relative to gcs_prefix)
        blob_by_name: dict[str, object] = {}
        for b in blobs:
            base = b.name[len(dataset.gcs_prefix) :] if b.name.startswith(dataset.gcs_prefix) else b.name
            blob_by_name[base] = b

        expected = list(dataset.files)
        missing = [f.filename for f in expected if f.filename not in blob_by_name]
        if missing:
            raise ValueError(f"Upload incomplete: missing files in GCS: {', '.join(missing)}")

        # MD5 verification: compare client-supplied md5 (if any) to GCS metadata
        manifest: dict[str, str] = {}
        total_size = 0
        for file_row in expected:
            blob = blob_by_name[file_row.filename]
            gcs_md5 = getattr(blob, "md5_hash", None)
            gcs_size = int(getattr(blob, "size", 0) or 0)
            if file_row.md5_checksum and gcs_md5 and file_row.md5_checksum != gcs_md5:
                # Mark failed and purge objects, then raise.
                dataset.status = "failed"
                dataset.deprecation_note = (
                    f"md5 mismatch for {file_row.filename}: declared {file_row.md5_checksum}, bucket reports {gcs_md5}"
                )
                await session.flush()
                try:
                    ReferenceDataService._delete_blobs(bucket_name, dataset.gcs_prefix, credentials=credentials)
                except Exception as e:
                    logger.warning("Failed to purge blobs for failed upload %s: %s", dataset.id, e)
                await log_action(
                    session,
                    user_id=user_id,
                    entity_type="reference_dataset",
                    entity_id=dataset.id,
                    action="upload_failed",
                    details={"reason": "md5_mismatch", "filename": file_row.filename},
                )
                raise ValueError(dataset.deprecation_note)
            file_row.md5_checksum = gcs_md5
            file_row.size_bytes = gcs_size
            if gcs_md5:
                manifest[file_row.filename] = gcs_md5
            total_size += gcs_size

        dataset.md5_manifest_json = manifest
        dataset.total_size_bytes = total_size
        dataset.status = "pending_approval" if dataset.scope == "public" else "active"
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="reference_dataset",
            entity_id=dataset.id,
            action="upload_completed",
            details={
                "name": dataset.name,
                "version": dataset.version,
                "scope": dataset.scope,
                "file_count": len(expected),
                "total_size_bytes": total_size,
                "final_status": dataset.status,
            },
        )

        return dataset

    @staticmethod
    async def abort_upload(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
        user_id: int,
    ) -> None:
        """Idempotent abort: purge GCS objects under prefix and delete the row."""
        dataset = await ReferenceDataService.get_reference(session, reference_id, org_id)
        if not dataset:
            return  # idempotent

        bucket_name = await ReferenceDataService._get_references_bucket(session)
        from app.services.upload_service import UploadService

        credentials = await UploadService._get_gcs_credentials(session)
        try:
            ReferenceDataService._delete_blobs(bucket_name, dataset.gcs_prefix, credentials=credentials)
        except Exception as e:
            logger.warning("Failed to purge blobs while aborting %s: %s", reference_id, e)

        await log_action(
            session,
            user_id=user_id,
            entity_type="reference_dataset",
            entity_id=dataset.id,
            action="upload_aborted",
            details={"name": dataset.name, "version": dataset.version},
        )

        await session.delete(dataset)
        await session.flush()

    # --- Import-from-URL (GKE Job) flow — spec §3 ------------------------------

    @staticmethod
    def _create_import_job(
        *,
        reference_id: int,
        source_url: str,
        source_md5_url: str | None,
        gcs_prefix: str,
        bucket_name: str,
        extract: str,
        auth_header: str | None,
    ) -> str:
        """Launch the importer GKE Job and return its name. Tests monkey-patch.

        Production: builds a kubernetes BatchV1 Job from the spec in
        documentation/spec-reference-data-ingest.md §3 and returns the Job's
        metadata.name.
        """
        from kubernetes import client as k8s_client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        nonce = re.sub(r"[^a-z0-9]", "", str(reference_id))[:6] or "0"
        job_name = f"refimport-{reference_id}-{nonce}"
        env = [
            k8s_client.V1EnvVar(name="REFERENCE_ID", value=str(reference_id)),
            k8s_client.V1EnvVar(name="SOURCE_URL", value=source_url),
            k8s_client.V1EnvVar(name="GCS_PREFIX", value=gcs_prefix),
            k8s_client.V1EnvVar(name="GCS_BUCKET", value=bucket_name),
            k8s_client.V1EnvVar(name="EXTRACT_MODE", value=extract),
        ]
        if source_md5_url:
            env.append(k8s_client.V1EnvVar(name="SOURCE_MD5_URL", value=source_md5_url))
        if auth_header:
            env.append(k8s_client.V1EnvVar(name="SOURCE_AUTH_HEADER", value=auth_header))

        container = k8s_client.V1Container(
            name="importer",
            image="us-central1-docker.pkg.dev/bioaf/bioaf-reference-importer:latest",
            env=env,
            resources=k8s_client.V1ResourceRequirements(
                requests={"cpu": "1", "memory": "2Gi"},
                limits={"cpu": "2", "memory": "4Gi"},
            ),
        )
        pod_spec = k8s_client.V1PodSpec(
            restart_policy="Never",
            service_account_name="bioaf-reference-importer",
            containers=[container],
        )
        job = k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                labels={
                    "bioaf.app/job-type": "reference-import",
                    "bioaf.app/reference-id": str(reference_id),
                },
            ),
            spec=k8s_client.V1JobSpec(
                backoff_limit=1,
                ttl_seconds_after_finished=3600,
                template=k8s_client.V1PodTemplateSpec(spec=pod_spec),
            ),
        )
        k8s_client.BatchV1Api().create_namespaced_job(namespace="default", body=job)
        return job_name

    @staticmethod
    def _delete_import_job(job_name: str) -> None:
        """Delete the importer GKE Job. Tests monkey-patch."""
        from kubernetes import client as k8s_client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        k8s_client.BatchV1Api().delete_namespaced_job(
            name=job_name,
            namespace="default",
            propagation_policy="Background",
        )

    @staticmethod
    async def start_import(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        request: ReferenceImportRequest,
    ) -> tuple[ReferenceDataset, str]:
        """Create the dataset, progress row, and launch the importer GKE Job."""
        # Up-front uniqueness check
        existing = await session.execute(
            select(ReferenceDataset.id).where(
                ReferenceDataset.organization_id == org_id,
                ReferenceDataset.name == request.name,
                ReferenceDataset.version == request.version,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Reference '{request.name}' version '{request.version}' already exists")

        bucket_name = await ReferenceDataService._get_references_bucket(session)
        gcs_prefix = f"{request.category}/{_slugify(request.name)}/{_slugify(request.version)}/"

        dataset = ReferenceDataset(
            organization_id=org_id,
            name=request.name,
            category=request.category,
            scope=request.scope,
            version=request.version,
            source_url=request.source_url,
            gcs_prefix=gcs_prefix,
            uploaded_by_user_id=user_id,
            status="uploading",
        )
        session.add(dataset)
        await session.flush()

        job_id = ReferenceDataService._create_import_job(
            reference_id=dataset.id,
            source_url=request.source_url,
            source_md5_url=request.source_md5_url,
            gcs_prefix=gcs_prefix,
            bucket_name=bucket_name,
            extract=request.extract,
            auth_header=request.auth_header,
        )

        progress = ReferenceImportProgress(
            reference_id=dataset.id,
            status="pending",
            import_job_id=job_id,
        )
        session.add(progress)

        await log_action(
            session,
            user_id=user_id,
            entity_type="reference_dataset",
            entity_id=dataset.id,
            action="import_started",
            details={
                "name": request.name,
                "version": request.version,
                "category": request.category,
                "scope": request.scope,
                "source_url": request.source_url,
                "extract": request.extract,
                "import_job_id": job_id,
                "gcs_prefix": gcs_prefix,
                "bucket": bucket_name,
            },
        )
        await session.flush()
        return dataset, job_id

    @staticmethod
    async def get_import_status(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
    ) -> ReferenceImportStatusResponse:
        """Read the import_progress row for a reference owned by org_id."""
        # Make sure the dataset exists & is org-scoped before reading progress.
        dataset = await session.execute(
            select(ReferenceDataset.id).where(
                ReferenceDataset.id == reference_id,
                ReferenceDataset.organization_id == org_id,
            )
        )
        if dataset.scalar_one_or_none() is None:
            raise ValueError("Reference dataset not found")

        result = await session.execute(
            select(
                ReferenceImportProgress.reference_id,
                ReferenceImportProgress.status,
                ReferenceImportProgress.progress_pct,
                ReferenceImportProgress.bytes_downloaded,
                ReferenceImportProgress.total_bytes,
                ReferenceImportProgress.error_message,
                ReferenceImportProgress.import_job_id,
                ReferenceImportProgress.updated_at,
            ).where(ReferenceImportProgress.reference_id == reference_id)
        )
        row = result.one_or_none()
        if row is None:
            raise ValueError("No import progress record for this reference")

        return ReferenceImportStatusResponse(
            reference_id=row.reference_id,
            status=row.status,
            progress_pct=row.progress_pct,
            bytes_downloaded=row.bytes_downloaded,
            total_bytes=row.total_bytes,
            error_message=row.error_message,
            import_job_id=row.import_job_id,
            updated_at=row.updated_at,
        )

    @staticmethod
    async def cancel_import(
        session: AsyncSession,
        reference_id: int,
        org_id: int,
        user_id: int,
    ) -> None:
        """Terminate the GKE job + abort the in-flight reference (purge + delete)."""
        progress = await session.get(ReferenceImportProgress, reference_id)
        if progress and progress.import_job_id:
            try:
                ReferenceDataService._delete_import_job(progress.import_job_id)
            except Exception as e:
                logger.warning("Failed to delete import job %s: %s", progress.import_job_id, e)

        await ReferenceDataService.abort_upload(session, reference_id, org_id, user_id)

    @staticmethod
    async def record_import_progress(
        session: AsyncSession,
        reference_id: int,
        *,
        status: str,
        progress_pct: int | None = None,
        bytes_downloaded: int | None = None,
        total_bytes: int | None = None,
        error_message: str | None = None,
    ) -> ReferenceImportProgress:
        """Update the progress row. Called by the importer container's callback."""
        progress = await session.get(ReferenceImportProgress, reference_id)
        if not progress:
            raise ValueError("No import progress record for this reference")

        progress.status = status
        if progress_pct is not None:
            progress.progress_pct = progress_pct
        if bytes_downloaded is not None:
            progress.bytes_downloaded = bytes_downloaded
        if total_bytes is not None:
            progress.total_bytes = total_bytes
        if error_message is not None:
            progress.error_message = error_message

        if status == "failed":
            dataset = await session.get(ReferenceDataset, reference_id)
            if dataset:
                dataset.status = "failed"
                dataset.deprecation_note = error_message or "Import failed"

        await session.flush()
        return progress
