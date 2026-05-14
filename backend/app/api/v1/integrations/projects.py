"""Projects endpoints for /api/v1/integrations.

Upsert on POST by external_id; PATCH disallows status changes. Custom fields
are delta-applied (null deletes the row)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.integrations.dependencies import require_api_key_permission
from app.database import get_session
from app.models.project import Project
from app.models.project_custom_field import ProjectCustomField
from app.schemas.integrations.common import CustomFieldOut
from app.schemas.integrations.project import (
    ProjectCreate,
    ProjectListOut,
    ProjectOut,
    ProjectUpdate,
)
from app.services import idempotency_service
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import (
    INTEGRATION_PROJECT_CREATED,
    INTEGRATION_PROJECT_UPDATED,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


def _project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        external_id=project.external_id,
        name=project.name,
        code=project.code,
        description=project.description,
        hypothesis=project.hypothesis,
        status=project.status,
        created_at=project.created_at,
        custom_fields=[
            CustomFieldOut(field_name=cf.field_name, field_value=cf.field_value)
            for cf in (project.custom_fields or [])
        ],
    )


async def _apply_custom_fields(
    session: AsyncSession,
    project_id: int,
    existing_fields: list[ProjectCustomField] | None,
    fields_in: list[dict] | None,
) -> None:
    """Delta-apply custom_fields onto a project. `existing_fields` must be the
    already-loaded list (or None on first create where there are none)."""
    if fields_in is None:
        return
    existing = {cf.field_name: cf for cf in (existing_fields or [])}
    for field in fields_in:
        name = field["field_name"]
        value = field.get("field_value")
        if value is None and name in existing:
            await session.delete(existing[name])
        elif name in existing:
            existing[name].field_value = value
        else:
            session.add(ProjectCustomField(project_id=project_id, field_name=name, field_value=value))
    await session.flush()


@router.post(
    "",
    response_model=ProjectOut,
    summary="Create or upsert a project",
    description=(
        "Creates a project, or upserts an existing one if `external_id` is "
        "provided and a row with the same `(org, external_id)` already exists. "
        "Returns 201 on create, 200 on upsert match. `Idempotency-Key` is "
        "honored on retries."
    ),
)
async def create_project(
    body: ProjectCreate,
    request: Request,
    response: Response,
    user: dict = require_api_key_permission("projects", "create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    api_key_id = int(user["api_key_id"])
    org_id = int(user["org_id"])
    body_dict = body.model_dump(mode="json")

    if idempotency_key is not None:
        cached = await idempotency_service.lookup(session, api_key_id, idempotency_key)
        if cached is not None:
            current_fp = idempotency_service.fingerprint("POST", "/api/v1/integrations/projects", body_dict)
            if cached.request_fingerprint != current_fp:
                raise HTTPException(422, "idempotency_key_reused_with_different_body")
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = cached.response_status
            return cached.response_body

    # Upsert by external_id if supplied
    existing: Project | None = None
    if body.external_id is not None:
        result = await session.execute(
            select(Project)
            .options(selectinload(Project.custom_fields))
            .where(
                Project.organization_id == org_id,
                Project.external_id == body.external_id,
            )
        )
        existing = result.scalar_one_or_none()

    if existing is not None:
        for field in ("name", "description", "hypothesis"):
            new_val = getattr(body, field, None)
            if new_val is not None:
                setattr(existing, field, new_val)
        if body.code is not None:
            existing.code = body.code
        await session.flush()
        await _apply_custom_fields(
            session,
            existing.id,
            list(existing.custom_fields or []),
            [cf.model_dump() for cf in body.custom_fields] if body.custom_fields else None,
        )
        await log_action(
            session,
            user_id=int(user["sub"]),
            api_key_id=api_key_id,
            entity_type="project",
            entity_id=existing.id,
            action="upsert_matched",
            details={"external_id": body.external_id},
        )
        # Reload with custom_fields eager-loaded
        result = await session.execute(
            select(Project).options(selectinload(Project.custom_fields)).where(Project.id == existing.id)
        )
        existing = result.scalar_one()
        out = _project_out(existing)
        await event_bus.emit(
            INTEGRATION_PROJECT_UPDATED,
            {
                "organization_id": org_id,
                "data": {"project_id": existing.id, "external_id": existing.external_id},
            },
        )
        if idempotency_key is not None:
            await idempotency_service.record(
                session,
                api_key_id,
                idempotency_key,
                idempotency_service.fingerprint("POST", "/api/v1/integrations/projects", body_dict),
                200,
                json.loads(out.model_dump_json()),
            )
        await session.commit()
        response.status_code = 200
        return out

    project = Project(
        organization_id=org_id,
        external_id=body.external_id,
        name=body.name,
        code=body.code,
        description=body.description,
        hypothesis=body.hypothesis,
        owner_user_id=int(user["sub"]),
        created_by_user_id=int(user["sub"]),
    )
    session.add(project)
    await session.flush()
    # Freshly created; no existing fields to merge with.
    await _apply_custom_fields(
        session,
        project.id,
        None,
        [cf.model_dump() for cf in body.custom_fields] if body.custom_fields else None,
    )
    await log_action(
        session,
        user_id=int(user["sub"]),
        api_key_id=api_key_id,
        entity_type="project",
        entity_id=project.id,
        action="created",
        details={"external_id": body.external_id, "name": body.name},
    )
    result = await session.execute(
        select(Project).options(selectinload(Project.custom_fields)).where(Project.id == project.id)
    )
    project = result.scalar_one()
    out = _project_out(project)
    await event_bus.emit(
        INTEGRATION_PROJECT_CREATED,
        {
            "organization_id": org_id,
            "data": {
                "project_id": project.id,
                "external_id": project.external_id,
                "name": project.name,
            },
        },
    )
    if idempotency_key is not None:
        await idempotency_service.record(
            session,
            api_key_id,
            idempotency_key,
            idempotency_service.fingerprint("POST", "/api/v1/integrations/projects", body_dict),
            201,
            json.loads(out.model_dump_json()),
        )
    await session.commit()
    response.status_code = 201
    return out


@router.get(
    "",
    response_model=ProjectListOut,
    summary="List projects",
)
async def list_projects(
    request: Request,
    user: dict = require_api_key_permission("projects", "view"),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None),
    external_id: str | None = Query(None),
    q: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    org_id = int(user["org_id"])
    stmt = select(Project).options(selectinload(Project.custom_fields)).where(Project.organization_id == org_id)
    if status:
        stmt = stmt.where(Project.status == status)
    if external_id:
        stmt = stmt.where(Project.external_id == external_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Project.name.ilike(like)) | (Project.code.ilike(like)))
    if cursor:
        try:
            stmt = stmt.where(Project.id < int(cursor))
        except ValueError as e:
            raise HTTPException(400, "invalid_cursor") from e
    stmt = stmt.order_by(Project.id.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = str(rows[limit - 1].id)
        rows = rows[:limit]
    return ProjectListOut(items=[_project_out(p) for p in rows], next_cursor=next_cursor)


@router.get(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Get a project by id",
)
async def get_project(
    project_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("projects", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Project)
        .options(selectinload(Project.custom_fields))
        .where(Project.id == project_id, Project.organization_id == org_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "project_not_found")
    return _project_out(row)


@router.get(
    "/by-external/{external_id}",
    response_model=ProjectOut,
    summary="Get a project by external_id",
)
async def get_project_by_external(
    external_id: str,
    user: dict = require_api_key_permission("projects", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Project)
        .options(selectinload(Project.custom_fields))
        .where(Project.organization_id == org_id, Project.external_id == external_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "project_not_found")
    return _project_out(row)


@router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Update a project (status not permitted)",
)
async def patch_project(
    body: ProjectUpdate,
    project_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("projects", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Project)
        .options(selectinload(Project.custom_fields))
        .where(Project.id == project_id, Project.organization_id == org_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "project_not_found")
    updates: dict = {}
    for field in ("name", "description", "hypothesis"):
        new_val = getattr(body, field, None)
        if new_val is not None:
            setattr(row, field, new_val)
            updates[field] = new_val
    await session.flush()
    await _apply_custom_fields(
        session,
        row.id,
        list(row.custom_fields or []),
        [cf.model_dump() for cf in body.custom_fields] if body.custom_fields else None,
    )
    await log_action(
        session,
        user_id=int(user["sub"]),
        api_key_id=int(user["api_key_id"]),
        entity_type="project",
        entity_id=row.id,
        action="updated",
        details=updates,
    )
    await event_bus.emit(
        INTEGRATION_PROJECT_UPDATED,
        {
            "organization_id": org_id,
            "data": {
                "project_id": row.id,
                "external_id": row.external_id,
                "changed_fields": list(updates.keys()),
            },
        },
    )
    await session.commit()
    # Force a fresh selectinload of custom_fields by expiring the relationship.
    await session.refresh(row, attribute_names=["custom_fields"])
    return _project_out(row)
