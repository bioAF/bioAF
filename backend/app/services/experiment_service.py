import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models.audit_log import AuditLog
from app.models.batch import Batch
from app.models.experiment import Experiment, EXPERIMENT_STATUS_TRANSITIONS
from app.models.experiment_custom_field import ExperimentCustomField
from app.models.experiment_field_default import ExperimentFieldDefault
from app.models.sample import Sample
from app.schemas.experiment import ExperimentCreate, ExperimentUpdate
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import EXPERIMENT_STATUS_CHANGED
from app.services.vocabulary_validator import VocabularyValidator


class ExperimentService:
    @staticmethod
    async def create_experiment(session: AsyncSession, org_id: int, user_id: int, data: ExperimentCreate) -> Experiment:
        await VocabularyValidator.validate_experiment_fields(session, {"design_type": data.design_type})
        experiment = Experiment(
            organization_id=org_id,
            project_id=data.project_id,
            template_id=data.template_id,
            name=data.name,
            hypothesis=data.hypothesis,
            description=data.description,
            start_date=data.start_date,
            expected_sample_count=data.expected_sample_count,
            owner_user_id=user_id,
            status="registered",
            design_type=data.design_type,
            protocol_version=data.protocol_version,
            variables_json=data.variables_json,
        )
        session.add(experiment)
        await session.flush()

        if data.custom_fields:
            for cf in data.custom_fields:
                custom_field = ExperimentCustomField(
                    experiment_id=experiment.id,
                    field_name=cf.field_name,
                    field_value=cf.field_value,
                    field_type=cf.field_type,
                )
                session.add(custom_field)
            await session.flush()

        if data.field_defaults:
            for fd in data.field_defaults:
                field_default = ExperimentFieldDefault(
                    experiment_id=experiment.id,
                    field_name=fd.field_name,
                    default_value=fd.default_value,
                    is_required=fd.is_required,
                )
                session.add(field_default)
            await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action="create",
            details={"name": data.name, "status": "registered"},
        )
        return experiment

    @staticmethod
    async def update_experiment(
        session: AsyncSession, experiment_id: int, org_id: int, user_id: int, data: ExperimentUpdate
    ) -> Experiment | None:
        result = await session.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        experiment = result.scalar_one_or_none()
        if not experiment:
            return None

        previous = {}
        updates = {}
        for field in [
            "name",
            "hypothesis",
            "description",
            "start_date",
            "expected_sample_count",
            "design_type",
            "protocol_version",
            "variables_json",
        ]:
            new_val = getattr(data, field, None)
            if new_val is not None:
                old_val = getattr(experiment, field)
                previous[field] = str(old_val) if old_val is not None else None
                setattr(experiment, field, new_val)
                updates[field] = str(new_val) if new_val is not None else None

        if data.field_defaults is not None:
            # Delete existing field defaults and replace
            existing = await session.execute(
                select(ExperimentFieldDefault).where(ExperimentFieldDefault.experiment_id == experiment_id)
            )
            for row in existing.scalars().all():
                await session.delete(row)
            await session.flush()

            for fd in data.field_defaults:
                field_default = ExperimentFieldDefault(
                    experiment_id=experiment_id,
                    field_name=fd.field_name,
                    default_value=fd.default_value,
                    is_required=fd.is_required,
                )
                session.add(field_default)
            updates["field_defaults"] = [
                {"field_name": fd.field_name, "default_value": fd.default_value, "is_required": fd.is_required}
                for fd in data.field_defaults
            ]

        if updates:
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="experiment",
                entity_id=experiment.id,
                action="update",
                details=updates,
                previous_value=previous,
            )
        return experiment

    @staticmethod
    async def update_status(
        session: AsyncSession, experiment_id: int, org_id: int, user_id: int, new_status: str
    ) -> Experiment:
        result = await session.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        experiment = result.scalar_one_or_none()
        if not experiment:
            raise HTTPException(404, "Experiment not found")

        allowed = EXPERIMENT_STATUS_TRANSITIONS.get(experiment.status, [])
        if new_status not in allowed:
            raise HTTPException(
                400,
                f"Cannot transition from '{experiment.status}' to '{new_status}'. "
                f"Next valid status: {', '.join(allowed) if allowed else 'none (terminal state)'}.",
            )

        old_status = experiment.status
        experiment.status = new_status
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action="status_change",
            details={"status": new_status},
            previous_value={"status": old_status},
        )

        asyncio.create_task(
            event_bus.emit(
                EXPERIMENT_STATUS_CHANGED,
                {
                    "event_type": EXPERIMENT_STATUS_CHANGED,
                    "org_id": org_id,
                    "user_id": user_id,
                    "entity_type": "experiment",
                    "entity_id": experiment.id,
                    "title": f"Experiment '{experiment.name}' status changed to {new_status}",
                    "message": f"Status changed from {old_status} to {new_status}",
                    "summary": f"Experiment '{experiment.name}' is now {new_status}",
                    "metadata": {"old_status": old_status, "new_status": new_status},
                },
            )
        )

        return experiment

    @staticmethod
    async def get_experiment(session: AsyncSession, experiment_id: int, org_id: int) -> Experiment | None:
        result = await session.execute(
            select(Experiment)
            .options(
                selectinload(Experiment.samples),
                selectinload(Experiment.batches),
                selectinload(Experiment.custom_fields),
                selectinload(Experiment.field_defaults),
                selectinload(Experiment.project),
                selectinload(Experiment.owner),
                selectinload(Experiment.template),
            )
            .where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_experiments(
        session: AsyncSession,
        org_id: int,
        page: int = 1,
        page_size: int = 25,
        project_id: int | None = None,
        status: str | None = None,
        owner_user_id: int | None = None,
        search: str | None = None,
    ) -> tuple[list[Experiment], int]:
        base_query = select(Experiment).where(Experiment.organization_id == org_id)

        if project_id is not None:
            base_query = base_query.where(Experiment.project_id == project_id)
        if status is not None:
            base_query = base_query.where(Experiment.status == status)
        if owner_user_id is not None:
            base_query = base_query.where(Experiment.owner_user_id == owner_user_id)
        if search:
            search_filter = f"%{search}%"
            base_query = base_query.where(
                Experiment.name.ilike(search_filter)
                | Experiment.hypothesis.ilike(search_filter)
                | Experiment.description.ilike(search_filter)
            )

        count_result = await session.execute(select(func.count()).select_from(base_query.subquery()))
        total = count_result.scalar()

        result = await session.execute(
            base_query.options(
                selectinload(Experiment.project),
                selectinload(Experiment.owner),
                selectinload(Experiment.samples),
                selectinload(Experiment.batches),
                selectinload(Experiment.template),
            )
            .order_by(Experiment.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        experiments = list(result.scalars().all())
        return experiments, total

    @staticmethod
    async def delete_experiment(
        session: AsyncSession, experiment_id: int, org_id: int, user_id: int
    ) -> Experiment | None:
        result = await session.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        experiment = result.scalar_one_or_none()
        if not experiment:
            return None

        old_status = experiment.status
        experiment.status = "deleted"
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action="delete",
            details={"status": "deleted"},
            previous_value={"status": old_status},
        )
        return experiment

    @staticmethod
    async def get_audit_trail(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[AuditLog], int]:
        # Verify experiment belongs to org
        exp_result = await session.execute(
            select(Experiment.id).where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        if not exp_result.scalar_one_or_none():
            return [], 0

        # Get sample IDs for this experiment
        sample_result = await session.execute(select(Sample.id).where(Sample.experiment_id == experiment_id))
        sample_ids = [r[0] for r in sample_result.all()]

        # Build audit query for experiment + its samples
        from sqlalchemy import or_

        conditions = [(AuditLog.entity_type == "experiment") & (AuditLog.entity_id == experiment_id)]
        if sample_ids:
            conditions.append((AuditLog.entity_type == "sample") & (AuditLog.entity_id.in_(sample_ids)))
        conditions.append(
            (AuditLog.entity_type == "batch")
            & (AuditLog.entity_id.in_(select(Batch.id).where(Batch.experiment_id == experiment_id)))
        )

        base_query = select(AuditLog).where(or_(*conditions))

        count_result = await session.execute(select(func.count()).select_from(base_query.subquery()))
        total = count_result.scalar()

        result = await session.execute(
            base_query.order_by(AuditLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        entries = list(result.scalars().all())
        return entries, total
