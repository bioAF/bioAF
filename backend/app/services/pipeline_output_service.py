"""Register pipeline output files as File records in the database."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.services.file_service import FileService
from app.services.file_type_utils import classify_artifact_type, detect_file_type

logger = logging.getLogger("bioaf.pipeline_output_service")


class PipelineOutputService:
    @staticmethod
    def _match_samples(filename: str, gcs_uri: str, sample_extids: list[tuple[str, int]]) -> list[int]:
        """Return ids of samples this output file belongs to, matched by external_id.

        Matches when the external_id is a full path segment of the GCS URI (e.g.
        ``.../star/SAMPLE-101/...``) or the filename starts with ``<extid>_``/
        ``<extid>.``. Returns [] for aggregate outputs (e.g. multiqc) that match
        no single sample, so the caller can fall back to all run samples.
        """
        segments = set((gcs_uri or "").split("/"))
        matched: list[int] = []
        for extid, sid in sample_extids:
            if (
                extid in segments
                or filename == extid
                or filename.startswith(f"{extid}_")
                or filename.startswith(f"{extid}.")
            ):
                if sid not in matched:
                    matched.append(sid)
        return matched

    @staticmethod
    async def register_outputs(
        session: AsyncSession,
        run: PipelineRun,
        collected_files: list[dict],
    ) -> list[File]:
        """Create File records for pipeline outputs and link to the run's samples.

        Args:
            session: DB session (caller commits).
            run: The completed PipelineRun.
            collected_files: Dicts from storage_adapter.collect_outputs()
                with keys: filename, gcs_uri, size_bytes, md5_hash.

        Returns:
            List of newly created File records.
        """
        if not collected_files:
            return []

        # Project-scoped runs (project_id set, experiment_id null) register files
        # against the project with no sample links. Experiment-scoped runs link
        # files to all samples in the run.
        is_project_scoped = run.experiment_id is None and run.project_id is not None

        # Map each run sample's external_id -> id so each output can be linked to
        # the sample it actually belongs to (nf-core writes per-sample outputs
        # under paths/filenames carrying the sample's external_id). Aggregate
        # outputs that match no single sample fall back to all run samples.
        sample_ids: list[int] = []
        sample_extids: list[tuple[str, int]] = []
        if not is_project_scoped:
            result = await session.execute(
                select(Sample.id, Sample.external_id)
                .join(PipelineRunSample, PipelineRunSample.sample_id == Sample.id)
                .where(PipelineRunSample.pipeline_run_id == run.id)
            )
            for sid, extid in result.all():
                sample_ids.append(sid)
                if extid:
                    sample_extids.append((extid, sid))
            # Longest external_id first so e.g. "SAMPLE-10" can't shadow "SAMPLE-101".
            sample_extids.sort(key=lambda t: len(t[0]), reverse=True)

        # Collect existing gcs_uris to skip duplicates
        uris = [f["gcs_uri"] for f in collected_files]
        existing = await session.execute(select(File.gcs_uri).where(File.gcs_uri.in_(uris)))
        existing_uris: set[str] = {row[0] for row in existing.all()}

        created: list[File] = []

        for file_dict in collected_files:
            gcs_uri = file_dict["gcs_uri"]
            if gcs_uri in existing_uris:
                logger.debug("Skipping duplicate gcs_uri: %s", gcs_uri)
                continue

            filename = file_dict["filename"]
            file_type = detect_file_type(filename)
            artifact_type = classify_artifact_type(filename)

            file_record = await FileService.create_file_record(
                session,
                org_id=run.organization_id,
                user_id=run.submitted_by_user_id,
                filename=filename,
                gcs_uri=gcs_uri,
                size_bytes=file_dict.get("size_bytes"),
                md5_checksum=file_dict.get("md5_hash"),
                file_type=file_type,
                project_id=run.project_id if is_project_scoped else None,
                experiment_id=run.experiment_id,
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
                artifact_type=artifact_type,
            )

            # Associate the output with the sample(s) it belongs to. Match the
            # sample external_id as a path segment or filename prefix; only fall
            # back to all run samples for aggregate outputs that match none.
            matched = PipelineOutputService._match_samples(filename, gcs_uri, sample_extids)
            targets = matched if matched else sample_ids
            for sample_id in targets:
                await FileService.link_file_to_sample(session, file_record.id, sample_id)

            created.append(file_record)

        logger.info(
            "Registered %d output files for pipeline run %d (skipped %d duplicates)",
            len(created),
            run.id,
            len(collected_files) - len(created),
        )
        return created

    @staticmethod
    async def register_nextflow_metadata(
        session: AsyncSession,
        run: PipelineRun,
    ) -> list[File]:
        """Register Nextflow report.html and trace.tsv as File records.

        These files live in the RAW storage store at deterministic paths based
        on the K8s job name. The URIs are resolved through the BAL storage
        adapter (Phase 5) so no caller names a bucket; a backend without a RAW
        store configured yields no metadata.
        """
        if not run.compute_job_ref:
            return []

        from app.adapters.models import StorageStore
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        try:
            report_uri = await adapter.resolve_uri(
                StorageStore.RAW, f"nextflow-reports/{run.compute_job_ref}/report.html"
            )
            trace_uri = await adapter.resolve_uri(StorageStore.RAW, f"nextflow-traces/{run.compute_job_ref}/trace.tsv")
        except ValueError:
            # No RAW store configured for this install.
            return []

        metadata_files = [
            {
                "filename": "report.html",
                "gcs_uri": report_uri,
                "artifact_type": "pipeline_report",
                "file_type": "report",
            },
            {
                "filename": "trace.tsv",
                "gcs_uri": trace_uri,
                "artifact_type": "pipeline_trace",
                "file_type": "count_matrix",
            },
        ]

        # Check which URIs already exist in DB
        uris = [f["gcs_uri"] for f in metadata_files]
        existing = await session.execute(select(File.gcs_uri).where(File.gcs_uri.in_(uris)))
        existing_uris: set[str] = {row[0] for row in existing.all()}

        # Check which blobs exist in GCS
        existing_blobs = await _check_gcs_blobs(metadata_files)

        created: list[File] = []
        for meta in metadata_files:
            gcs_uri = meta["gcs_uri"]
            if gcs_uri in existing_uris:
                continue
            if gcs_uri not in existing_blobs:
                continue

            is_project_scoped = run.experiment_id is None and run.project_id is not None
            file_record = await FileService.create_file_record(
                session,
                org_id=run.organization_id,
                user_id=run.submitted_by_user_id,
                filename=meta["filename"],
                gcs_uri=gcs_uri,
                size_bytes=existing_blobs[gcs_uri],
                md5_checksum=None,
                file_type=meta["file_type"],
                project_id=run.project_id if is_project_scoped else None,
                experiment_id=run.experiment_id,
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
                artifact_type=meta["artifact_type"],
            )
            created.append(file_record)

        if created:
            logger.info(
                "Registered %d Nextflow metadata files for run %d",
                len(created),
                run.id,
            )
        return created


async def _check_gcs_blobs(metadata_files: list[dict]) -> dict[str, int | None]:
    """Check which storage objects exist and return {storage_uri: size_bytes}."""
    from app.adapters.models import StorageObjectNotFound
    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()
    result: dict[str, int | None] = {}
    for meta in metadata_files:
        gcs_uri = meta["gcs_uri"]
        try:
            metadata = await adapter.get_object_metadata(gcs_uri)
        except StorageObjectNotFound:
            continue
        except Exception as e:
            logger.warning("Could not check storage object %s: %s", gcs_uri, e)
            continue
        result[gcs_uri] = metadata.size_bytes
    return result
