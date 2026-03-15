from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models.experiment import Experiment
from app.models.experiment_field_default import ExperimentFieldDefault, DEFAULTABLE_SAMPLE_FIELDS
from app.models.experiment_template import ExperimentTemplate
from app.models.sample import Sample
from app.schemas.sample import SampleCreate, SampleUpdate
from app.services.audit_service import log_action
from app.services.vocabulary_validator import VocabularyValidator


SAMPLE_STATUS_TRANSITIONS = {
    "registered": ["library_prepped"],
    "library_prepped": ["sequenced"],
    "sequenced": ["fastq_uploaded"],
    "fastq_uploaded": ["pipeline_complete"],
    "pipeline_complete": ["analysis_complete"],
    "analysis_complete": [],
}


class SampleService:
    @staticmethod
    async def _get_field_defaults(session: AsyncSession, experiment_id: int) -> dict[str, str]:
        """Load experiment-level field defaults as a {field_name: default_value} dict."""
        result = await session.execute(
            select(ExperimentFieldDefault).where(
                ExperimentFieldDefault.experiment_id == experiment_id,
                ExperimentFieldDefault.default_value.isnot(None),
            )
        )
        return {fd.field_name: fd.default_value for fd in result.scalars().all() if fd.default_value}

    @staticmethod
    def _apply_defaults(data: SampleCreate, defaults: dict[str, str]) -> SampleCreate:
        """Return a copy of data with experiment-level defaults filled in for empty fields."""
        if not defaults:
            return data
        overrides = {}
        for field_name in DEFAULTABLE_SAMPLE_FIELDS:
            if field_name not in defaults:
                continue
            current = getattr(data, field_name, None)
            if current is None or (isinstance(current, str) and not current.strip()):
                overrides[field_name] = defaults[field_name]
        if not overrides:
            return data
        return data.model_copy(update=overrides)

    @staticmethod
    async def _validate_template_fields(
        session: AsyncSession, experiment_id: int, sample_data: SampleCreate
    ) -> list[str]:
        result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        experiment = result.scalar_one_or_none()
        if not experiment or not experiment.template_id:
            return []

        template_result = await session.execute(
            select(ExperimentTemplate).where(ExperimentTemplate.id == experiment.template_id)
        )
        template = template_result.scalar_one_or_none()
        if not template or not template.required_fields_json:
            return []

        sample_fields = template.required_fields_json.get("sample_fields", [])
        errors = []
        for field in sample_fields:
            val = getattr(sample_data, field, None)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"Required field '{field}' is missing or empty")
        return errors

    @staticmethod
    async def create_sample(session: AsyncSession, experiment_id: int, user_id: int, data: SampleCreate) -> Sample:
        # Apply experiment-level defaults for any fields not provided
        defaults = await SampleService._get_field_defaults(session, experiment_id)
        data = SampleService._apply_defaults(data, defaults)

        errors = await SampleService._validate_template_fields(session, experiment_id, data)
        if errors:
            raise HTTPException(400, detail="; ".join(errors))

        # Validate controlled vocabulary fields
        await VocabularyValidator.validate_sample_fields(
            session,
            {
                "molecule_type": data.molecule_type,
                "library_prep_method": data.library_prep_method,
                "library_layout": data.library_layout,
            },
        )

        sample = Sample(
            experiment_id=experiment_id,
            batch_id=data.batch_id,
            sample_id_external=data.sample_id_external,
            organism=data.organism,
            tissue_type=data.tissue_type,
            donor_source=data.donor_source,
            treatment_condition=data.treatment_condition,
            chemistry_version=data.chemistry_version,
            viability_pct=data.viability_pct,
            cell_count=data.cell_count,
            prep_notes=data.prep_notes,
            molecule_type=data.molecule_type,
            library_prep_method=data.library_prep_method,
            library_layout=data.library_layout,
            qc_status=data.qc_status,
            qc_notes=data.qc_notes,
            status="registered",
        )
        session.add(sample)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="sample",
            entity_id=sample.id,
            action="create",
            details={
                "experiment_id": experiment_id,
                "sample_id_external": data.sample_id_external,
            },
        )
        return sample

    @staticmethod
    async def bulk_create_samples(
        session: AsyncSession, experiment_id: int, user_id: int, samples_data: list[SampleCreate]
    ) -> list[Sample]:
        # Apply experiment-level defaults
        defaults = await SampleService._get_field_defaults(session, experiment_id)
        samples_data = [SampleService._apply_defaults(d, defaults) for d in samples_data]

        # Validate all before creating any
        all_errors = []
        for i, data in enumerate(samples_data):
            errors = await SampleService._validate_template_fields(session, experiment_id, data)
            if errors:
                all_errors.append(f"Sample {i + 1}: {'; '.join(errors)}")
        if all_errors:
            raise HTTPException(400, detail="; ".join(all_errors))

        # Validate vocabulary fields for all samples
        for i, data in enumerate(samples_data):
            await VocabularyValidator.validate_sample_fields(
                session,
                {
                    "molecule_type": data.molecule_type,
                    "library_prep_method": data.library_prep_method,
                    "library_layout": data.library_layout,
                },
            )

        created = []
        for data in samples_data:
            sample = Sample(
                experiment_id=experiment_id,
                batch_id=data.batch_id,
                sample_id_external=data.sample_id_external,
                organism=data.organism,
                tissue_type=data.tissue_type,
                donor_source=data.donor_source,
                treatment_condition=data.treatment_condition,
                chemistry_version=data.chemistry_version,
                viability_pct=data.viability_pct,
                cell_count=data.cell_count,
                prep_notes=data.prep_notes,
                molecule_type=data.molecule_type,
                library_prep_method=data.library_prep_method,
                library_layout=data.library_layout,
                qc_status=data.qc_status,
                qc_notes=data.qc_notes,
                status="registered",
            )
            session.add(sample)
            await session.flush()

            await log_action(
                session,
                user_id=user_id,
                entity_type="sample",
                entity_id=sample.id,
                action="create",
                details={
                    "experiment_id": experiment_id,
                    "sample_id_external": data.sample_id_external,
                },
            )
            created.append(sample)
        return created

    @staticmethod
    async def update_sample(session: AsyncSession, sample_id: int, user_id: int, data: SampleUpdate) -> Sample | None:
        result = await session.execute(select(Sample).options(selectinload(Sample.batch)).where(Sample.id == sample_id))
        sample = result.scalar_one_or_none()
        if not sample:
            return None

        # Validate controlled vocabulary fields
        await VocabularyValidator.validate_sample_fields(
            session,
            {
                "molecule_type": data.molecule_type,
                "library_prep_method": data.library_prep_method,
                "library_layout": data.library_layout,
            },
        )

        previous = {}
        updates = {}
        for field in [
            "sample_id_external",
            "organism",
            "tissue_type",
            "donor_source",
            "treatment_condition",
            "chemistry_version",
            "batch_id",
            "viability_pct",
            "cell_count",
            "prep_notes",
            "molecule_type",
            "library_prep_method",
            "library_layout",
        ]:
            new_val = getattr(data, field, None)
            if new_val is not None:
                old_val = getattr(sample, field)
                previous[field] = str(old_val) if old_val is not None else None
                setattr(sample, field, new_val)
                updates[field] = str(new_val) if new_val is not None else None

        if updates:
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="sample",
                entity_id=sample.id,
                action="update",
                details=updates,
                previous_value=previous,
            )
        return sample

    @staticmethod
    async def update_qc_status(
        session: AsyncSession, sample_id: int, user_id: int, qc_status: str, qc_notes: str | None
    ) -> Sample | None:
        result = await session.execute(select(Sample).where(Sample.id == sample_id))
        sample = result.scalar_one_or_none()
        if not sample:
            return None

        previous = {"qc_status": sample.qc_status, "qc_notes": sample.qc_notes}
        sample.qc_status = qc_status
        sample.qc_notes = qc_notes
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="sample",
            entity_id=sample.id,
            action="qc_update",
            details={"qc_status": qc_status, "qc_notes": qc_notes},
            previous_value=previous,
        )
        return sample

    @staticmethod
    async def update_status(session: AsyncSession, sample_id: int, user_id: int, new_status: str) -> Sample:
        result = await session.execute(select(Sample).where(Sample.id == sample_id))
        sample = result.scalar_one_or_none()
        if not sample:
            raise HTTPException(404, "Sample not found")

        allowed = SAMPLE_STATUS_TRANSITIONS.get(sample.status, [])
        if new_status not in allowed:
            raise HTTPException(
                400,
                f"Cannot transition from '{sample.status}' to '{new_status}'. "
                f"Next valid status: {', '.join(allowed) if allowed else 'none (terminal state)'}.",
            )

        old_status = sample.status
        sample.status = new_status
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="sample",
            entity_id=sample.id,
            action="status_change",
            details={"status": new_status},
            previous_value={"status": old_status},
        )
        return sample

    @staticmethod
    async def list_samples(
        session: AsyncSession,
        experiment_id: int,
        batch_id: int | None = None,
        qc_status: str | None = None,
        status: str | None = None,
    ) -> list[Sample]:
        query = select(Sample).options(selectinload(Sample.batch)).where(Sample.experiment_id == experiment_id)
        if batch_id is not None:
            query = query.where(Sample.batch_id == batch_id)
        if qc_status is not None:
            query = query.where(Sample.qc_status == qc_status)
        if status is not None:
            query = query.where(Sample.status == status)

        result = await session.execute(query.order_by(Sample.created_at.desc()))
        return list(result.scalars().all())

    @staticmethod
    async def get_sample(session: AsyncSession, sample_id: int) -> Sample | None:
        result = await session.execute(select(Sample).options(selectinload(Sample.batch)).where(Sample.id == sample_id))
        return result.scalar_one_or_none()
