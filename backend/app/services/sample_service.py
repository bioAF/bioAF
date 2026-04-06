from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models.experiment import Experiment
from app.models.experiment_field_default import ExperimentFieldDefault, DEFAULTABLE_SAMPLE_FIELDS
from app.models.experiment_template import ExperimentTemplate
from app.models.sample import Sample
from app.models.sample_batch import SampleBatch
from app.models.sample_custom_field import SampleCustomField
from app.models.sequencing_batch import SequencingBatch
from app.schemas.sample import SampleCreate, SampleUpdate
from app.services.audit_service import log_action
from app.services.snapshot_utils import serialize_entity
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
    async def _resolve_sample_batch(session: AsyncSession, experiment_id: int, code: str) -> int:
        """Find or create a SampleBatch by code (name) within an experiment."""
        result = await session.execute(
            select(SampleBatch).where(SampleBatch.name == code, SampleBatch.experiment_id == experiment_id)
        )
        batch = result.scalar_one_or_none()
        if batch:
            return batch.id
        batch = SampleBatch(experiment_id=experiment_id, name=code)
        session.add(batch)
        await session.flush()
        return batch.id

    @staticmethod
    async def _resolve_sequencing_batch(session: AsyncSession, org_id: int, code: str) -> int:
        """Find or create a SequencingBatch by code within an organization."""
        result = await session.execute(
            select(SequencingBatch).where(SequencingBatch.code == code, SequencingBatch.organization_id == org_id)
        )
        batch = result.scalar_one_or_none()
        if batch:
            return batch.id
        batch = SequencingBatch(organization_id=org_id, code=code, name=code, status="pending")
        session.add(batch)
        await session.flush()
        return batch.id

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

        # Resolve batch codes to IDs
        sample_batch_id = None
        if data.sample_batch_code:
            sample_batch_id = await SampleService._resolve_sample_batch(session, experiment_id, data.sample_batch_code)
        sequencing_batch_id = None
        if data.sequencing_batch_code:
            exp_result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
            exp = exp_result.scalar_one()
            sequencing_batch_id = await SampleService._resolve_sequencing_batch(
                session, exp.organization_id, data.sequencing_batch_code
            )

        sample = Sample(
            experiment_id=experiment_id,
            sample_batch_id=sample_batch_id,
            sequencing_batch_id=sequencing_batch_id,
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
            parent_sample_id=data.parent_sample_id,
            collection_timestamp=data.collection_timestamp,
            collection_method=data.collection_method,
            status="registered",
        )
        session.add(sample)
        await session.flush()

        if data.custom_fields:
            for cf in data.custom_fields:
                session.add(
                    SampleCustomField(
                        sample_id=sample.id,
                        field_name=cf.field_name,
                        field_value=cf.field_value,
                    )
                )
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
            snapshot=serialize_entity(sample),
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

        # Pre-fetch org_id for sequencing batch resolution
        exp_result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        exp_for_org = exp_result.scalar_one()
        org_id = exp_for_org.organization_id

        created = []
        for data in samples_data:
            sample_batch_id = None
            if data.sample_batch_code:
                sample_batch_id = await SampleService._resolve_sample_batch(
                    session, experiment_id, data.sample_batch_code
                )
            sequencing_batch_id = None
            if data.sequencing_batch_code:
                sequencing_batch_id = await SampleService._resolve_sequencing_batch(
                    session, org_id, data.sequencing_batch_code
                )

            sample = Sample(
                experiment_id=experiment_id,
                sample_batch_id=sample_batch_id,
                sequencing_batch_id=sequencing_batch_id,
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
                parent_sample_id=data.parent_sample_id,
                collection_timestamp=data.collection_timestamp,
                collection_method=data.collection_method,
                status="registered",
            )
            session.add(sample)
            await session.flush()

            if data.custom_fields:
                for cf in data.custom_fields:
                    session.add(
                        SampleCustomField(
                            sample_id=sample.id,
                            field_name=cf.field_name,
                            field_value=cf.field_value,
                        )
                    )
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
                snapshot=serialize_entity(sample),
            )
            created.append(sample)
        return created

    @staticmethod
    async def update_sample(session: AsyncSession, sample_id: int, user_id: int, data: SampleUpdate) -> Sample | None:
        result = await session.execute(
            select(Sample)
            .options(
                selectinload(Sample.sample_batch),
                selectinload(Sample.sequencing_batch),
                selectinload(Sample.custom_fields),
            )
            .where(Sample.id == sample_id)
        )
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

        # Resolve batch codes
        previous = {}
        updates = {}
        if data.sample_batch_code is not None:
            previous["sample_batch_id"] = str(sample.sample_batch_id) if sample.sample_batch_id else None
            sample.sample_batch_id = await SampleService._resolve_sample_batch(
                session, sample.experiment_id, data.sample_batch_code
            )
            updates["sample_batch_code"] = data.sample_batch_code
        if data.sequencing_batch_code is not None:
            previous["sequencing_batch_id"] = str(sample.sequencing_batch_id) if sample.sequencing_batch_id else None
            exp_result = await session.execute(select(Experiment).where(Experiment.id == sample.experiment_id))
            exp = exp_result.scalar_one()
            sample.sequencing_batch_id = await SampleService._resolve_sequencing_batch(
                session, exp.organization_id, data.sequencing_batch_code
            )
            updates["sequencing_batch_code"] = data.sequencing_batch_code
        for field in [
            "sample_id_external",
            "organism",
            "tissue_type",
            "donor_source",
            "treatment_condition",
            "chemistry_version",
            "viability_pct",
            "cell_count",
            "prep_notes",
            "molecule_type",
            "library_prep_method",
            "library_layout",
            "parent_sample_id",
            "collection_timestamp",
            "collection_method",
        ]:
            new_val = getattr(data, field, None)
            if new_val is not None:
                old_val = getattr(sample, field)
                previous[field] = str(old_val) if old_val is not None else None
                setattr(sample, field, new_val)
                updates[field] = str(new_val) if new_val is not None else None

        # Handle custom fields (delete-and-replace)
        if data.custom_fields is not None:
            existing = await session.execute(select(SampleCustomField).where(SampleCustomField.sample_id == sample_id))
            for row in existing.scalars().all():
                await session.delete(row)
            await session.flush()
            for cf in data.custom_fields:
                session.add(
                    SampleCustomField(
                        sample_id=sample_id,
                        field_name=cf.field_name,
                        field_value=cf.field_value,
                    )
                )
            updates["custom_fields"] = [
                {"field_name": cf.field_name, "field_value": cf.field_value} for cf in data.custom_fields
            ]

        if updates:
            # Capture snapshot before flush to avoid lazy-load issues
            snap = serialize_entity(sample)
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="sample",
                entity_id=sample.id,
                action="update",
                details=updates,
                previous_value=previous,
                snapshot=snap,
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
        snap = serialize_entity(sample)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="sample",
            entity_id=sample.id,
            action="qc_update",
            details={"qc_status": qc_status, "qc_notes": qc_notes},
            previous_value=previous,
            snapshot=snap,
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
        snap = serialize_entity(sample)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="sample",
            entity_id=sample.id,
            action="status_change",
            details={"status": new_status},
            previous_value={"status": old_status},
            snapshot=snap,
        )
        return sample

    @staticmethod
    async def list_samples(
        session: AsyncSession,
        experiment_id: int,
        sample_batch_id: int | None = None,
        qc_status: str | None = None,
        status: str | None = None,
    ) -> list[Sample]:
        query = (
            select(Sample)
            .options(
                selectinload(Sample.sample_batch),
                selectinload(Sample.sequencing_batch),
                selectinload(Sample.custom_fields),
            )
            .where(Sample.experiment_id == experiment_id)
        )
        if sample_batch_id is not None:
            query = query.where(Sample.sample_batch_id == sample_batch_id)
        if qc_status is not None:
            query = query.where(Sample.qc_status == qc_status)
        if status is not None:
            query = query.where(Sample.status == status)

        result = await session.execute(query.order_by(Sample.created_at.desc()))
        return list(result.scalars().all())

    @staticmethod
    async def get_sample(session: AsyncSession, sample_id: int) -> Sample | None:
        result = await session.execute(
            select(Sample)
            .options(
                selectinload(Sample.sample_batch),
                selectinload(Sample.sequencing_batch),
                selectinload(Sample.custom_fields),
            )
            .where(Sample.id == sample_id)
        )
        return result.scalar_one_or_none()
