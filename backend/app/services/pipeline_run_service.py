import logging
from datetime import datetime, timezone

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    SamplesMissingFilesError,
    ValidationError,
)
from app.models.experiment import Experiment
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
from app.services.samplesheet_declaration import parse_declaration
from app.services.samplesheet_mapping_service import SamplesheetMappingService
from app.adapters.registry import get_compute_adapter
from app.services.vocabulary_validator import VocabularyValidator

logger = logging.getLogger("bioaf.pipeline_runs")

# MACS mappable ("effective") genome size per reference build, for peak-calling pipelines
# (chipseq/atacseq). These are the standard MACS values: human ~2.7e9, mouse ~1.87e9. Keyed by the
# controlled reference_genome vocabulary (+ common UCSC aliases). Unknown builds are left unset so
# nf-core surfaces its own "specify --read_length or --macs_gsize" guidance rather than a wrong size.
_MACS_GSIZE_BY_GENOME: dict[str, float] = {
    "GRCh38": 2.7e9,
    "GRCh37": 2.7e9,
    "hg38": 2.7e9,
    "hg19": 2.7e9,
    "T2T-CHM13": 3.03e9,
    "GRCm39": 1.87e9,
    "GRCm38": 1.87e9,
    "mm10": 1.87e9,
}


class PipelineRunService:
    @staticmethod
    def _requires_per_sample_fastq(pipeline, contract=None) -> bool:
        """Whether this pipeline consumes per-sample FASTQ input.

        The pipeline's own samplesheet contract answers this exactly, so it wins
        when we have one. The name-based heuristic below is the fallback for the
        pipelines that publish no contract, and it is wrong in a specific way:
        it returns True for EVERY nf-core pipeline, so it demands FASTQ files
        for assembly, variant and imaging pipelines that never read them.
        """
        if contract is not None and not contract.is_empty:
            return contract.is_sample_launchable

        key = (getattr(pipeline, "pipeline_key", "") or "").lower()
        if "fetchngs" in key:
            return False
        if "rnaseq" in key or "scrnaseq" in key:
            return True
        return (getattr(pipeline, "source_type", "") or "").lower() == "nf-core"

    @staticmethod
    async def preflight(session, org_id: int, data) -> dict:
        """Whether this launch would succeed, and what it would submit.

        Runs the same checks ``launch_run`` runs, so the dialog can say what is
        wrong while the user can still fix it. Returns the failure instead of
        raising, because a pipeline that cannot run is an answer here, not an
        error: the caller asked a question.

        Also answers the questions the launch flow's later steps need: the sheet
        this run would hand to Nextflow, the columns an entry grid must collect,
        and what a saved design would contribute. The first two come from the
        same computation as the verdict, so the review step cannot confirm a
        sheet other than the one about to run, and the grid cannot ask for
        something different from what the block reports.

        The third is reported ALONGSIDE the sheet and never folded into it. A
        saved design is an offer: it fills the grid, the scientist confirms it,
        and it reaches the sheet because they sent it back. Applying it here
        would carry a design that fitted six samples silently onto twelve.
        """
        pipeline = await PipelineCatalogService.get_pipeline(session, org_id, data.pipeline_key)
        if not pipeline:
            raise NotFoundError(f"Pipeline {data.pipeline_key} not found")

        sample_query = (
            select(Sample)
            .where(Sample.experiment_id == data.experiment_id)
            .options(selectinload(Sample.files), selectinload(Sample.custom_fields))
        )
        if data.sample_ids:
            sample_query = sample_query.where(Sample.id.in_(data.sample_ids))
        samples = list((await session.execute(sample_query)).scalars().all())
        for sample in samples:
            # Resolved with the SAME opt-in the launch will use. Hardcoding this
            # off previewed a sheet with empty read columns and then submitted
            # populated ones, so the scientist approved a sheet that was not the
            # one that ran, which is the single property the review step exists
            # to provide.
            sample._input_files = PipelineRunService._input_eligible_files(
                sample.files, getattr(data, "include_derived_inputs", False)
            )

        contract, mapping, scope = await PipelineRunService._effective_contract(
            session, pipeline, org_id, getattr(data, "experiment_id", None), getattr(data, "columns", None)
        )
        # Whether a declaration is what defines this sheet at all. True only for
        # a pipeline that publishes no contract AND has no tailored generator:
        # declaring columns for chipseq would be collecting an answer that is
        # then ignored, because its generator builds a sheet a schema cannot
        # describe.
        declarable = await PipelineRunService._is_declarable(session, pipeline)
        parameters = data.parameters or {}
        sample_values = getattr(data, "sample_values", None) or {}

        # Built for every pipeline, including the ones a schema does not
        # describe: design section 6 puts the review step on EVERY launch, and a
        # tailored generator's sheet needs reviewing as much as a derived one.
        preview = SampleSheetService.preview(
            pipeline.pipeline_key, samples, parameters, contract=contract, sample_values=sample_values
        )

        verdict: dict = {"can_launch": True, "code": None, "reason": None, "details": {}}
        inputs: list[dict] = []
        if not SampleSheetService.has_handwritten_generator(pipeline.pipeline_key):
            inputs = SampleSheetService.per_sample_inputs(contract, samples, parameters, sample_values)
            try:
                SampleSheetService.check_contract_satisfiable(contract, samples, parameters, sample_values)
            except DomainError as exc:
                verdict = {
                    "can_launch": False,
                    "code": exc.code,
                    "reason": str(exc),
                    "details": exc.details,
                }

        carried = SamplesheetMappingService.flatten(mapping)
        prefill = {
            "scope": scope,
            "values": carried,
            "bindings": SamplesheetMappingService.flatten_bindings(mapping),
            # The declared columns travel with the design, so the editor opens
            # on what is in force rather than on an empty sheet.
            "columns": SamplesheetMappingService.declared_columns(mapping),
            # Selected samples the saved design does not name. Adding samples and
            # re-running is normal, and a grouping that was right for six may be
            # wrong for twelve, so these are reported rather than left to look
            # answered.
            "samples_without_values": [s.id for s in samples if str(s.id) not in carried],
        }

        return {
            **verdict,
            "samplesheet": preview,
            "per_sample_inputs": inputs,
            "prefill": prefill,
            # What the column editor needs: whether this pipeline is one whose
            # sheet a scientist declares, and the vocabulary to bind against.
            # Offered from what these samples actually carry, because a file
            # type typed from memory binds to nothing and the column then blocks
            # the launch with no hint as to why.
            "declaration": {
                "declarable": declarable,
                "file_types": sorted(
                    {(f.file_type or "").strip() for s in samples for f in (s._input_files or []) if f.file_type}
                ),
                "custom_fields": sorted(
                    {
                        (field.field_name or "").strip()
                        for s in samples
                        for field in (getattr(s, "custom_fields", None) or [])
                        if field.field_name
                    }
                ),
            },
        }

    @staticmethod
    async def _is_declarable(session, pipeline) -> bool:
        """Whether this pipeline's samplesheet is one a scientist declares.

        Only when it publishes no contract of its own and no tailored generator
        owns it. Both halves matter: a published contract already says what the
        columns are, and a tailored generator builds a sheet the schema cannot
        describe, so a declaration for either would be an answer bioAF collects
        and then ignores.
        """
        if SampleSheetService.has_handwritten_generator(pipeline.pipeline_key):
            return False
        published = await PipelineRunService._resolve_contract(session, pipeline)
        return published.is_empty

    @staticmethod
    async def _effective_contract(
        session, pipeline, org_id: int, experiment_id: int | None, declared: list[dict] | None = None
    ):
        """The contract this launch is judged against, and the design it carries.

        A pipeline's own ``schema_input.json`` when it publishes one. When it
        does not, the columns a scientist DECLARED, which is the only statement
        about the sheet that exists for the seventeen pipelines that publish
        nothing.

        Resolved once and used for the preflight and the launch alike. Two
        resolutions would let the review step confirm a sheet other than the one
        that runs, which is the single property that step exists to provide.

        ``declared`` is the declaration ON SCREEN, and it outranks what is saved
        for exactly that reason: a scientist editing a saved sheet and launching
        without re-saving would otherwise review one sheet and run another. It is
        NOT saved by being used, so nothing is promoted by launching. ``None``
        means the caller said nothing about columns and the saved design stands;
        an empty list means the editor was cleared, which is a statement and
        means the generic sheet.
        """
        contract = await PipelineRunService._resolve_contract(session, pipeline)
        mapping, scope = await SamplesheetMappingService.resolve(session, org_id, pipeline.pipeline_key, experiment_id)
        if contract.is_empty and not SampleSheetService.has_handwritten_generator(pipeline.pipeline_key):
            declared = SamplesheetMappingService.declared_columns(mapping) if declared is None else declared
            if declared:
                # Refused at save time, so this cannot normally raise. If a
                # stored declaration is somehow unparseable, fall back to
                # today's generic sheet rather than failing the launch: an
                # unlaunchable pipeline is worse than an un-customised one.
                try:
                    contract = parse_declaration({"fields": declared})
                except ValueError:
                    logger.warning("Ignoring an unparseable samplesheet declaration for %s", pipeline.pipeline_key)
        return contract, mapping, scope

    @staticmethod
    async def _resolve_contract(session, pipeline):
        """The pipeline's samplesheet contract, kept in step with the version installed.

        Entries installed before the contract existed carry NULL, so the first
        launch resolves and persists it. A fetch failure records nothing and
        returns an empty contract, which every caller treats as "we do not
        know" and falls back on.

        The contract is also re-fetched when the pipeline's VERSION MOVES. Until
        it was, a contract fetched at install stayed pinned to the tag current
        then, so an upgraded pipeline was validated against its old rules: still
        requiring a column that had been dropped, still blind to one that had
        been added. Re-fetching per launch was rejected (a network call on the
        launch path, and runs that fail when GitHub is unreachable), as were a
        background schedule (drift found late) and a manual button (an upgraded
        pipeline stays wrong until somebody remembers).

        A version we have no record for is assumed current and simply stamped.
        Treating it as a mismatch would re-fetch the whole catalog on its next
        launch to correct a drift that may not exist.
        """
        from app.services.samplesheet_schema import is_absent_marker, parse_contract

        stored = getattr(pipeline, "input_schema_json", None)
        version = getattr(pipeline, "version", None)
        fetched_for = getattr(pipeline, "input_schema_version", None)
        is_nf_core = (getattr(pipeline, "source_type", "") or "").lower() == "nf-core"

        if stored is None and is_nf_core:
            fetched = await PipelineCatalogService.fetch_input_schema(getattr(pipeline, "source_url", None), version)
            if fetched is not None:
                pipeline.input_schema_json = fetched
                pipeline.input_schema_version = version
                await session.flush()
                stored = fetched
        elif stored is not None and fetched_for is None:
            pipeline.input_schema_version = version
            await session.flush()
        elif stored is not None and is_nf_core and fetched_for != version:
            fetched = await PipelineCatalogService.fetch_input_schema(getattr(pipeline, "source_url", None), version)
            # A failed fetch leaves both the contract and the recorded version
            # alone. Advancing the version here would record a refresh that did
            # not happen, and the pipeline would stay wrong until it moved again.
            if fetched is not None:
                pipeline.input_schema_json = fetched
                pipeline.input_schema_version = version
                await session.flush()
                stored = fetched

        if stored is None or is_absent_marker(stored):
            return parse_contract(None)
        return parse_contract(stored)

    # File source types that are pipeline/notebook DERIVATIVES, not raw inputs.
    # By default these are excluded from a run's inputs so a previous run's
    # outputs are never fed back in as inputs (which compounds every run).
    DERIVED_SOURCE_TYPES: frozenset[str] = frozenset({"pipeline_output", "notebook_output"})

    @staticmethod
    def _input_eligible_files(files: list, include_derived: bool) -> list:
        """Filter a sample's files to those eligible as pipeline inputs.

        Raw uploads are always eligible. Derived files (a prior pipeline or
        notebook run's outputs) are excluded unless the caller opts in via
        ``include_derived_inputs`` on the launch request.
        """
        # A deleted file is never an input, whatever else it qualifies as. The
        # row survives so its identity keeps resolving and no provenance record
        # dangles, but feeding a run a file its scientist believes is gone is a
        # scientific error rather than a tidiness one.
        files = [f for f in (files or []) if getattr(f, "deleted_at", None) is None]
        if include_derived:
            return list(files)
        return [
            f
            for f in files
            if (getattr(f, "source_type", "") or "upload") not in PipelineRunService.DERIVED_SOURCE_TYPES
        ]

    @staticmethod
    async def launch_run(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: PipelineRunLaunchRequest,
        *,
        via_assistant: bool = False,
    ) -> PipelineRun:
        """Launch a pipeline run: the core orchestration method."""
        # 1. Load pipeline from catalog
        pipeline = await PipelineCatalogService.get_pipeline(session, org_id, data.pipeline_key)
        if not pipeline:
            raise ValidationError(f"Pipeline '{data.pipeline_key}' not found or not enabled")

        # 2. Load experiment
        exp_result = await session.execute(
            select(Experiment).where(
                Experiment.id == data.experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        experiment = exp_result.scalar_one_or_none()
        if not experiment:
            raise ValidationError(f"Experiment {data.experiment_id} not found")

        # 3. Resolve sample_ids
        if data.sample_ids:
            sample_result = await session.execute(
                select(Sample)
                .where(
                    Sample.id.in_(data.sample_ids),
                    Sample.experiment_id == data.experiment_id,
                )
                .options(selectinload(Sample.files), selectinload(Sample.custom_fields))
            )
            samples = list(sample_result.scalars().all())
            if len(samples) != len(data.sample_ids):
                raise ValidationError("Some sample IDs do not belong to this experiment")
        else:
            sample_result = await session.execute(
                select(Sample)
                .where(Sample.experiment_id == data.experiment_id)
                .options(selectinload(Sample.files), selectinload(Sample.custom_fields))
            )
            samples = list(sample_result.scalars().all())

        # 3a. Resolve each sample's INPUT-eligible files. Prior pipeline/notebook
        # outputs are excluded by default so they are never fed back in as inputs
        # (which compounded the dataset every run); include_derived_inputs opts in.
        # Stored on a transient attribute the sample-sheet builder reads; it is
        # not a mapped column, so it never touches the ORM/DB.
        for s in samples:
            s._input_files = PipelineRunService._input_eligible_files(s.files, data.include_derived_inputs)

        # 3b. A FASTQ-consuming pipeline needs every selected sample to have its
        # own input files. Reject (or, if asked, drop) samples with none. The old
        # behaviour back-filled file-less samples with the WHOLE experiment's
        # files, which cross-contaminated one sample's run with another's reads.
        # 3c. The samplesheet contract, which decides both whether this pipeline
        # reads FASTQ at all and whether bioAF can fill every required column.
        # The pipeline's own when it publishes one, and the columns a scientist
        # declared for this experiment when it does not.
        contract, _mapping, _scope = await PipelineRunService._effective_contract(
            session, pipeline, org_id, data.experiment_id, getattr(data, "columns", None)
        )

        if PipelineRunService._requires_per_sample_fastq(pipeline, contract):
            missing = [s for s in samples if not s._input_files]
            if missing:
                if not data.drop_samples_without_files:
                    raise SamplesMissingFilesError(
                        "Some selected samples have no linked input files",
                        details={
                            "samples_without_files": [{"id": s.id, "external_id": s.external_id} for s in missing]
                        },
                    )
                samples = [s for s in samples if s._input_files]
                if not samples:
                    raise ValidationError("All selected samples lack input files; nothing to run")

        # 4. Check quota
        allowed, message = await QuotaService.check_quota(session, user_id, estimated_hours=2.0)
        if not allowed:
            raise ConflictError(f"Quota exceeded: {message}")

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

        # reference_genome is the first-class control for the nf-core iGenomes `--genome` key. Translate
        # it into the param so pipelines that don't hardcode a `genome` default still receive it: the
        # built-in rnaseq defaults file sets genome, but registry-installed pipelines (chipseq, atacseq,
        # ...) do not, so without this they launch with no genome and fail validation ("Missing --fasta").
        # An explicit `genome` param (from defaults or the caller) wins.
        if data.reference_genome and "genome" not in merged_params:
            merged_params["genome"] = data.reference_genome

        # Peak-calling pipelines (chipseq/atacseq) need the mappable genome size for MACS; nf-core
        # fails ("specify --read_length or --macs_gsize") when neither is set and iGenomes doesn't
        # supply it. Derive macs_gsize from the genome so these runs work out of the box; an explicit
        # macs_gsize/read_length wins.
        if any(k in pipeline.pipeline_key for k in ("chipseq", "atacseq")):
            if "macs_gsize" not in merged_params and "read_length" not in merged_params:
                gsize = _MACS_GSIZE_BY_GENOME.get(merged_params.get("genome"))
                if gsize is not None:
                    merged_params["macs_gsize"] = gsize

        # 6b. Refuse a launch that cannot produce a valid samplesheet, while it
        # is still free to do so. Deliberately BEFORE the run row, the sample
        # linkage and the compute call: the failure this replaces is a run that
        # scales up a node, pulls containers, and dies inside Nextflow on a
        # schema error the user did not write. Pipelines with a hand-written
        # generator are exempt, because those build a sheet the schema alone
        # cannot describe (chipseq's control detection, fetchngs' accession list).
        # The values the scientist stated, which are what the preview showed them
        # and therefore what they approved. Checked and emitted from the same
        # set, or the run submits a sheet other than the one confirmed.
        sample_values = data.sample_values or {}

        if not SampleSheetService.has_handwritten_generator(pipeline.pipeline_key):
            SampleSheetService.check_contract_satisfiable(contract, samples, merged_params, sample_values)

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

        # 7b. Record input files in junction table (ADR-038). Use the
        # input-eligible set so the recorded provenance matches what actually
        # fed the run (raw inputs, not re-ingested prior outputs).
        seen_file_ids: set[int] = set()
        for sample in samples:
            for f in sample._input_files or []:
                if f.id not in seen_file_ids:
                    session.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=f.id))
                    seen_file_ids.add(f.id)
        if seen_file_ids:
            # Also populate input_files_json for backward compat
            run.input_files_json = sorted(seen_file_ids)
            await session.flush()

        # 8. Generate the sample sheet, through the same call the review step
        # used. One computation produces the CSV, the rows (each naming the
        # sample it belongs to) and the column holding the name, so the sheet
        # that runs and the record of what it emitted cannot disagree about
        # either.
        sheet = SampleSheetService.preview(
            pipeline.pipeline_key,
            samples,
            merged_params,
            contract=contract,
            sample_values=sample_values,
        )
        sample_sheet_csv = sheet["csv"]

        # 8b. Keep what this run was actually given. Re-deriving the sheet later
        # reads today's samples, today's files and today's mapping, none of which
        # are what the run received, so defending a result means holding the
        # sheet itself. The design is snapshotted alongside it, with the stamps
        # that say who set each value, because whoever fills the design grid is
        # often not whoever launches.
        run.samplesheet_csv = sample_sheet_csv
        # What this run put in the identity column, and which asset each of those
        # names stood for. This is what lets a later output be matched against
        # the name THIS RUN EMITTED rather than the name its sample happens to
        # carry by then, which is the divergence that used to attribute a file to
        # nobody. The annotated CSV is the same fact for a person to read; the
        # UID column is in that copy alone and never in the sheet submitted,
        # because an undeclared column fails nf-schema for the whole sheet.
        identity = SampleSheetService.identity_snapshot(sheet, samples)
        run.samplesheet_snapshot_csv = identity["csv"]
        run.samplesheet_emitted_json = identity["emitted"]
        run.samplesheet_mapping_json = SamplesheetMappingService.snapshot(sample_values, None, user_id)

        # 8c. A value the scientist stated about the SAMPLE belongs on the
        # sample. Which ones those are is decided by the sheet service, which
        # owns the column-to-field map: a column the pipeline constrains is an
        # accommodation and stays on the run, and a field that already holds a
        # value is never overwritten.
        field_updates = SampleSheetService.sample_field_updates(contract, samples, sample_values)
        for sample in samples:
            for field, value in field_updates.get(sample.id, {}).items():
                setattr(sample, field, value)

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
                "input_files": [s.external_id or str(s.id) for s in samples],
            }
            job_result = await compute_adapter.submit_job(job_spec)

            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.slurm_job_id = job_result.job_id
            run.k8s_job_name = job_result.job_id
            run.k8s_namespace = job_result.provider_details.get("namespace", "")
            # Backend-neutral handle + provider detail (BAL Phase 4); dual-written
            # alongside k8s_* until the old columns are dropped.
            run.compute_job_ref = job_result.job_id
            run.provider_metadata = {
                k: v
                for k, v in {"job_name": job_result.job_id, **(job_result.provider_details or {})}.items()
                if v is not None
            }

            if job_result.estimated_cost:
                run.cost_estimate = job_result.estimated_cost.estimated_cost_usd

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
            # Status transition may not be valid from current state: that's OK
            logger.warning("Could not update experiment status: %s", e)

        # 12. Write audit log
        launch_details: dict[str, object] = {
            "pipeline_key": data.pipeline_key,
            "experiment_id": data.experiment_id,
            "sample_count": len(samples),
            "status": run.status,
        }
        if via_assistant:
            launch_details["via_assistant"] = True
        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_run",
            entity_id=run.id,
            action="launch",
            details=launch_details,
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
            raise NotFoundError("Run not found")

        old_status = run.status

        # Persist logs before killing the pod -- once deleted they're gone
        job_id = run.compute_job_ref or run.slurm_job_id
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

    # Sortable columns, by the name the API exposes.
    #
    # An allowlist rather than a getattr: a sort field arrives as user input and
    # ends up as a column in ORDER BY, so anything not named here must not be
    # reachable. Restricted to what a user can already see in the list, which is
    # also exactly what the table offers as a sortable header.
    SORTABLE = {
        "id": PipelineRun.id,
        "status": PipelineRun.status,
        "pipeline_name": PipelineRun.pipeline_name,
        "created_at": PipelineRun.created_at,
        "started_at": PipelineRun.started_at,
        "completed_at": PipelineRun.completed_at,
    }

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
        sort_by: str | None = None,
        sort_dir: str = "desc",
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

        # The ORDER BY goes before the LIMIT, which is the whole point: sorting
        # the rows a page already holds answers a different question from the
        # one the user asked. `id` is the tiebreaker so paging is stable across
        # requests when the sort column has duplicates (status, especially).
        column = PipelineRunService.SORTABLE.get(sort_by) if sort_by else None
        if column is None:
            column = PipelineRun.created_at
        direction = column.asc() if sort_dir == "asc" else column.desc()
        query = query.order_by(direction, PipelineRun.id.desc())
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
    async def reproduce_run(
        session: AsyncSession,
        original_run_id: int,
        user_id: int,
        drop_samples_without_files: bool = False,
    ) -> PipelineRun:
        """Re-launch with identical parameters.

        Reproduce replays the original's samples through ``launch_run``, so it is
        subject to the same per-sample file requirement: a sample that has since
        lost its files raises SamplesMissingFilesError unless the caller opts to
        drop the offending samples.
        """
        original = await PipelineRunService.get_run_model(session, original_run_id)
        if not original:
            raise NotFoundError("Original run not found")

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
            drop_samples_without_files=drop_samples_without_files,
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
    async def _resolve_input_files(session: AsyncSession, run) -> list[dict]:
        """Resolve a run's input files to human-readable provenance records.

        The provenance tab previously showed bare file IDs, which are
        meaningless to a user. Resolve each input file to its project,
        experiment, sample, and filename. Prefers the pipeline_run_input_files
        junction (ADR-038); falls back to the legacy input_files_json id list.
        """
        file_ids = [
            row[0]
            for row in (
                await session.execute(
                    text("SELECT file_id FROM pipeline_run_input_files WHERE pipeline_run_id = :rid"),
                    {"rid": run.id},
                )
            ).fetchall()
        ]
        if not file_ids:
            file_ids = list(run.input_files_json or [])
        if not file_ids:
            return []

        rows = (
            (
                await session.execute(
                    text(
                        "SELECT f.id AS file_id, f.filename, "
                        "       e.id AS experiment_id, e.name AS experiment_name, "
                        "       p.id AS project_id, p.name AS project_name, "
                        "       s.id AS sample_id, s.external_id AS sample_external_id "
                        "FROM files f "
                        "LEFT JOIN experiments e ON e.id = f.experiment_id "
                        "LEFT JOIN projects p ON p.id = COALESCE(f.project_id, e.project_id) "
                        "LEFT JOIN sample_files sf ON sf.file_id = f.id "
                        "LEFT JOIN samples s ON s.id = sf.sample_id "
                        "WHERE f.id = ANY(:ids)"
                    ).bindparams(ids=file_ids)
                )
            )
            .mappings()
            .all()
        )

        # One record per file; a file may link to several samples.
        by_file: dict[int, dict] = {}
        for r in rows:
            rec = by_file.get(r["file_id"])
            if rec is None:
                rec = {
                    "file_id": r["file_id"],
                    "filename": r["filename"],
                    "project": {"id": r["project_id"], "name": r["project_name"]} if r["project_id"] else None,
                    "experiment": {"id": r["experiment_id"], "name": r["experiment_name"]}
                    if r["experiment_id"]
                    else None,
                    "samples": [],
                }
                by_file[r["file_id"]] = rec
            if r["sample_id"] and not any(s["id"] == r["sample_id"] for s in rec["samples"]):
                rec["samples"].append({"id": r["sample_id"], "external_id": r["sample_external_id"]})

        # Preserve the input id ordering; include any ids that resolved to no row.
        ordered = [by_file[fid] for fid in file_ids if fid in by_file]
        return ordered

    @staticmethod
    async def export_provenance(session: AsyncSession, run_id: int) -> dict:
        """Export complete provenance for a run."""
        run = await PipelineRunService.get_run_model(session, run_id)
        if not run:
            raise NotFoundError("Run not found")

        return {
            "run_id": run.id,
            "pipeline_name": run.pipeline_name,
            "pipeline_version": run.pipeline_version,
            "parameters": run.parameters_json,
            "input_files": await PipelineRunService._resolve_input_files(session, run),
            "output_files": run.output_files_json,
            "container_versions": run.container_versions_json,
            "experiment": {
                "id": run.experiment.id,
                "name": run.experiment.name,
            }
            if run.experiment
            else None,
            "samples": [
                {"id": s.id, "external_id": s.external_id, "organism": s.organism} for s in (run.samples or [])
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
