"""GEO SuperSeries Export Service for cross-experiment project-level exports."""

import json
import logging
import zipfile
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.experiment import Experiment
from app.models.project import Project
from app.services.geo.geo_export_service import GeoExportService

logger = logging.getLogger("bioaf.geo.superseries")


class SuperSeriesValidation:
    """Results from cross-experiment validation."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict:
        return {
            "warnings": self.warnings,
            "errors": self.errors,
            "has_errors": self.has_errors,
        }


class SuperSeriesExportService:
    """Orchestrates project-level GEO SuperSeries export."""

    @staticmethod
    async def validate_cross_experiment(
        session: AsyncSession,
        project_id: int,
        org_id: int,
        experiment_ids: list[int] | None = None,
        exclude_unclaimed: bool = True,
    ) -> SuperSeriesValidation:
        """Run cross-experiment validation checks."""
        experiments = await SuperSeriesExportService._load_project_experiments(
            session, project_id, org_id, experiment_ids, exclude_unclaimed
        )
        return SuperSeriesExportService._cross_validate(experiments)

    @staticmethod
    async def export(
        session: AsyncSession,
        project_id: int,
        org_id: int,
        pipeline_run_ids: dict[int, int] | None = None,
        qc_status_filter: str = "exclude_failed",
        exclude_unclaimed: bool = True,
        experiment_ids: list[int] | None = None,
    ) -> tuple[bytes, str]:
        """Generate SuperSeries export package as ZIP bytes.

        Args:
            pipeline_run_ids: Optional mapping of experiment_id -> pipeline_run_id.
            experiment_ids: Optional list of experiment IDs to include (subset of project).

        Returns:
            Tuple of (zip_bytes, filename).
        """
        project = await SuperSeriesExportService._load_project(session, project_id, org_id)
        experiments = await SuperSeriesExportService._load_project_experiments(
            session, project_id, org_id, experiment_ids, exclude_unclaimed
        )

        if not experiments:
            raise ValueError("No experiments found for project")

        # Cross-experiment validation
        cross_validation = SuperSeriesExportService._cross_validate(experiments)

        # Generate per-experiment sub-Series exports
        sub_series: list[dict] = []
        experiment_zips: dict[str, bytes] = {}

        for exp in experiments:
            run_id = (pipeline_run_ids or {}).get(exp.id)
            try:
                exp_zip_bytes, exp_filename = await GeoExportService.export(
                    session, exp.id, org_id, run_id, qc_status_filter
                )
                exp_safe_name = exp.name.replace(" ", "_")[:50]
                sub_series.append(
                    {
                        "experiment_id": exp.id,
                        "experiment_name": exp.name,
                        "sub_series_filename": exp_filename,
                    }
                )
                experiment_zips[f"experiments/{exp_safe_name}/{exp_filename}"] = exp_zip_bytes
            except Exception as e:
                logger.warning("Failed to export experiment %d: %s", exp.id, e)
                sub_series.append(
                    {
                        "experiment_id": exp.id,
                        "experiment_name": exp.name,
                        "error": str(e),
                    }
                )

        # Generate SuperSeries metadata
        superseries_metadata = SuperSeriesExportService._build_superseries_metadata(project, sub_series)

        # Generate unified file manifest
        unified_manifest = SuperSeriesExportService._build_unified_manifest(experiments, session)

        # Build validation report
        validation_json = json.dumps(
            {
                "cross_experiment": cross_validation.to_dict(),
                "sub_series": sub_series,
                "project_id": project_id,
                "project_name": project.name,
                "export_timestamp": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )

        # Build ZIP
        project_name = project.name.replace(" ", "_")[:50]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        zip_filename = f"geo_superseries_{project_name}_{date_str}.zip"

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SuperSeries_metadata.txt", superseries_metadata)
            zf.writestr("unified_file_manifest.tsv", unified_manifest)
            zf.writestr("validation_report.json", validation_json)

            for path, data in experiment_zips.items():
                zf.writestr(path, data)

        zip_buffer.seek(0)
        return zip_buffer.read(), zip_filename

    @staticmethod
    async def _load_project(
        session: AsyncSession,
        project_id: int,
        org_id: int,
    ) -> Project:
        result = await session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.organization_id == org_id,
            )
        )
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError("Project not found")
        return project

    @staticmethod
    async def _load_project_experiments(
        session: AsyncSession,
        project_id: int,
        org_id: int,
        experiment_ids: list[int] | None = None,
        exclude_unclaimed: bool = True,
    ) -> list[Experiment]:
        query = (
            select(Experiment)
            .options(selectinload(Experiment.samples))
            .where(
                Experiment.project_id == project_id,
                Experiment.organization_id == org_id,
            )
        )
        if exclude_unclaimed:
            query = query.where(Experiment.is_unclaimed == False)  # noqa: E712
        if experiment_ids:
            query = query.where(Experiment.id.in_(experiment_ids))

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def _cross_validate(experiments: list[Experiment]) -> SuperSeriesValidation:
        """Run cross-experiment validation checks."""
        validation = SuperSeriesValidation()

        if not experiments:
            validation.errors.append("No experiments to validate")
            return validation

        # Check organism consistency
        organisms: set[str] = set()
        for exp in experiments:
            for sample in exp.samples:
                if sample.organism:
                    organisms.add(sample.organism)
        if len(organisms) > 1:
            validation.warnings.append(f"Multiple organisms across experiments: {', '.join(sorted(organisms))}")

        # Check reference genome consistency (would need pipeline run data)
        # For now, check via sample chemistry versions
        chemistries: set[str] = set()
        for exp in experiments:
            for sample in exp.samples:
                if sample.chemistry_version:
                    chemistries.add(sample.chemistry_version)
        if len(chemistries) > 1:
            validation.warnings.append(
                f"Multiple chemistry versions across experiments: {', '.join(sorted(chemistries))}"
            )

        # Check sample ID uniqueness
        sample_ids: list[str] = []
        for exp in experiments:
            for sample in exp.samples:
                if sample.sample_id_unique:
                    sample_ids.append(sample.sample_id_unique)
        duplicates = {sid for sid in sample_ids if sample_ids.count(sid) > 1}
        if duplicates:
            validation.errors.append(f"Duplicate sample IDs across experiments: {', '.join(sorted(duplicates))}")

        return validation

    @staticmethod
    def _build_superseries_metadata(
        project: Project,
        sub_series: list[dict],
    ) -> str:
        lines = [
            "^SUPERSERIES",
            f"!SuperSeries_title\t{project.name}",
            f"!SuperSeries_summary\t{project.description or 'No description'}",
        ]
        for ss in sub_series:
            if "error" not in ss:
                lines.append(f"!SuperSeries_subSeries\t{ss['experiment_name']}")
        lines.append(f"!SuperSeries_experiment_count\t{len(sub_series)}")
        return "\n".join(lines)

    @staticmethod
    def _build_unified_manifest(
        experiments: list[Experiment],
        session: AsyncSession,
    ) -> str:
        lines = ["experiment_id\texperiment_name\tsample_count\tstatus"]
        for exp in experiments:
            lines.append(f"{exp.id}\t{exp.name}\t{len(exp.samples)}\t{exp.status}")
        return "\n".join(lines)
