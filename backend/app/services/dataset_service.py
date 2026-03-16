import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.batch import Batch
from app.models.cellxgene_publication import CellxgenePublication
from app.models.experiment import Experiment
from app.models.sample import Sample
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_review import PipelineRunReview
from app.models.qc_dashboard import QCDashboard

logger = logging.getLogger("bioaf.dataset_service")


class DatasetService:
    @staticmethod
    async def search_datasets(
        session: AsyncSession,
        org_id: int,
        query: str | None = None,
        organism: str | None = None,
        tissue: str | None = None,
        chemistry: str | None = None,
        status: str | None = None,
        molecule_type: str | None = None,
        instrument_model: str | None = None,
        review_status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        batch_id: int | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[dict], int]:
        """Search datasets (experiments with aggregated sample/file info)."""
        base = select(Experiment).where(Experiment.organization_id == org_id)
        count_base = select(func.count(Experiment.id)).where(Experiment.organization_id == org_id)

        if query:
            like = f"%{query}%"
            base = base.where(Experiment.name.ilike(like))
            count_base = count_base.where(Experiment.name.ilike(like))
        if status:
            base = base.where(Experiment.status == status)
            count_base = count_base.where(Experiment.status == status)
        if date_from:
            base = base.where(Experiment.created_at >= date_from)
            count_base = count_base.where(Experiment.created_at >= date_from)
        if date_to:
            base = base.where(Experiment.created_at <= date_to)
            count_base = count_base.where(Experiment.created_at <= date_to)

        total_result = await session.execute(count_base)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        base = (
            base.options(selectinload(Experiment.samples), selectinload(Experiment.owner))
            .order_by(Experiment.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )

        result = await session.execute(base)
        experiments = list(result.scalars().all())

        datasets = []
        for exp in experiments:
            samples = exp.samples or []

            # Filter by organism/tissue/chemistry/molecule_type at sample level
            if organism and not any(s.organism == organism for s in samples):
                continue
            if tissue and not any(s.tissue_type == tissue for s in samples):
                continue
            if chemistry and not any(s.chemistry_version == chemistry for s in samples):
                continue
            if molecule_type and not any(s.molecule_type == molecule_type for s in samples):
                continue

            # Instrument model from batches
            batch_result = await session.execute(
                select(Batch.instrument_model).where(Batch.experiment_id == exp.id, Batch.instrument_model.is_not(None))
            )
            instrument_models = [row[0] for row in batch_result.all()]
            top_instrument = max(set(instrument_models), key=instrument_models.count) if instrument_models else None

            if instrument_model and instrument_model not in instrument_models:
                continue

            # Review status from pipeline run reviews (latest non-superseded)
            review_result = await session.execute(
                select(PipelineRunReview.verdict)
                .join(PipelineRun, PipelineRunReview.pipeline_run_id == PipelineRun.id)
                .where(
                    PipelineRun.experiment_id == exp.id,
                    PipelineRunReview.superseded_by_id.is_(None),
                )
                .order_by(PipelineRunReview.reviewed_at.desc())
                .limit(1)
            )
            review_verdict = review_result.scalar_one_or_none()

            if review_status and review_verdict != review_status:
                continue

            # Get file count and size via File.experiment_id (direct FK)
            file_result = await session.execute(
                select(func.count(File.id), func.coalesce(func.sum(File.size_bytes), 0)).where(
                    File.experiment_id == exp.id
                )
            )
            file_row = file_result.first()
            file_count = file_row[0] if file_row else 0
            total_size = file_row[1] if file_row else 0

            # Pipeline run count
            run_result = await session.execute(
                select(func.count(PipelineRun.id)).where(PipelineRun.experiment_id == exp.id)
            )
            run_count = run_result.scalar() or 0

            # QC dashboard check
            qc_result = await session.execute(
                select(func.count(QCDashboard.id)).where(QCDashboard.experiment_id == exp.id)
            )
            has_qc = (qc_result.scalar() or 0) > 0

            # cellxgene check
            cx_result = await session.execute(
                select(func.count(CellxgenePublication.id)).where(
                    CellxgenePublication.experiment_id == exp.id,
                    CellxgenePublication.status == "published",
                )
            )
            has_cellxgene = (cx_result.scalar() or 0) > 0

            # Most common organism/tissue/molecule_type
            organisms = [s.organism for s in samples if s.organism]
            tissues = [s.tissue_type for s in samples if s.tissue_type]
            mol_types = [s.molecule_type for s in samples if s.molecule_type]

            datasets.append(
                {
                    "experiment_id": exp.id,
                    "experiment_name": exp.name,
                    "status": exp.status,
                    "organism": max(set(organisms), key=organisms.count) if organisms else None,
                    "tissue": max(set(tissues), key=tissues.count) if tissues else None,
                    "molecule_type": max(set(mol_types), key=mol_types.count) if mol_types else None,
                    "instrument_model": top_instrument,
                    "review_status": review_verdict,
                    "sample_count": len(samples),
                    "file_count": file_count,
                    "total_size_bytes": total_size,
                    "pipeline_run_count": run_count,
                    "has_qc_dashboard": has_qc,
                    "has_cellxgene": has_cellxgene,
                    "owner": exp.owner,
                    "created_at": exp.created_at,
                }
            )

        return datasets, total

    @staticmethod
    async def get_filter_options(session: AsyncSession, org_id: int) -> dict:
        """Return distinct filter values for the org's samples."""
        result = await session.execute(
            select(Sample.organism)
            .join(Experiment, Sample.experiment_id == Experiment.id)
            .where(Experiment.organization_id == org_id, Sample.organism.is_not(None), Sample.organism != "")
            .distinct()
        )
        organisms = sorted([row[0] for row in result.all()], key=lambda s: (s.lower(), s))
        return {"organisms": organisms}

    @staticmethod
    async def get_dataset_detail(session: AsyncSession, org_id: int, experiment_id: int) -> dict | None:
        """Get full dataset detail for an experiment."""
        result = await session.execute(
            select(Experiment)
            .options(
                selectinload(Experiment.samples),
                selectinload(Experiment.owner),
                selectinload(Experiment.project),
            )
            .where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
        )
        exp = result.scalar_one_or_none()
        if not exp:
            return None

        # Pipeline runs
        runs_result = await session.execute(select(PipelineRun).where(PipelineRun.experiment_id == experiment_id))
        runs = list(runs_result.scalars().all())

        # QC dashboards
        qc_result = await session.execute(select(QCDashboard).where(QCDashboard.experiment_id == experiment_id))
        dashboards = list(qc_result.scalars().all())

        # cellxgene publications
        cx_result = await session.execute(
            select(CellxgenePublication).where(CellxgenePublication.experiment_id == experiment_id)
        )
        publications = list(cx_result.scalars().all())

        return {
            "experiment": exp,
            "samples": exp.samples or [],
            "pipeline_runs": runs,
            "qc_dashboards": dashboards,
            "cellxgene_publications": publications,
        }
