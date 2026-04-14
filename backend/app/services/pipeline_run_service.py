import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.sample import Sample
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_FAILED, PIPELINE_STARTED
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.quota_service import QuotaService
from app.services.sample_sheet_service import SampleSheetService
from app.adapters.registry import get_compute_adapter
from app.services.vocabulary_validator import VocabularyValidator

logger = logging.getLogger("bioaf.pipeline_runs")


class PipelineRunService:
    @staticmethod
    def _attach_experiment_files(samples: list, experiment_files: list) -> None:
        """Attach experiment-level FASTQ files to samples with no linked files.

        When the sample_files junction table is empty, fall back to
        experiment-level files so the sample sheet has FASTQ paths.
        Only attaches files with file_type 'fastq' or .fastq.gz extension.
        """
        fastq_files = [
            f
            for f in experiment_files
            if getattr(f, "file_type", "") == "fastq" or (getattr(f, "filename", "") or "").endswith(".fastq.gz")
        ]
        for sample in samples:
            if not sample.files:
                sample.files = list(fastq_files)

    @staticmethod
    async def launch_run(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: PipelineRunLaunchRequest,
    ) -> PipelineRun:
        """Launch a pipeline run — the core orchestration method."""
        # 1. Load pipeline from catalog
        pipeline = await PipelineCatalogService.get_pipeline(session, org_id, data.pipeline_key)
        if not pipeline:
            raise ValueError(f"Pipeline '{data.pipeline_key}' not found or not enabled")

        # 2. Load experiment
        exp_result = await session.execute(
            select(Experiment).where(
                Experiment.id == data.experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        experiment = exp_result.scalar_one_or_none()
        if not experiment:
            raise ValueError(f"Experiment {data.experiment_id} not found")

        # 3. Resolve sample_ids
        if data.sample_ids:
            sample_result = await session.execute(
                select(Sample)
                .where(
                    Sample.id.in_(data.sample_ids),
                    Sample.experiment_id == data.experiment_id,
                )
                .options(selectinload(Sample.files))
            )
            samples = list(sample_result.scalars().all())
            if len(samples) != len(data.sample_ids):
                raise ValueError("Some sample IDs do not belong to this experiment")
        else:
            sample_result = await session.execute(
                select(Sample).where(Sample.experiment_id == data.experiment_id).options(selectinload(Sample.files))
            )
            samples = list(sample_result.scalars().all())

        # 3b. Fall back to experiment-level files when sample_files is empty
        any_sample_missing_files = any(not s.files for s in samples)
        if any_sample_missing_files:
            exp_files_result = await session.execute(select(File).where(File.experiment_id == data.experiment_id))
            exp_files = list(exp_files_result.scalars().all())
            if exp_files:
                PipelineRunService._attach_experiment_files(samples, exp_files)

        # 4. Check quota
        allowed, message = await QuotaService.check_quota(session, user_id, estimated_hours=2.0)
        if not allowed:
            raise ValueError(f"Quota exceeded: {message}")

        # 5. Validate controlled vocabulary fields
        await VocabularyValidator.validate_pipeline_run_fields(
            session,
            {
                "reference_genome": data.reference_genome,
                "alignment_algorithm": data.alignment_algorithm,
            },
        )

        # 6. Merge parameters (user params override defaults)
        merged_params = dict(pipeline.default_params_json or {})
        merged_params.update(data.parameters)

        # 7. Create pipeline_runs record
        run = PipelineRun(
            organization_id=org_id,
            experiment_id=data.experiment_id,
            project_id=data.project_id,
            submitted_by_user_id=user_id,
            pipeline_name=pipeline.name,
            pipeline_version=pipeline.version,
            parameters_json=merged_params,
            reference_genome=data.reference_genome,
            alignment_algorithm=data.alignment_algorithm,
            status="pending",
            work_dir="/data/working/nextflow/run-{id}",
        )
        if data.resume_from_run_id:
            run.resume_from_run_id = data.resume_from_run_id
        session.add(run)
        await session.flush()

        # Update work_dir with actual ID
        run.work_dir = f"/data/working/nextflow/run-{run.id}"

        # 7. Create pipeline_run_samples linkage
        for sample in samples:
            link = PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id)
            session.add(link)
        await session.flush()

        # 7b. Record input files in junction table (ADR-038)
        seen_file_ids: set[int] = set()
        for sample in samples:
            for f in sample.files or []:
                if f.id not in seen_file_ids:
                    session.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=f.id))
                    seen_file_ids.add(f.id)
        if seen_file_ids:
            # Also populate input_files_json for backward compat
            run.input_files_json = sorted(seen_file_ids)
            await session.flush()

        # 8. Generate sample sheet
        sample_sheet_csv = SampleSheetService.generate_sheet(
            pipeline.pipeline_key,
            samples,
            merged_params,
        )

        # 9. Submit job via the compute adapter (BAL)
        try:
            compute_adapter = get_compute_adapter()
            job_spec = {
                "run_id": run.id,
                "pipeline_name": pipeline.pipeline_key,
                "pipeline_source": pipeline.source_url,
                "pipeline_version": pipeline.version,
                "parameters": merged_params,
                "sample_sheet": sample_sheet_csv,
                "experiment_id": data.experiment_id,
                "work_dir": run.work_dir,
                "resume_from_run_id": data.resume_from_run_id,
                "input_files": [s.sample_id_unique or str(s.id) for s in samples],
            }
            job_result = await compute_adapter.submit_job(job_spec)

            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.slurm_job_id = job_result.get("job_id", "")
            run.k8s_job_name = job_result.get("job_id", "")
            run.k8s_namespace = job_result.get("namespace", "")

            estimated_cost = job_result.get("estimated_cost", {})
            if estimated_cost:
                run.cost_estimate = estimated_cost.get("estimated_cost_usd")

            import asyncio

            asyncio.create_task(
                event_bus.emit(
                    PIPELINE_STARTED,
                    {
                        "event_type": PIPELINE_STARTED,
                        "org_id": org_id,
                        "user_id": user_id,
                        "target_user_id": user_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "title": f"Pipeline '{pipeline.name}' started",
                        "message": f"Run {run.id} submitted for experiment {data.experiment_id}",
                        "summary": f"Pipeline '{pipeline.name}' run {run.id} started",
                    },
                )
            )

        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            logger.error("Pipeline launch failed for run %d: %s", run.id, e)

            import asyncio

            asyncio.create_task(
                event_bus.emit(
                    PIPELINE_FAILED,
                    {
                        "event_type": PIPELINE_FAILED,
                        "org_id": org_id,
                        "user_id": user_id,
                        "target_user_id": user_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "title": f"Pipeline '{pipeline.name}' failed to launch",
                        "message": str(e),
                        "severity": "critical",
                        "summary": f"Pipeline run {run.id} failed to launch",
                    },
                )
            )

        await session.flush()

        # 11. Update experiment status to "processing"
        try:
            from app.services.experiment_service import ExperimentService

            await ExperimentService.update_status(
                session,
                data.experiment_id,
                org_id,
                user_id,
                "processing",
            )
        except Exception as e:
            # Status transition may not be valid from current state — that's OK
            logger.warning("Could not update experiment status: %s", e)

        # 12. Write audit log
        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_run",
            entity_id=run.id,
            action="launch",
            details={
                "pipeline_key": data.pipeline_key,
                "experiment_id": data.experiment_id,
                "sample_count": len(samples),
                "status": run.status,
            },
        )

        # 13. Best-effort reference linkage from parameter paths
        linked_ref_ids: list[int] = []
        try:
            linked_ref_ids = await PipelineRunService._link_references_from_params(
                session, run.id, org_id, merged_params
            )
        except Exception as e:
            logger.warning("Reference linkage failed for run %d: %s", run.id, e)

        # 14. Auto-populate reference_genome if not explicitly set
        if not run.reference_genome:
            run.reference_genome = await PipelineRunService._resolve_reference_genome(
                session, linked_ref_ids, merged_params
            )
            if run.reference_genome:
                await session.flush()

        return run

    @staticmethod
    async def _resolve_reference_genome(
        session: AsyncSession,
        linked_ref_ids: list[int],
        params: dict,
    ) -> str | None:
        """Resolve reference_genome from linked reference datasets or parameter keys.

        Priority:
        1. First linked reference dataset (name + version)
        2. params["genome"] or params["reference_genome"] fallback
        """
        if linked_ref_ids:
            from app.models.reference_dataset import ReferenceDataset

            result = await session.execute(select(ReferenceDataset).where(ReferenceDataset.id == linked_ref_ids[0]))
            ref = result.scalar_one_or_none()
            if ref:
                return f"{ref.name} {ref.version}"

        # Fallback to parameter keys (reference_genome is more explicit, check first)
        for key in ("reference_genome", "genome"):
            val = params.get(key)
            if val and isinstance(val, str):
                return val

        return None

    @staticmethod
    async def _link_references_from_params(
        session: AsyncSession,
        run_id: int,
        org_id: int,
        params: dict,
    ) -> list[int]:
        """Inspect parameter values for reference data paths and create linkages.

        Best-effort: logs warnings for unresolvable paths, never raises.
        Returns list of linked reference dataset IDs.
        """
        from app.models.reference_dataset import ReferenceDataset, pipeline_run_references

        MOUNT_PREFIX = "/data/references/"
        candidate_paths: list[str] = []

        def _extract_paths(obj: object, depth: int = 0) -> None:
            if depth > 10:
                return
            if isinstance(obj, str) and MOUNT_PREFIX in obj:
                candidate_paths.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _extract_paths(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj:
                    _extract_paths(v, depth + 1)

        _extract_paths(params)

        if not candidate_paths:
            return []

        # Load all active references for this org
        result = await session.execute(
            select(ReferenceDataset).where(
                ReferenceDataset.organization_id == org_id,
            )
        )
        all_refs = list(result.scalars().all())

        linked_ids: list[int] = []
        warnings: list[str] = []

        for path in candidate_paths:
            # Strip mount prefix to get relative path
            idx = path.find(MOUNT_PREFIX)
            relative = path[idx + len(MOUNT_PREFIX) :]

            # Match against gcs_prefix using prefix matching
            matched = None
            for ref in all_refs:
                prefix = ref.gcs_prefix.rstrip("/") + "/"
                if relative.startswith(prefix) or relative.rstrip("/") + "/" == prefix:
                    matched = ref
                    break

            if matched:
                if matched.id not in linked_ids:
                    linked_ids.append(matched.id)
                    await session.execute(
                        pipeline_run_references.insert().values(
                            pipeline_run_id=run_id,
                            reference_dataset_id=matched.id,
                        )
                    )
                    if matched.status == "deprecated":
                        logger.warning(
                            "Run %d uses deprecated reference: %s %s",
                            run_id,
                            matched.name,
                            matched.version,
                        )
            else:
                warnings.append(f"Unresolvable reference path: {path}")

        for w in warnings:
            logger.warning("Run %d: %s", run_id, w)

        return linked_ids

    @staticmethod
    async def cancel_run(session: AsyncSession, run_id: int, user_id: int) -> PipelineRun:
        run = await PipelineRunService.get_run_model(session, run_id)
        if not run:
            raise ValueError("Run not found")

        old_status = run.status

        # Persist logs before killing the pod -- once deleted they're gone
        job_id = run.k8s_job_name or run.slurm_job_id
        if job_id:
            try:
                compute_adapter = get_compute_adapter()
                await compute_adapter.persist_job_logs(job_id)
            except Exception as e:
                logger.warning("Failed to persist logs before cancel for run %d: %s", run_id, e)

            try:
                await compute_adapter.cancel_job(job_id)
            except Exception as e:
                logger.warning("Failed to cancel run %d: %s", run_id, e)

        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_run",
            entity_id=run.id,
            action="cancel",
            details={"status": "cancelled"},
            previous_value={"status": old_status},
        )
        return run

    @staticmethod
    async def get_run_model(session: AsyncSession, run_id: int) -> PipelineRun | None:
        result = await session.execute(
            select(PipelineRun)
            .options(
                selectinload(PipelineRun.experiment),
                selectinload(PipelineRun.submitted_by),
                selectinload(PipelineRun.processes),
                selectinload(PipelineRun.samples),
            )
            .where(PipelineRun.id == run_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_run(session: AsyncSession, run_id: int, org_id: int) -> PipelineRun | None:
        result = await session.execute(
            select(PipelineRun)
            .options(
                selectinload(PipelineRun.experiment),
                selectinload(PipelineRun.submitted_by),
                selectinload(PipelineRun.processes),
                selectinload(PipelineRun.samples),
            )
            .where(PipelineRun.id == run_id, PipelineRun.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_runs(
        session: AsyncSession,
        org_id: int,
        page: int = 1,
        page_size: int = 25,
        experiment_id: int | None = None,
        pipeline_key: str | None = None,
        status: str | None = None,
        submitted_by_user_id: int | None = None,
    ) -> tuple[list[PipelineRun], int]:
        query = (
            select(PipelineRun)
            .options(selectinload(PipelineRun.experiment), selectinload(PipelineRun.submitted_by))
            .where(PipelineRun.organization_id == org_id)
        )
        count_query = select(func.count(PipelineRun.id)).where(PipelineRun.organization_id == org_id)

        if experiment_id:
            query = query.where(PipelineRun.experiment_id == experiment_id)
            count_query = count_query.where(PipelineRun.experiment_id == experiment_id)
        if pipeline_key:
            query = query.where(PipelineRun.pipeline_name == pipeline_key)
            count_query = count_query.where(PipelineRun.pipeline_name == pipeline_key)
        if status:
            query = query.where(PipelineRun.status == status)
            count_query = count_query.where(PipelineRun.status == status)
        if submitted_by_user_id:
            query = query.where(PipelineRun.submitted_by_user_id == submitted_by_user_id)
            count_query = count_query.where(PipelineRun.submitted_by_user_id == submitted_by_user_id)

        query = query.order_by(PipelineRun.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(query)
        runs = list(result.scalars().all())

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return runs, total

    @staticmethod
    async def compare_runs(session: AsyncSession, run_ids: list[int]) -> dict:
        """Compare parameters across multiple runs."""
        result = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.experiment), selectinload(PipelineRun.submitted_by))
            .where(PipelineRun.id.in_(run_ids))
        )
        runs = list(result.scalars().all())

        # Compute parameter diffs
        all_keys: set[str] = set()
        for run in runs:
            if run.parameters_json:
                all_keys.update(run.parameters_json.keys())

        diffs = {}
        for key in sorted(all_keys):
            values = []
            for run in runs:
                val = (run.parameters_json or {}).get(key)
                values.append(val)
            if len(set(str(v) for v in values)) > 1:
                diffs[key] = {str(run.id): (run.parameters_json or {}).get(key) for run in runs}

        return {"runs": runs, "parameter_diffs": diffs}

    @staticmethod
    async def reproduce_run(session: AsyncSession, original_run_id: int, user_id: int) -> PipelineRun:
        """Re-launch with identical parameters."""
        original = await PipelineRunService.get_run_model(session, original_run_id)
        if not original:
            raise ValueError("Original run not found")

        # Reconstruct launch request from original
        sample_ids = [s.id for s in original.samples] if original.samples else None

        # Find the pipeline_key from catalog
        pipeline_key = original.pipeline_name

        data = PipelineRunLaunchRequest(
            pipeline_key=pipeline_key,
            experiment_id=original.experiment_id,
            sample_ids=sample_ids,
            parameters=original.parameters_json or {},
            resume_from_run_id=original_run_id,
        )

        new_run = await PipelineRunService.launch_run(
            session,
            original.organization_id,
            user_id,
            data,
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_run",
            entity_id=new_run.id,
            action="reproduce",
            details={"original_run_id": original_run_id},
        )

        return new_run

    @staticmethod
    async def export_provenance(session: AsyncSession, run_id: int) -> dict:
        """Export complete provenance for a run."""
        run = await PipelineRunService.get_run_model(session, run_id)
        if not run:
            raise ValueError("Run not found")

        return {
            "run_id": run.id,
            "pipeline_name": run.pipeline_name,
            "pipeline_version": run.pipeline_version,
            "parameters": run.parameters_json,
            "input_files": run.input_files_json,
            "output_files": run.output_files_json,
            "container_versions": run.container_versions_json,
            "experiment": {
                "id": run.experiment.id,
                "name": run.experiment.name,
            }
            if run.experiment
            else None,
            "samples": [
                {"id": s.id, "sample_id_unique": s.sample_id_unique, "organism": s.organism}
                for s in (run.samples or [])
            ],
            "submitted_by": {
                "id": run.submitted_by.id,
                "name": run.submitted_by.name,
                "email": run.submitted_by.email,
            }
            if run.submitted_by
            else None,
            "status": run.status,
            "work_dir": run.work_dir,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }
