"""Queries DB for all provenance data needed to build reports."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.batch import Batch
from app.models.experiment import Experiment
from app.models.experiment_custom_field import ExperimentCustomField
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.project_sample import ProjectSample
from app.models.sample import Sample
from app.models.user import User
from app.services.provenance.schema import (
    ArtifactProvenanceData,
    ExperimentProvenanceData,
    PipelineRunProvenanceData,
    ProjectProvenanceData,
    ProvenanceData,
    SampleProvenanceData,
)


def _dt(val: datetime | date | None) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return val.isoformat()


def _dec(val: Decimal | None) -> float | None:
    if val is None:
        return None
    return float(val)


async def _user_map(session: AsyncSession, user_ids: set[int | None]) -> dict[int, dict[str, Any]]:
    """Resolve a set of user IDs to {id, email, name} dicts."""
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    result = await session.execute(select(User).where(User.id.in_(ids)))
    users = result.scalars().all()
    return {u.id: {"id": u.id, "email": u.email, "name": getattr(u, "name", None)} for u in users}


def _user_ref(user_map: dict[int, dict[str, Any]], user_id: int | None) -> dict[str, Any] | None:
    if user_id is None:
        return None
    return user_map.get(user_id)


async def _audit_entries(
    session: AsyncSession,
    entity_types_ids: list[tuple[str, int]],
    user_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load audit log entries for a list of (entity_type, entity_id) pairs."""
    if not entity_types_ids:
        return []
    # Build OR conditions
    conditions = []
    for etype, eid in entity_types_ids:
        conditions.append(f"(entity_type = '{etype}' AND entity_id = {eid})")
    where_clause = " OR ".join(conditions)
    result = await session.execute(
        text(f"SELECT * FROM audit_log WHERE {where_clause} ORDER BY timestamp")  # noqa: S608
    )
    rows = result.mappings().all()
    entries = []
    for row in rows:
        user_info = _user_ref(user_map, row.get("user_id"))
        entries.append(
            {
                "id": row["id"],
                "timestamp": _dt(row["timestamp"]),
                "user": user_info,
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "action": row["action"],
                "details": row.get("details_json"),
            }
        )
    return entries


