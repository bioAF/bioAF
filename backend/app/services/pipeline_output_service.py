"""Register pipeline output files as File records in the database."""

import logging
import uuid as uuid_pkg

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.services import asset_identity
from app.services.file_service import FileService
from app.services.file_type_utils import classify_artifact_type, detect_file_type

logger = logging.getLogger("bioaf.pipeline_output_service")


class PipelineOutputService:
    @staticmethod
    def _match_samples(
        filename: str,
        gcs_uri: str,
        sample_extids: list[tuple[str, int]],
        sample_uids: list[tuple[uuid_pkg.UUID, int]] | None = None,
        emitted: list[dict] | None = None,
    ) -> list[int]:
        """Return ids of samples this output file belongs to.

        Three routes, tried in that order.

        **By the name THIS RUN EMITTED**, which is the middle route and the one
        that removes a live class of failure. A pipeline names its outputs after
        the samplesheet's value, and that value is not always the name the sample
        carries today: a scientist accepts a recommended spelling, the sheet says
        ``SAMPLE_101`` and the database says ``SAMPLE-101``, and matching the
        database name finds nothing. The run's own record says what it emitted,
        so the two can no longer drift apart. It resolves to a UID, which is what
        bioAF processes on; the name is a human-interface layer only.

        **By identity**, when the path names a sample's own UID. That is exact:
        the identifier bioAF put in the samplesheet is the one the pipeline named
        the file after, so no spelling can drift between the two. A UID is found
        wherever a pipeline puts it, because the position VARIES: nf-core/demo
        writes it as a directory segment AND a filename prefix, nf-core/bamtofastq
        writes it in the filename only, and a rule for one silently fails the
        other.

        **By name**, which is the live path until sheets emit identities, and
        stays as the fallback afterwards. Matches when the external_id is a full
        path segment (``.../star/SAMPLE-101/...``) or the filename starts with
        ``<extid>_`` / ``<extid>.``.

        The identity and emitted-name routes are taken only when they match a
        sample in THIS run, which keeps the change monotonic: each can add an
        exact match, never remove one that works today. A run with no record of
        what it emitted (every run launched before that record existed) falls
        straight through to the database name, and nothing is reconstructed for
        it. The spelling is ``s`` plus 32
        lowercase hex and an md5 is also 32 hex, so a path like ``s<md5>.tmp``
        parses as an identity belonging to nothing; letting that suppress name
        matching would turn a correctly matched file into an unattributed one.

        Returns [] for an output naming no sample, whether that is an aggregate
        report (e.g. multiqc) or a per-sample file bioAF failed to parse. Those
        two cannot be told apart from a name, so neither is guessed at: the caller
        attaches the file to the run and to no sample. Once every sheet carries a
        UID that ambiguity disappears, because a per-sample artifact always
        carries one.
        """
        if sample_uids:
            named = asset_identity.uids_in(f"{gcs_uri or ''}/{filename or ''}")
            if named:
                by_identity = [sid for uid, sid in sample_uids if uid in named]
                if by_identity:
                    deduped: list[int] = []
                    for sid in by_identity:
                        if sid not in deduped:
                            deduped.append(sid)
                    return deduped

        # Longest first, so `SAMPLE-10` cannot claim `SAMPLE-101`'s outputs.
        # Same guard the database names below carry, for the same reason.
        by_emitted = sorted(
            (
                (str(entry.get("name") or ""), entry.get("sample_id"))
                for entry in (emitted or [])
                if entry.get("name") and entry.get("sample_id") is not None
            ),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        if by_emitted:
            found = PipelineOutputService._match_by_name(filename, gcs_uri, by_emitted)
            if found:
                return found

        return PipelineOutputService._match_by_name(filename, gcs_uri, sample_extids)

    @staticmethod
    def _match_by_name(filename: str, gcs_uri: str, names: list[tuple[str, int]]) -> list[int]:
        """Samples whose NAME appears in this output's path or filename.

        A full path segment (``.../star/SAMPLE-101/...``) or a filename that
        starts with ``<name>_`` or ``<name>.``. Both forms are load-bearing and
        neither alone covers the catalog: nf-core/demo writes the value as a
        directory segment AND a filename prefix, nf-core/bamtofastq writes it in
        the filename only.
        """
        segments = set((gcs_uri or "").split("/"))
        matched: list[int] = []
        for extid, sid in names:
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
        # under paths/filenames carrying the sample's external_id). An output
        # naming no sample is attached to the run alone.
        sample_ids: list[int] = []
        sample_extids: list[tuple[str, int]] = []
        # The same samples by identity. Carried alongside the names rather than
        # instead of them: no sheet emits a UID yet, so the names are still the
        # live route, and an identity match is taken only when the path actually
        # names one of these samples.
        sample_uids: list[tuple[uuid_pkg.UUID, int]] = []
        if not is_project_scoped:
            result = await session.execute(
                select(Sample.id, Sample.external_id, Sample.uuid)
                .join(PipelineRunSample, PipelineRunSample.sample_id == Sample.id)
                .where(PipelineRunSample.pipeline_run_id == run.id)
            )
            for sid, extid, suid in result.all():
                sample_ids.append(sid)
                if extid:
                    sample_extids.append((extid, sid))
                if suid:
                    sample_uids.append((suid, sid))
            # Longest external_id first so e.g. "SAMPLE-10" can't shadow "SAMPLE-101".
            # UIDs need no such guard: they are fixed-length and bounded on both
            # sides, so one can never be a prefix of another.
            sample_extids.sort(key=lambda t: len(t[0]), reverse=True)

        # What this run actually put in its samplesheet's identity column,
        # recorded at launch. A pipeline names its outputs after that value, and
        # it is not always the name the sample carries today. Absent for every
        # run launched before the record existed, in which case the names below
        # remain the only route and behave exactly as they did.
        emitted = run.samplesheet_emitted_json or None
        if emitted and not is_project_scoped:
            in_this_run = set(sample_ids)
            emitted = [entry for entry in emitted if entry.get("sample_id") in in_this_run]

        # Collect existing gcs_uris to skip duplicates
        uris = [f["gcs_uri"] for f in collected_files]
        existing = await session.execute(select(File.storage_uri).where(File.storage_uri.in_(uris)))
        existing_uris: set[str] = {row[0] for row in existing.all()}

        created: list[File] = []
        unattributed: list[str] = []

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
                storage_uri=gcs_uri,
                size_bytes=file_dict.get("size_bytes"),
                md5_checksum=file_dict.get("md5_hash"),
                file_type=file_type,
                project_id=run.project_id if is_project_scoped else None,
                experiment_id=run.experiment_id,
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
                artifact_type=artifact_type,
            )

            # Associate the output with the sample(s) it belongs to: by the
            # sample's own identity where the path carries one, then by the name
            # THIS RUN EMITTED, then by its external_id as a path segment or
            # filename prefix. An output matching none is attached to the run
            # alone: it used to be linked to every sample in the run, which put
            # one sample's alignment on all of them. Fewer links, all of them
            # true.
            matched = PipelineOutputService._match_samples(
                filename, gcs_uri, sample_extids, sample_uids, emitted=emitted
            )
            if not matched and sample_ids:
                unattributed.append(filename)
            for sample_id in matched:
                await FileService.link_file_to_sample(session, file_record.id, sample_id)

            created.append(file_record)

        if unattributed:
            shown = ", ".join(unattributed[:20])
            if len(unattributed) > 20:
                shown += f", and {len(unattributed) - 20} more"
            logger.warning(
                "Pipeline run %d: %d output file(s) name no sample in the run and are attached to the run alone: %s",
                run.id,
                len(unattributed),
                shown,
            )

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
        existing = await session.execute(select(File.storage_uri).where(File.storage_uri.in_(uris)))
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
                storage_uri=gcs_uri,
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
