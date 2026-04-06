"""GEO Export Service — orchestrates validation, Excel generation, and ZIP packaging."""

import logging
import zipfile
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_review import PipelineRunReview
from app.models.sample import Sample
from app.services.geo.checksum_manifest import generate_checksum_manifest
from app.services.geo.excel_generator import generate_geo_workbook
from app.services.geo.readme_generator import generate_readme
from app.services.geo.validation import ValidationReport, validate_experiment_for_geo

logger = logging.getLogger("bioaf.geo.export")


class GeoExportService:
    """Orchestrates GEO export: data gathering, validation, Excel + ZIP generation."""

    @staticmethod
    async def validate(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
        pipeline_run_id: int | None = None,
        qc_status_filter: str = "exclude_failed",
    ) -> ValidationReport:
        """Run validation only, returning the report."""
        experiment_data, samples_data, pipeline_data, files_data = await GeoExportService._gather_data(
            session, experiment_id, org_id, pipeline_run_id, qc_status_filter
        )
        return validate_experiment_for_geo(experiment_data, samples_data, pipeline_data, files_data)

    @staticmethod
    async def export(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
        pipeline_run_id: int | None = None,
        qc_status_filter: str = "exclude_failed",
    ) -> tuple[bytes, str]:
        """Generate full GEO export package as ZIP bytes.

        Returns:
            Tuple of (zip_bytes, filename).
        """
        experiment_data, samples_data, pipeline_data, files_data = await GeoExportService._gather_data(
            session, experiment_id, org_id, pipeline_run_id, qc_status_filter
        )

        # Validation
        validation_report = validate_experiment_for_geo(experiment_data, samples_data, pipeline_data, files_data)

        # Excel
        excel_bytes = generate_geo_workbook(experiment_data, samples_data, pipeline_data, files_data)

        # Checksums
        checksum_manifest, missing_checksums = generate_checksum_manifest(files_data)

        # README
        readme_text = generate_readme(
            experiment_data.get("name", "unknown"),
            validation_report,
            files_data,
            missing_checksums,
        )

        # Human-readable validation report
        validation_text = _format_validation_report(validation_report)

        # Build ZIP in memory
        exp_name = experiment_data.get("name", "experiment").replace(" ", "_")[:50]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        zip_filename = f"geo_export_{exp_name}_{date_str}.zip"

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"geo_metadata_{exp_name}.xlsx", excel_bytes)
            zf.writestr("md5_checksums.txt", checksum_manifest)
            zf.writestr("validation_report.json", validation_report.model_dump_json(indent=2))
            zf.writestr("validation_report.txt", validation_text)
            zf.writestr("README.txt", readme_text)

        zip_buffer.seek(0)
        return zip_buffer.read(), zip_filename

    @staticmethod
    async def _gather_data(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
        pipeline_run_id: int | None,
        qc_status_filter: str,
    ) -> tuple[dict, list[dict], dict | None, dict | None]:
        """Gather all data needed for GEO export in efficient queries."""
        # 1. Experiment (with owner for Series_contributor, custom_fields for protocols)
        exp_result = await session.execute(
            select(Experiment)
            .options(selectinload(Experiment.owner), selectinload(Experiment.custom_fields))
            .where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        experiment = exp_result.scalar_one_or_none()
        if not experiment:
            raise ValueError("Experiment not found")

        # 2. Samples with batches
        sample_query = (
            select(Sample)
            .options(selectinload(Sample.sample_batch), selectinload(Sample.sequencing_batch))
            .where(Sample.experiment_id == experiment_id)
        )
        if qc_status_filter == "exclude_failed":
            sample_query = sample_query.where((Sample.qc_status != "fail") | (Sample.qc_status.is_(None)))
        sample_result = await session.execute(sample_query)
        samples = list(sample_result.scalars().all())

        # 3. Pipeline run (select best one if not specified)
        pipeline_run = None
        if pipeline_run_id:
            run_result = await session.execute(
                select(PipelineRun).where(
                    PipelineRun.id == pipeline_run_id,
                    PipelineRun.experiment_id == experiment_id,
                )
            )
            pipeline_run = run_result.scalar_one_or_none()
        else:
            pipeline_run = await GeoExportService._select_best_run(session, experiment_id)

        # 4. Files linked to experiment
        files_result = await session.execute(
            select(File).where(
                File.organization_id == org_id,
                File.gcs_uri.contains(f"/experiments/{experiment_id}/"),
            )
        )
        files = list(files_result.scalars().all())

        # Load custom fields for protocol info
        custom_fields_dict: dict[str, str] = {}
        if experiment.custom_fields:
            for cf in experiment.custom_fields:
                if cf.field_value:
                    custom_fields_dict[cf.field_name] = cf.field_value

        # Auto-generate protocol descriptions if not set
        if "extract_protocol" not in custom_fields_dict and samples:
            preps = {s.library_prep_method for s in samples if s.library_prep_method}
            molecules = {s.molecule_type for s in samples if s.molecule_type}
            if preps or molecules:
                parts = []
                if molecules:
                    parts.append(f"Molecule type: {', '.join(sorted(molecules))}")
                if preps:
                    parts.append(f"Library preparation: {', '.join(sorted(preps))}")
                custom_fields_dict["extract_protocol"] = ". ".join(parts) + "."

        if "treatment_protocol" not in custom_fields_dict and samples:
            treatments = {s.treatment_condition for s in samples if s.treatment_condition}
            if treatments:
                custom_fields_dict["treatment_protocol"] = f"Treatment conditions: {', '.join(sorted(treatments))}."

        # Assemble data dicts
        experiment_data = {
            "id": experiment.id,
            "name": experiment.name,
            "description": experiment.description,
            "hypothesis": getattr(experiment, "hypothesis", None),
            "owner_user_name": experiment.owner.name if experiment.owner else None,
            "custom_fields": custom_fields_dict,
            "samples": [{"organism": s.organism, "tissue_type": s.tissue_type} for s in samples],
        }

        samples_data = []
        for s in samples:
            sample_dict: dict = {
                "id": s.id,
                "sample_id_external": s.sample_id_external,
                "organism": s.organism,
                "molecule_type": s.molecule_type,
                "tissue_type": s.tissue_type,
                "treatment_condition": s.treatment_condition,
                "library_prep_method": s.library_prep_method,
                "library_layout": s.library_layout,
                "prep_notes": s.prep_notes,
                "chemistry_version": s.chemistry_version,
                "qc_status": s.qc_status,
                "sample_batch": None,
                "sequencing_batch": None,
            }
            if s.sample_batch:
                sample_dict["sample_batch"] = {
                    "id": s.sample_batch.id,
                    "name": s.sample_batch.name,
                }
            if s.sequencing_batch:
                sample_dict["sequencing_batch"] = {
                    "id": s.sequencing_batch.id,
                    "code": s.sequencing_batch.code,
                    "instrument_model": s.sequencing_batch.instrument_model,
                    "instrument_platform": s.sequencing_batch.instrument_platform,
                }
            samples_data.append(sample_dict)

        pipeline_data = None
        if pipeline_run:
            pipeline_data = {
                "id": pipeline_run.id,
                "pipeline_name": pipeline_run.pipeline_name,
                "pipeline_version": pipeline_run.pipeline_version,
                "reference_genome": pipeline_run.reference_genome,
                "alignment_algorithm": pipeline_run.alignment_algorithm,
                "parameters_json": pipeline_run.parameters_json,
            }

        raw_files = [f for f in files if f.file_type in ("fastq", "fastq.gz", "fq.gz")]
        processed_files = [f for f in files if f.file_type not in ("fastq", "fastq.gz", "fq.gz")]

        # Build per-sample file data by matching filename prefix to sample external ID
        for sd in samples_data:
            ext_id = sd["sample_id_external"]
            s_raw = [f for f in raw_files if f.filename.startswith(ext_id)]
            s_proc = [f for f in processed_files if f.filename.startswith(ext_id)]
            sd["files"] = {
                "raw_filenames": ", ".join(f.filename for f in s_raw),
                "processed_filenames": ", ".join(f.filename for f in s_proc),
                "processed_gcs_uris": ", ".join(f.gcs_uri for f in s_proc),
            }

        files_data: dict | None = None
        if files:
            files_data = {
                "raw_files": [
                    {"filename": f.filename, "md5_checksum": f.md5_checksum, "gcs_uri": f.gcs_uri} for f in raw_files
                ],
                "processed_files": [
                    {"filename": f.filename, "md5_checksum": f.md5_checksum, "gcs_uri": f.gcs_uri}
                    for f in processed_files
                ],
                "raw_filenames": ", ".join(f.filename for f in raw_files),
                "processed_filenames": ", ".join(f.filename for f in processed_files),
                "processed_gcs_uris": ", ".join(f.gcs_uri for f in processed_files),
            }

        return experiment_data, samples_data, pipeline_data, files_data

    @staticmethod
    async def _select_best_run(session: AsyncSession, experiment_id: int) -> PipelineRun | None:
        """Select the best pipeline run for export.

        Priority: most recent reviewed run > most recent completed run.
        """
        # Try reviewed runs first
        reviewed_subq = (
            select(PipelineRunReview.pipeline_run_id)
            .where(
                PipelineRunReview.superseded_by_id.is_(None),
                PipelineRunReview.verdict.in_(["approved", "approved_with_caveats"]),
            )
            .distinct()
        )

        result = await session.execute(
            select(PipelineRun)
            .where(
                PipelineRun.experiment_id == experiment_id,
                PipelineRun.status == "completed",
                PipelineRun.id.in_(reviewed_subq),
            )
            .order_by(PipelineRun.completed_at.desc())
            .limit(1)
        )
        run = result.scalar_one_or_none()
        if run:
            return run

        # Fall back to most recent completed
        result = await session.execute(
            select(PipelineRun)
            .where(
                PipelineRun.experiment_id == experiment_id,
                PipelineRun.status == "completed",
            )
            .order_by(PipelineRun.completed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


def _format_validation_report(report: ValidationReport) -> str:
    """Format validation report as human-readable text."""
    lines = [
        "GEO Validation Report",
        "=" * 50,
        "",
        f"Experiment ID: {report.experiment_id}",
        f"Pipeline Run ID: {report.pipeline_run_id or 'N/A'}",
        "",
        "SUMMARY",
        "-" * 30,
        f"Total fields: {report.summary.total_fields}",
        f"Complete: {report.summary.complete}",
        f"Populated (unvalidated): {report.summary.populated_unvalidated}",
        f"Missing (required): {report.summary.missing_required}",
        f"Missing (recommended): {report.summary.missing_recommended}",
        "",
    ]

    if report.summary.missing_required > 0:
        lines.append("MISSING REQUIRED FIELDS")
        lines.append("-" * 30)
        for f in report.series_fields + report.protocol_fields:
            if f.status == "missing_required":
                lines.append(f"  [SERIES/PROTOCOL] {f.geo_column}: {f.message}")
        for sv in report.sample_validations:
            for f in sv.fields:
                if f.status == "missing_required":
                    lines.append(f"  [SAMPLE {sv.sample_name}] {f.geo_column}: {f.message}")
        lines.append("")

    if report.summary.populated_unvalidated > 0:
        lines.append("UNVALIDATED VALUES")
        lines.append("-" * 30)
        for f in report.series_fields + report.protocol_fields:
            if f.status == "populated_unvalidated":
                lines.append(f"  [SERIES/PROTOCOL] {f.geo_column}: {f.value} — {f.message}")
        for sv in report.sample_validations:
            for f in sv.fields:
                if f.status == "populated_unvalidated":
                    lines.append(f"  [SAMPLE {sv.sample_name}] {f.geo_column}: {f.value} — {f.message}")
        lines.append("")

    if report.file_manifest.files_missing_checksums > 0:
        lines.append("FILES MISSING CHECKSUMS")
        lines.append("-" * 30)
        for f in report.file_manifest.files:
            if not f.has_checksum:
                lines.append(f"  {f.filename}")
        lines.append("")

    return "\n".join(lines)