class ProvenanceDataGatherer:
    @staticmethod
    async def gather(
        session: AsyncSession,
        entity_type: str,
        entity_id: int,
        org_id: int,
    ) -> ProvenanceData:
        """Dispatch to the appropriate gatherer based on entity type."""
        gatherers: dict[str, Any] = {
            "project": ProvenanceDataGatherer.gather_project,
            "experiment": ProvenanceDataGatherer.gather_experiment,
            "sample": ProvenanceDataGatherer.gather_sample,
            "pipeline_run": ProvenanceDataGatherer.gather_pipeline_run,
            "artifact": ProvenanceDataGatherer.gather_artifact,
        }
        gatherer = gatherers.get(entity_type)
        if not gatherer:
            raise ValueError(f"Unknown entity type: {entity_type}")
        return await gatherer(session, entity_id, org_id)

    @staticmethod
    async def gather_project(session: AsyncSession, project_id: int, org_id: int) -> ProjectProvenanceData:
        result = await session.execute(
            select(Project).where(Project.id == project_id, Project.organization_id == org_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            return ProjectProvenanceData()

        # Collect user IDs for batch resolution
        user_ids: set[int | None] = {project.owner_user_id, project.created_by_user_id}

        # Experiments
        exp_result = await session.execute(select(Experiment).where(Experiment.project_id == project_id))
        experiments = exp_result.scalars().all()
        for exp in experiments:
            user_ids.add(exp.owner_user_id)

        # Samples via project_samples
        ps_result = await session.execute(
            select(ProjectSample, Sample)
            .join(Sample, Sample.id == ProjectSample.sample_id)
            .where(ProjectSample.project_id == project_id)
        )
        ps_rows = ps_result.all()

        # Also samples from experiments
        exp_ids = [e.id for e in experiments]
        all_samples: list[Sample] = []
        if exp_ids:
            sample_result = await session.execute(select(Sample).where(Sample.experiment_id.in_(exp_ids)))
            all_samples = list(sample_result.scalars().all())

        # Dedupe samples
        seen_sample_ids: set[int] = set()
        sample_list: list[Sample] = []
        for _, s in ps_rows:
            if s.id not in seen_sample_ids:
                sample_list.append(s)
                seen_sample_ids.add(s.id)
        for s in all_samples:
            if s.id not in seen_sample_ids:
                sample_list.append(s)
                seen_sample_ids.add(s.id)

        # Pipeline runs
        run_result = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.references))
            .where(PipelineRun.project_id == project_id)
        )
        project_runs = list(run_result.scalars().all())

        if exp_ids:
            exp_run_result = await session.execute(
                select(PipelineRun)
                .options(selectinload(PipelineRun.references))
                .where(PipelineRun.experiment_id.in_(exp_ids), PipelineRun.project_id.is_(None))
            )
            project_runs.extend(exp_run_result.scalars().all())

        for run in project_runs:
            user_ids.add(run.submitted_by_user_id)
            user_ids.add(run.reviewed_by_user_id)

        # Files
        file_result = (
            await session.execute(select(File).where(File.organization_id == org_id, File.experiment_id.in_(exp_ids)))
            if exp_ids
            else None
        )
        files = list(file_result.scalars().all()) if file_result else []
        for f in files:
            user_ids.add(f.uploader_user_id)

        # Snapshots
        snap_result = await session.execute(select(AnalysisSnapshot).where(AnalysisSnapshot.project_id == project_id))
        snapshots = snap_result.scalars().all()

        # References (from runs)
        references_list: list[dict[str, Any]] = []
        seen_refs: set[int] = set()
        for run in project_runs:
            for ref in run.references or []:
                if ref.id not in seen_refs:
                    references_list.append(
                        {
                            "id": ref.id,
                            "name": ref.name,
                            "version": ref.version,
                            "category": ref.category,
                        }
                    )
                    seen_refs.add(ref.id)

        # Resolve users
        user_map = await _user_map(session, user_ids)

        # Audit trail
        audit_pairs: list[tuple[str, int]] = [("project", project_id)]
        for exp in experiments:
            audit_pairs.append(("experiment", exp.id))
        audit_trail = await _audit_entries(session, audit_pairs, user_map)

        return ProjectProvenanceData(
            project={
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "hypothesis": project.hypothesis,
                "owner": _user_ref(user_map, project.owner_user_id),
                "organization_id": project.organization_id,
                "created_at": _dt(project.created_at),
                "updated_at": _dt(project.updated_at),
            },
            experiments=[
                {
                    "id": e.id,
                    "name": e.name,
                    "status": e.status,
                    "sample_count": len([s for s in sample_list if s.experiment_id == e.id]),
                }
                for e in experiments
            ],
            samples=[
                {
                    "id": s.id,
                    "external_id": s.sample_id_external,
                    "organism": s.organism,
                    "tissue_type": s.tissue_type,
                    "qc_status": s.qc_status,
                    "experiment_id": s.experiment_id,
                }
                for s in sample_list
            ],
            pipeline_runs=[
                {
                    "id": r.id,
                    "pipeline_name": r.pipeline_name,
                    "pipeline_version": r.pipeline_version,
                    "status": r.status,
                    "submitted_by": _user_ref(user_map, r.submitted_by_user_id),
                    "started_at": _dt(r.started_at),
                    "completed_at": _dt(r.completed_at),
                }
                for r in project_runs
            ],
            files=[
                {
                    "id": f.id,
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "size_bytes": f.size_bytes,
                    "md5": f.md5_checksum,
                    "sha256": f.sha256_checksum,
                    "source_type": f.source_type,
                    "gcs_uri": f.gcs_uri,
                }
                for f in files
            ],
            references=references_list,
            snapshots=[
                {
                    "id": snap.id,
                    "label": snap.label,
                    "object_type": snap.object_type,
                    "created_at": _dt(snap.created_at),
                }
                for snap in snapshots
            ],
            audit_trail=audit_trail,
        )

    @staticmethod
    async def gather_experiment(session: AsyncSession, experiment_id: int, org_id: int) -> ExperimentProvenanceData:
        result = await session.execute(
            select(Experiment).where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
        )
        experiment = result.scalar_one_or_none()
        if not experiment:
            return ExperimentProvenanceData()

        user_ids: set[int | None] = {experiment.owner_user_id}

        # Samples
        sample_result = await session.execute(select(Sample).where(Sample.experiment_id == experiment_id))
        samples = sample_result.scalars().all()

        # Batches
        batch_result = await session.execute(select(Batch).where(Batch.experiment_id == experiment_id))
        batches = batch_result.scalars().all()
        for b in batches:
            user_ids.add(b.operator_user_id)

        # Pipeline runs with processes
        run_result = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.processes), selectinload(PipelineRun.references))
            .where(PipelineRun.experiment_id == experiment_id)
        )
        runs = run_result.scalars().all()
        for r in runs:
            user_ids.add(r.submitted_by_user_id)
            user_ids.add(r.reviewed_by_user_id)

        # Files
        file_result = await session.execute(select(File).where(File.experiment_id == experiment_id))
        files = file_result.scalars().all()
        for f in files:
            user_ids.add(f.uploader_user_id)

        raw_files = [f for f in files if f.source_type != "pipeline_output"]
        result_files = [f for f in files if f.source_type == "pipeline_output"]

        # References
        references_list: list[dict[str, Any]] = []
        seen_refs: set[int] = set()
        for run in runs:
            for ref in run.references or []:
                if ref.id not in seen_refs:
                    references_list.append(
                        {"id": ref.id, "name": ref.name, "version": ref.version, "category": ref.category}
                    )
                    seen_refs.add(ref.id)

        # Custom fields
        cf_result = await session.execute(
            select(ExperimentCustomField).where(ExperimentCustomField.experiment_id == experiment_id)
        )
        custom_fields = cf_result.scalars().all()

        # Resolve users
        user_map = await _user_map(session, user_ids)

        # Audit trail
        audit_pairs: list[tuple[str, int]] = [("experiment", experiment_id)]
        for s in samples:
            audit_pairs.append(("sample", s.id))
        for r in runs:
            audit_pairs.append(("pipeline_run", r.id))
        audit_trail = await _audit_entries(session, audit_pairs, user_map)

        return ExperimentProvenanceData(
            experiment={
                "id": experiment.id,
                "project_id": experiment.project_id,
                "name": experiment.name,
                "description": experiment.description,
                "hypothesis": experiment.hypothesis,
                "design_type": experiment.design_type,
                "protocol_version": experiment.protocol_version,
                "variables": experiment.variables_json,
                "status": experiment.status,
                "owner": _user_ref(user_map, experiment.owner_user_id),
                "created_at": _dt(experiment.created_at),
                "updated_at": _dt(experiment.updated_at),
                "start_date": _dt(experiment.start_date),
            },
            samples=[
                {
                    "id": s.id,
                    "external_id": s.sample_id_external,
                    "experiment_id": s.experiment_id,
                    "batch_id": s.batch_id,
                    "biological": {
                        "organism": s.organism,
                        "tissue_type": s.tissue_type,
                        "donor_source": s.donor_source,
                        "treatment_condition": s.treatment_condition,
                    },
                    "technical": {
                        "chemistry_version": s.chemistry_version,
                        "molecule_type": s.molecule_type,
                        "library_prep_method": s.library_prep_method,
                        "library_layout": s.library_layout,
                    },
                    "collection": {
                        "timestamp": _dt(s.collection_timestamp),
                        "method": s.collection_method,
                    },
                    "qc": {
                        "status": s.qc_status,
                        "notes": s.qc_notes,
                        "viability_pct": _dec(s.viability_pct),
                        "cell_count": s.cell_count,
                    },
                    "status": s.status,
                    "parent_sample_id": s.parent_sample_id,
                }
                for s in samples
            ],
            batches=[
                {
                    "id": b.id,
                    "name": b.name,
                    "prep_date": _dt(b.prep_date),
                    "instrument_model": b.instrument_model,
                    "operator": _user_ref(user_map, b.operator_user_id),
                }
                for b in batches
            ],
            pipeline_runs=[
                {
                    "id": r.id,
                    "pipeline_name": r.pipeline_name,
                    "pipeline_version": r.pipeline_version,
                    "status": r.status,
                    "submitted_by": _user_ref(user_map, r.submitted_by_user_id),
                    "reviewed_by": _user_ref(user_map, r.reviewed_by_user_id),
                    "reviewed_at": _dt(r.reviewed_at),
                    "started_at": _dt(r.started_at),
                    "completed_at": _dt(r.completed_at),
                    "parameters": r.parameters_json,
                    "reference_genome": r.reference_genome,
                    "alignment_algorithm": r.alignment_algorithm,
                    "retry_count": r.retry_count,
                    "cost": {"estimated": _dec(r.cost_estimate), "actual": _dec(r.actual_cost)},
                    "environment": {
                        "container_versions": r.container_versions_json,
                        "k8s_namespace": r.k8s_namespace,
                        "k8s_pod_name": r.k8s_pod_name,
                        "work_dir": r.work_dir,
                    },
                    "processes": [
                        {
                            "name": p.process_name,
                            "task_id": p.task_id,
                            "status": p.status,
                            "exit_code": p.exit_code,
                            "cpu_usage": _dec(p.cpu_usage),
                            "memory_peak_gb": _dec(p.memory_peak_gb),
                            "duration_seconds": p.duration_seconds,
                            "started_at": _dt(p.started_at),
                            "completed_at": _dt(p.completed_at),
                            "stdout_path": p.stdout_path,
                            "stderr_path": p.stderr_path,
                        }
                        for p in (r.processes or [])
                    ],
                }
                for r in runs
            ],
            files_raw=[
                {
                    "id": f.id,
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "size_bytes": f.size_bytes,
                    "md5": f.md5_checksum,
                    "sha256": f.sha256_checksum,
                    "gcs_uri": f.gcs_uri,
                    "artifact_type": f.artifact_type,
                    "upload_timestamp": _dt(f.upload_timestamp),
                }
                for f in raw_files
            ],
            files_results=[
                {
                    "id": f.id,
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "size_bytes": f.size_bytes,
                    "md5": f.md5_checksum,
                    "sha256": f.sha256_checksum,
                    "gcs_uri": f.gcs_uri,
                    "artifact_type": f.artifact_type,
                    "source_pipeline_run_id": f.source_pipeline_run_id,
                    "upload_timestamp": _dt(f.upload_timestamp),
                }
                for f in result_files
            ],
            references=references_list,
            custom_fields=[
                {"id": cf.id, "field_name": cf.field_name, "field_value": cf.field_value} for cf in custom_fields
            ],
            audit_trail=audit_trail,
        )

    @staticmethod
    async def gather_sample(session: AsyncSession, sample_id: int, org_id: int) -> SampleProvenanceData:
        # Load sample with experiment for org check
        result = await session.execute(
            select(Sample, Experiment)
            .join(Experiment, Experiment.id == Sample.experiment_id)
            .where(Sample.id == sample_id, Experiment.organization_id == org_id)
        )
        row = result.one_or_none()
        if not row:
            return SampleProvenanceData()

        sample, experiment = row

        user_ids: set[int | None] = {experiment.owner_user_id}

        # Parent sample
        parent_sample = None
        if sample.parent_sample_id:
            parent_result = await session.execute(select(Sample).where(Sample.id == sample.parent_sample_id))
            ps = parent_result.scalar_one_or_none()
            if ps:
                parent_sample = {
                    "id": ps.id,
                    "external_id": ps.sample_id_external,
                    "organism": ps.organism,
                }

        # Derived samples
        derived_result = await session.execute(select(Sample).where(Sample.parent_sample_id == sample_id))
        derived = derived_result.scalars().all()

        # Batch
        batch_data = None
        if sample.batch_id:
            batch_result = await session.execute(select(Batch).where(Batch.id == sample.batch_id))
            batch = batch_result.scalar_one_or_none()
            if batch:
                user_ids.add(batch.operator_user_id)
                batch_data = {
                    "id": batch.id,
                    "name": batch.name,
                    "instrument_model": batch.instrument_model,
                    "prep_date": _dt(batch.prep_date),
                }

        # Files via sample_files junction
        file_result = await session.execute(
            text("SELECT f.* FROM files f JOIN sample_files sf ON sf.file_id = f.id WHERE sf.sample_id = :sid"),
            {"sid": sample_id},
        )
        file_rows = file_result.mappings().all()

        # Pipeline runs via pipeline_run_samples
        run_result = await session.execute(
            text(
                "SELECT pr.* FROM pipeline_runs pr "
                "JOIN pipeline_run_samples prs ON prs.pipeline_run_id = pr.id "
                "WHERE prs.sample_id = :sid"
            ),
            {"sid": sample_id},
        )
        run_rows = run_result.mappings().all()
        for r in run_rows:
            user_ids.add(r.get("submitted_by_user_id"))

        # Resolve users
        user_map = await _user_map(session, user_ids)

        # Audit trail
        audit_trail = await _audit_entries(session, [("sample", sample_id)], user_map)

        return SampleProvenanceData(
            sample={
                "id": sample.id,
                "external_id": sample.sample_id_external,
                "experiment_id": sample.experiment_id,
                "parent_sample_id": sample.parent_sample_id,
                "biological": {
                    "organism": sample.organism,
                    "tissue_type": sample.tissue_type,
                    "donor_source": sample.donor_source,
                    "treatment_condition": sample.treatment_condition,
                },
                "technical": {
                    "chemistry_version": sample.chemistry_version,
                    "molecule_type": sample.molecule_type,
                    "library_prep_method": sample.library_prep_method,
                    "library_layout": sample.library_layout,
                },
                "collection": {
                    "timestamp": _dt(sample.collection_timestamp),
                    "method": sample.collection_method,
                },
                "qc": {
                    "status": sample.qc_status,
                    "notes": sample.qc_notes,
                    "viability_pct": _dec(sample.viability_pct),
                    "cell_count": sample.cell_count,
                },
                "status": sample.status,
            },
            parent_sample=parent_sample,
            derived_samples=[{"id": d.id, "external_id": d.sample_id_external} for d in derived],
            files=[
                {
                    "id": fr["id"],
                    "filename": fr["filename"],
                    "file_type": fr["file_type"],
                    "size_bytes": fr["size_bytes"],
                    "md5": fr["md5_checksum"],
                    "sha256": fr.get("sha256_checksum"),
                }
                for fr in file_rows
            ],
            pipeline_runs=[
                {
                    "id": r["id"],
                    "pipeline_name": r["pipeline_name"],
                    "pipeline_version": r.get("pipeline_version"),
                    "status": r["status"],
                    "submitted_by": _user_ref(user_map, r.get("submitted_by_user_id")),
                }
                for r in run_rows
            ],
            batch=batch_data,
            audit_trail=audit_trail,
        )

    @staticmethod
    async def gather_pipeline_run(session: AsyncSession, run_id: int, org_id: int) -> PipelineRunProvenanceData:
        result = await session.execute(
            select(PipelineRun)
            .options(
                selectinload(PipelineRun.processes),
                selectinload(PipelineRun.references),
                selectinload(PipelineRun.samples),
            )
            .where(PipelineRun.id == run_id, PipelineRun.organization_id == org_id)
        )
        run = result.scalar_one_or_none()
        if not run:
            return PipelineRunProvenanceData()

        user_ids: set[int | None] = {run.submitted_by_user_id, run.reviewed_by_user_id}
        user_map = await _user_map(session, user_ids)

        # Output files
        output_result = await session.execute(select(File).where(File.source_pipeline_run_id == run_id))
        output_files = output_result.scalars().all()

        # Input files (from input_files_json)
        input_files_data: list[dict[str, Any]] = []
        if run.input_files_json:
            file_ids = [entry.get("file_id") for entry in run.input_files_json if entry.get("file_id")]
            if file_ids:
                input_result = await session.execute(select(File).where(File.id.in_(file_ids)))
                input_files = input_result.scalars().all()
                input_files_data = [
                    {
                        "id": f.id,
                        "filename": f.filename,
                        "file_type": f.file_type,
                        "size_bytes": f.size_bytes,
                        "md5": f.md5_checksum,
                        "sha256": f.sha256_checksum,
                    }
                    for f in input_files
                ]

        # Resume from
        resume_from = None
        if run.resume_from_run_id:
            resume_result = await session.execute(select(PipelineRun).where(PipelineRun.id == run.resume_from_run_id))
            parent_run = resume_result.scalar_one_or_none()
            if parent_run:
                resume_from = {
                    "id": parent_run.id,
                    "pipeline_name": parent_run.pipeline_name,
                    "status": parent_run.status,
                }

        # Audit trail
        audit_trail = await _audit_entries(session, [("pipeline_run", run_id)], user_map)

        return PipelineRunProvenanceData(
            run={
                "id": run.id,
                "pipeline_name": run.pipeline_name,
                "pipeline_version": run.pipeline_version,
                "status": run.status,
                "submitted_by": _user_ref(user_map, run.submitted_by_user_id),
                "reviewed_by": _user_ref(user_map, run.reviewed_by_user_id),
                "reviewed_at": _dt(run.reviewed_at),
                "started_at": _dt(run.started_at),
                "completed_at": _dt(run.completed_at),
                "retry_count": run.retry_count,
                "cost": {"estimated": _dec(run.cost_estimate), "actual": _dec(run.actual_cost)},
                "parameters": run.parameters_json,
                "environment": {
                    "container_versions": run.container_versions_json,
                    "k8s_namespace": run.k8s_namespace,
                    "k8s_pod_name": run.k8s_pod_name,
                    "work_dir": run.work_dir,
                },
                "reference_genome": run.reference_genome,
                "alignment_algorithm": run.alignment_algorithm,
                "error_message": run.error_message,
                "nextflow_trace": run.nextflow_trace_json,
                "resume_from_run_id": run.resume_from_run_id,
            },
            processes=[
                {
                    "name": p.process_name,
                    "task_id": p.task_id,
                    "status": p.status,
                    "exit_code": p.exit_code,
                    "cpu_usage": _dec(p.cpu_usage),
                    "memory_peak_gb": _dec(p.memory_peak_gb),
                    "duration_seconds": p.duration_seconds,
                    "started_at": _dt(p.started_at),
                    "completed_at": _dt(p.completed_at),
                    "stdout_path": p.stdout_path,
                    "stderr_path": p.stderr_path,
                }
                for p in (run.processes or [])
            ],
            input_files=input_files_data,
            output_files=[
                {
                    "id": f.id,
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "size_bytes": f.size_bytes,
                    "md5": f.md5_checksum,
                    "sha256": f.sha256_checksum,
                    "artifact_type": f.artifact_type,
                }
                for f in output_files
            ],
            samples=[
                {
                    "id": s.id,
                    "external_id": s.sample_id_external,
                    "organism": s.organism,
                }
                for s in (run.samples or [])
            ],
            references=[
                {
                    "id": ref.id,
                    "name": ref.name,
                    "version": ref.version,
                    "category": ref.category,
                }
                for ref in (run.references or [])
            ],
            resume_from=resume_from,
            audit_trail=audit_trail,
        )

    @staticmethod
    async def gather_artifact(session: AsyncSession, file_id: int, org_id: int) -> ArtifactProvenanceData:
        result = await session.execute(select(File).where(File.id == file_id, File.organization_id == org_id))
        file = result.scalar_one_or_none()
        if not file:
            return ArtifactProvenanceData()

        user_ids: set[int | None] = {file.uploader_user_id}

        # Source pipeline run
        source_run_data = None
        if file.source_pipeline_run_id:
            run_result = await session.execute(
                select(PipelineRun)
                .options(selectinload(PipelineRun.processes))
                .where(PipelineRun.id == file.source_pipeline_run_id)
            )
            source_run = run_result.scalar_one_or_none()
            if source_run:
                user_ids.add(source_run.submitted_by_user_id)
                user_map_early = await _user_map(session, user_ids)
                source_run_data = {
                    "id": source_run.id,
                    "pipeline_name": source_run.pipeline_name,
                    "pipeline_version": source_run.pipeline_version,
                    "status": source_run.status,
                    "submitted_by": _user_ref(user_map_early, source_run.submitted_by_user_id),
                }

        # Linked samples
        linked_result = await session.execute(
            text(
                "SELECT s.id, s.sample_id_external, s.organism FROM samples s "
                "JOIN sample_files sf ON sf.sample_id = s.id "
                "WHERE sf.file_id = :fid"
            ),
            {"fid": file_id},
        )
        linked_samples = [
            {"id": r["id"], "external_id": r["sample_id_external"], "organism": r["organism"]}
            for r in linked_result.mappings().all()
        ]

        # Downstream usage: pipeline runs that consumed this file as input
        # Search input_files_json for references to this file_id
        downstream_result = await session.execute(
            text(
                "SELECT id, pipeline_name FROM pipeline_runs "
                "WHERE organization_id = :org AND input_files_json::text LIKE :pattern"
            ),
            {"org": org_id, "pattern": f'%"file_id": {file_id}%'},
        )
        downstream_usage = [
            {"pipeline_run_id": r["id"], "pipeline_name": r["pipeline_name"]}
            for r in downstream_result.mappings().all()
        ]

        user_map = await _user_map(session, user_ids)

        # Audit trail
        audit_trail = await _audit_entries(session, [("file", file_id)], user_map)

        return ArtifactProvenanceData(
            file={
                "id": file.id,
                "filename": file.filename,
                "file_type": file.file_type,
                "artifact_type": file.artifact_type,
                "size_bytes": file.size_bytes,
                "checksums": {
                    "md5": file.md5_checksum,
                    "sha256": file.sha256_checksum,
                },
                "gcs_uri": file.gcs_uri,
                "source": {
                    "type": "pipeline_output" if file.source_pipeline_run_id else file.source_type,
                    "pipeline_run": source_run_data,
                },
                "uploader": _user_ref(user_map, file.uploader_user_id),
                "created_at": _dt(file.created_at),
                "upload_timestamp": _dt(file.upload_timestamp),
            },
            source_pipeline_run=source_run_data,
            linked_samples=linked_samples,
            downstream_usage=downstream_usage,
            audit_trail=audit_trail,
        )
