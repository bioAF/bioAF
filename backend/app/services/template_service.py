from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.experiment_template import ExperimentTemplate
from app.schemas.template import TemplateCreate, TemplateUpdate
from app.services.audit_service import log_action


class TemplateService:
    @staticmethod
    async def create_template(
        session: AsyncSession, org_id: int, user_id: int, data: TemplateCreate
    ) -> ExperimentTemplate:
        template = ExperimentTemplate(
            organization_id=org_id,
            name=data.name,
            description=data.description,
            required_fields_json=data.required_fields_json,
            custom_fields_schema_json=data.custom_fields_schema_json,
            naming_profile_id=data.naming_profile_id,
            created_by_user_id=user_id,
        )
        session.add(template)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="template",
            entity_id=template.id,
            action="create",
            details={"name": data.name},
        )
        return template

    @staticmethod
    async def update_template(
        session: AsyncSession, template_id: int, user_id: int, data: TemplateUpdate
    ) -> ExperimentTemplate | None:
        result = await session.execute(select(ExperimentTemplate).where(ExperimentTemplate.id == template_id))
        template = result.scalar_one_or_none()
        if not template:
            return None

        previous = {}
        updates = {}
        for field in [
            "name",
            "description",
            "required_fields_json",
            "custom_fields_schema_json",
            "naming_profile_id",
        ]:
            new_val = getattr(data, field, None)
            if new_val is not None:
                old_val = getattr(template, field)
                previous[field] = str(old_val) if old_val is not None else None
                setattr(template, field, new_val)
                updates[field] = str(new_val) if new_val is not None else None

        if updates:
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="template",
                entity_id=template.id,
                action="update",
                details=updates,
                previous_value=previous,
            )
        return template

    @staticmethod
    async def list_templates(session: AsyncSession, org_id: int) -> list[ExperimentTemplate]:
        result = await session.execute(
            select(ExperimentTemplate)
            .options(selectinload(ExperimentTemplate.created_by))
            .where(ExperimentTemplate.organization_id == org_id)
            .order_by(ExperimentTemplate.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_template(session: AsyncSession, template_id: int, org_id: int) -> ExperimentTemplate | None:
        result = await session.execute(
            select(ExperimentTemplate)
            .options(selectinload(ExperimentTemplate.created_by))
            .where(
                ExperimentTemplate.id == template_id,
                ExperimentTemplate.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def validate_against_template(
        template: ExperimentTemplate,
        sample_data: dict,
    ) -> list[str]:
        errors = []
        if not template.required_fields_json:
            return errors

        sample_fields = template.required_fields_json.get("sample_fields", [])
        for field in sample_fields:
            val = sample_data.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"Required field '{field}' is missing or empty")
        return errors
