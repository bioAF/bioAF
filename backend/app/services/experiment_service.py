from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError, ValidationError
from app.models.audit_log import AuditLog
from app.models.sample_batch import SampleBatch
from app.services.snapshot_utils import serialize_entity
from app.models.experiment import Experiment, EXPERIMENT_STATUS_TRANSITIONS
from app.models.experiment_custom_field import ExperimentCustomField
from app.models.experiment_field_default import DEFAULTABLE_SAMPLE_FIELDS, ExperimentFieldDefault
from app.models.experiment_template import ExperimentTemplate
from app.models.naming_profile import NamingProfile
from app.models.project import Project
from app.models.sample import Sample
from app.schemas.experiment import CustomFieldValue, ExperimentCreate, ExperimentUpdate, FieldDefaultValue
from app.services.audit_service import log_action
from app.services.code_service import CodeService
from app.services.event_types import (
    EXPERIMENT_STATUS_CHANGED,
    INTEGRATION_EXPERIMENT_CREATED,
    INTEGRATION_EXPERIMENT_UPDATED,
)
from app.services.post_commit_events import defer_event
from app.services.vocabulary_validator import VocabularyValidator


def _escape_like(text: str) -> str:
    """``text`` matched literally inside an ILIKE pattern (its % and _ are not wildcards)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _custom_field_state(rows) -> list[tuple]:
    return sorted((r.field_name, r.field_value, r.field_type, bool(r.is_required)) for r in rows)


def _field_default_state(rows) -> list[tuple]:
    return sorted((r.field_name, r.default_value, bool(r.is_required)) for r in rows)


class ExperimentService:
    # -- webhook-producing primitives: mutation, audit and the deferred event live together ----

    @staticmethod
    async def _announce_created(session: AsyncSession, experiment: Experiment) -> None:
        from app.services import entity_references, webhook_events

        ref = await entity_references.experiment_reference(session, experiment)
        defer_event(
            session, INTEGRATION_EXPERIMENT_CREATED, webhook_events.experiment_created(experiment.organization_id, ref)
        )

    @staticmethod
    async def _announce_updated(session: AsyncSession, experiment: Experiment, changed: set[str]) -> None:
        """``experiment.updated`` for the fields that actually changed; nothing when none did."""
        if not changed:
            return
        from app.services import entity_references, webhook_events

        ref = await entity_references.experiment_reference(session, experiment)
        defer_event(
            session,
            INTEGRATION_EXPERIMENT_UPDATED,
            webhook_events.experiment_updated(experiment.organization_id, ref, changed),
        )

    @staticmethod
    async def _change_status(
        session: AsyncSession,
        experiment: Experiment,
        new_status: str,
        user_id: int | None,
        *,
        action: str = "status_change",
        details: dict | None = None,
    ) -> None:
        """The one status writer: set, audit, and defer ``experiment.status_changed``.

        The deferred payload keeps the notification fields its other subscribers read (``org_id``,
        title, message, metadata) and carries the public snapshot in ``data``. Callers validate the
        transition and own the transaction.
        """
        from app.services import entity_references, webhook_events

        old_status = experiment.status
        experiment.status = new_status
        snap = serialize_entity(experiment)
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action=action,
            details=details if details is not None else {"status": new_status},
            previous_value={"status": old_status},
            snapshot=snap,
        )
        ref = await entity_references.experiment_reference(session, experiment)
        defer_event(
            session,
            EXPERIMENT_STATUS_CHANGED,
            {
                "event_type": EXPERIMENT_STATUS_CHANGED,
                "org_id": experiment.organization_id,
                "user_id": user_id,
                "entity_type": "experiment",
                "entity_id": experiment.id,
                "title": f"Experiment '{experiment.name}' status changed to {new_status}",
                "message": f"Status changed from {old_status} to {new_status}",
                "summary": f"Experiment '{experiment.name}' is now {new_status}",
                "metadata": {"old_status": old_status, "new_status": new_status},
                "data": webhook_events.experiment_status_data(ref, old_status, new_status),
            },
        )

    @staticmethod
    async def create_experiment(
        session: AsyncSession, org_id: int, user_id: int, data: ExperimentCreate, *, via_assistant: bool = False
    ) -> Experiment:
        await VocabularyValidator.validate_experiment_fields(session, {"design_type": data.design_type})
        await ExperimentService._require_owned_links(session, org_id, data)
        code = await CodeService.next_experiment_code(session, org_id, data.project_id)
        experiment = Experiment(
            organization_id=org_id,
            project_id=data.project_id,
            template_id=data.template_id,
            name=data.name,
            code=code,
            hypothesis=data.hypothesis,
            description=data.description,
            start_date=data.start_date,
            expected_sample_count=data.expected_sample_count,
            owner_user_id=user_id,
            status="registered",
            design_type=data.design_type,
            protocol_version=data.protocol_version,
            variables_json=data.variables_json,
            column_aliases=data.column_aliases,
            naming_profile_id=data.naming_profile_id,
        )
        session.add(experiment)
        await session.flush()

        merged_defaults, merged_customs = await ExperimentService._merge_template_config(session, data)

        for fd in merged_defaults:
            session.add(
                ExperimentFieldDefault(
                    experiment_id=experiment.id,
                    field_name=fd.field_name,
                    default_value=fd.default_value,
                    is_required=fd.is_required,
                )
            )
        for cf in merged_customs:
            session.add(
                ExperimentCustomField(
                    experiment_id=experiment.id,
                    field_name=cf.field_name,
                    field_value=cf.field_value,
                    field_type=cf.field_type,
                    is_required=cf.is_required,
                )
            )
        if merged_defaults or merged_customs:
            await session.flush()

        create_details: dict[str, object] = {"name": data.name, "status": "registered"}
        if via_assistant:
            create_details["via_assistant"] = True
        await log_action(
            session,
            user_id=user_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action="create",
            details=create_details,
            snapshot=serialize_entity(experiment),
        )
        await ExperimentService._announce_created(session, experiment)
        return experiment

    @staticmethod
    async def _merge_custom_fields(
        session: AsyncSession, experiment_id: int, existing_rows, fields_in: list[dict] | None
    ) -> bool:
        """The Integration API's merge semantics: a null value removes a field, others upsert.
        Returns whether anything actually changed."""
        if fields_in is None:
            return False
        existing = {cf.field_name: cf for cf in (existing_rows or [])}
        changed = False
        for field in fields_in:
            name = field["field_name"]
            value = field.get("field_value")
            if value is None:
                if name in existing:
                    await session.delete(existing[name])
                    changed = True
            elif name in existing:
                if existing[name].field_value != value:
                    existing[name].field_value = value
                    changed = True
            else:
                session.add(
                    ExperimentCustomField(
                        experiment_id=experiment_id, field_name=name, field_value=value, field_type="text"
                    )
                )
                changed = True
        await session.flush()
        return changed

    @staticmethod
    async def create_from_integration(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        api_key_id: int,
        project_id: int | None,
        external_id: str,
        name: str,
        hypothesis: str | None,
        description: str | None,
        expected_sample_count: int | None,
        variables_json: dict | None,
        custom_fields: list[dict] | None,
    ) -> Experiment:
        """An Integration API create: always ``registered``, audited with the API key, announced once.
        The caller has authorized the project and checked the external ID, and owns the transaction."""
        code = await CodeService.next_experiment_code(session, org_id, project_id)
        experiment = Experiment(
            organization_id=org_id,
            project_id=project_id,
            external_id=external_id,
            name=name,
            code=code,
            hypothesis=hypothesis,
            description=description,
            expected_sample_count=expected_sample_count,
            variables_json=variables_json,
            owner_user_id=user_id,
            status="registered",
        )
        session.add(experiment)
        await session.flush()
        await ExperimentService._merge_custom_fields(session, experiment.id, None, custom_fields)
        await log_action(
            session,
            user_id=user_id,
            api_key_id=api_key_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action="created",
            details={"external_id": external_id, "name": name},
        )
        await ExperimentService._announce_created(session, experiment)
        return experiment

    @staticmethod
    async def patch_from_integration(
        session: AsyncSession,
        experiment: Experiment,
        *,
        user_id: int,
        api_key_id: int,
        fields: dict,
        custom_fields: list[dict] | None,
    ) -> None:
        """An Integration API PATCH (never status): set supplied fields, merge custom fields, audit, and
        announce the fields that actually changed."""
        updates: dict = {}
        changed: set[str] = set()
        for field, new_val in fields.items():
            if new_val is None:
                continue
            if getattr(experiment, field) != new_val:
                changed.add(field)
            setattr(experiment, field, new_val)
            updates[field] = new_val
        await session.flush()
        if await ExperimentService._merge_custom_fields(
            session, experiment.id, list(experiment.custom_fields or []), custom_fields
        ):
            changed.add("custom_fields")
        await log_action(
            session,
            user_id=user_id,
            api_key_id=api_key_id,
            entity_type="experiment",
            entity_id=experiment.id,
            action="updated",
            details=updates,
        )
        await ExperimentService._announce_updated(session, experiment, changed)

    @staticmethod
    async def claim(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
        user_id: int,
        *,
        name: str | None,
        description: str | None,
    ) -> Experiment:
        """Claim an existing unclaimed experiment of this organization; announce what changed."""
        experiment = (
            await session.execute(
                select(Experiment).where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
            )
        ).scalar_one_or_none()
        if experiment is None:
            raise NotFoundError("Experiment not found", plain_body=True)
        if not experiment.is_unclaimed:
            raise ValidationError("Experiment is not unclaimed", plain_body=True)
        changed = {"is_unclaimed"}
        experiment.is_unclaimed = False
        for field, value in (("owner_user_id", user_id), ("name", name), ("description", description)):
            if value and getattr(experiment, field) != value:
                setattr(experiment, field, value)
                changed.add(field)
        await session.flush()
        await log_action(session, user_id=user_id, entity_type="experiment", entity_id=experiment.id, action="claim")
        await ExperimentService._announce_updated(session, experiment, changed)
        return experiment

    @staticmethod
    async def _require_owned_links(session: AsyncSession, org_id: int, data: ExperimentCreate) -> None:
        """Refuse a project, template or naming profile this organization does not own.

        Missing and foreign ids are refused alike, so the answer says nothing
        about another organization's records. Checked before the row exists:
        the response names the linked project and template.
        """
        for model, value, label in (
            (Project, data.project_id, "Project"),
            (ExperimentTemplate, data.template_id, "Experiment template"),
            (NamingProfile, data.naming_profile_id, "Naming profile"),
        ):
            if value is None:
                continue
            owned = await session.scalar(select(model.id).where(model.id == value, model.organization_id == org_id))
            if owned is None:
                raise NotFoundError(f"{label} {value} not found")

    @staticmethod
    async def _merge_template_config(
        session: AsyncSession, data: ExperimentCreate
    ) -> tuple[list[FieldDefaultValue], list[CustomFieldValue]]:
        """Merge template-defined required/custom fields with user-supplied values.

        User-supplied default_value/field_value wins; template's is_required and
        field_type are layered on. Field names not in DEFAULTABLE_SAMPLE_FIELDS are
        skipped for field_defaults (they cannot be persisted there).
        """
        user_defaults = {fd.field_name: fd for fd in (data.field_defaults or [])}
        user_customs = {cf.field_name: cf for cf in (data.custom_fields or [])}

        if data.template_id:
            template = await session.get(ExperimentTemplate, data.template_id)
            if template:
                for field_name in (template.required_fields_json or {}).get("sample_fields", []) or []:
                    if field_name not in DEFAULTABLE_SAMPLE_FIELDS:
                        continue
                    existing = user_defaults.get(field_name)
                    user_defaults[field_name] = FieldDefaultValue(
                        field_name=field_name,
                        default_value=existing.default_value if existing else None,
                        is_required=True,
                    )

                for tf in (template.custom_fields_schema_json or {}).get("fields", []) or []:
                    name = tf.get("name")
                    if not name:
                        continue
                    ftype = tf.get("type") or "string"
                    required = bool(tf.get("required", False))
                    existing = user_customs.get(name)
                    user_customs[name] = CustomFieldValue(
                        field_name=name,
                        field_value=existing.field_value if existing else "",
                        field_type=ftype,
                        is_required=(existing.is_required if existing else False) or required,
                    )

        return list(user_defaults.values()), list(user_customs.values())

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
        changed: set[str] = set()
        for field in [
            "name",
            "hypothesis",
            "description",
            "start_date",
            "expected_sample_count",
            "design_type",
            "protocol_version",
            "variables_json",
            "column_aliases",
            "naming_profile_id",
        ]:
            new_val = getattr(data, field, None)
            if new_val is not None:
                old_val = getattr(experiment, field)
                previous[field] = str(old_val) if old_val is not None else None
                setattr(experiment, field, new_val)
                updates[field] = str(new_val) if new_val is not None else None
                if old_val != new_val:
                    changed.add(field)

        if data.field_defaults is not None:
            # Delete existing field defaults and replace
            existing = await session.execute(
                select(ExperimentFieldDefault).where(ExperimentFieldDefault.experiment_id == experiment_id)
            )
            existing_rows = existing.scalars().all()
            before = _field_default_state(existing_rows)
            for row in existing_rows:
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
            after = sorted((fd.field_name, fd.default_value, bool(fd.is_required)) for fd in data.field_defaults)
            if before != after:
                changed.add("field_defaults")

        if data.custom_fields is not None:
            # Delete existing custom fields and replace
            existing_cf = await session.execute(
                select(ExperimentCustomField).where(ExperimentCustomField.experiment_id == experiment_id)
            )
            existing_rows = existing_cf.scalars().all()
            before = _custom_field_state(existing_rows)
            for row in existing_rows:
                await session.delete(row)
            await session.flush()

            for cf in data.custom_fields:
                custom_field = ExperimentCustomField(
                    experiment_id=experiment_id,
                    field_name=cf.field_name,
                    field_value=cf.field_value,
                    field_type=cf.field_type,
                    is_required=cf.is_required,
                )
                session.add(custom_field)
            updates["custom_fields"] = [
                {"field_name": cf.field_name, "field_value": cf.field_value, "is_required": cf.is_required}
                for cf in data.custom_fields
            ]
            after = sorted(
                (cf.field_name, cf.field_value, cf.field_type, bool(cf.is_required)) for cf in data.custom_fields
            )
            if before != after:
                changed.add("custom_fields")

        if updates:
            snap = serialize_entity(experiment)
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="experiment",
                entity_id=experiment.id,
                action="update",
                details=updates,
                previous_value=previous,
                snapshot=snap,
            )
        await ExperimentService._announce_updated(session, experiment, changed)
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
            raise NotFoundError("Experiment not found", plain_body=True)

        allowed = EXPERIMENT_STATUS_TRANSITIONS.get(experiment.status, [])
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot transition from '{experiment.status}' to '{new_status}'. "
                f"Next valid status: {', '.join(allowed) if allowed else 'none (terminal state)'}.",
                plain_body=True,
            )

        await ExperimentService._change_status(session, experiment, new_status, user_id)
        return experiment

    @staticmethod
    async def get_experiment(session: AsyncSession, experiment_id: int, org_id: int) -> Experiment | None:
        result = await session.execute(
            select(Experiment)
            .options(
                selectinload(Experiment.samples),
                selectinload(Experiment.sample_batches),
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
        name: str | None = None,
    ) -> tuple[list[Experiment], int]:
        """One page, newest first with id as the tie-breaker (offset pages never skip or repeat a row).

        Deleted experiments are left out unless ``status`` asks for them; ``name`` matches names only, while
        ``search`` also matches the hypothesis and description.
        """
        base_query = select(Experiment).where(Experiment.organization_id == org_id)

        if project_id is not None:
            base_query = base_query.where(Experiment.project_id == project_id)
        if status is not None:
            base_query = base_query.where(Experiment.status == status)
        else:
            base_query = base_query.where(Experiment.status != "deleted")
        if name:
            base_query = base_query.where(Experiment.name.ilike(f"%{_escape_like(name)}%", escape="\\"))
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
                selectinload(Experiment.sample_batches),
                selectinload(Experiment.template),
            )
            .order_by(Experiment.created_at.desc(), Experiment.id.desc())
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

        # Soft deletion is a status change: it is audited as a delete and announced as one.
        await ExperimentService._change_status(
            session, experiment, "deleted", user_id, action="delete", details={"status": "deleted"}
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
            (AuditLog.entity_type.in_(["batch", "sample_batch"]))
            & (AuditLog.entity_id.in_(select(SampleBatch.id).where(SampleBatch.experiment_id == experiment_id)))
        )

        base_query = select(AuditLog).where(or_(*conditions))

        count_result = await session.execute(select(func.count()).select_from(base_query.subquery()))
        total = count_result.scalar()

        result = await session.execute(
            base_query.order_by(AuditLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        entries = list(result.scalars().all())
        return entries, total
