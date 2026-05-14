"""Experiments endpoints for /api/v1/integrations.

Status writes are not permitted on this surface. Creates are forced to
`registered`; PATCH rejects any payload field named `status`. External-system
status reads are allowed.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.integrations.dependencies import require_api_key_permission
from app.database import get_session
from app.models.experiment import Experiment
from app.models.experiment_custom_field import ExperimentCustomField
from app.models.project import Project
from app.schemas.integrations.common import CustomFieldOut
from app.schemas.integrations.experiment import (
    ExperimentCreate,
    ExperimentListOut,
    ExperimentOut,
    ExperimentUpdate,
)
from app.services import idempotency_service
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import (
    INTEGRATION_EXPERIMENT_CREATED,
    INTEGRATION_EXPERIMENT_UPDATED,
)

router = APIRouter(prefix="/experiments", tags=["Experiments"])


def _experiment_out(exp: Experiment) -> ExperimentOut:
    return ExperimentOut(
        id=exp.id,
        external_id=exp.external_id,
        name=exp.name,
        code=exp.code,
        project_id=exp.project_id,
        status=exp.status,
        hypothesis=exp.hypothesis,
        description=exp.description,
        expected_sample_count=exp.expected_sample_count,
        variables_json=exp.variables_json,
        created_at=exp.created_at,
        custom_fields=[
            CustomFieldOut(field_name=cf.field_name, field_value=cf.field_value)
            for cf in (exp.custom_fields or [])
        ],
    )


async def _apply_custom_fields(
    session: AsyncSession,
    experiment_id: int,
    existing_fields: list[ExperimentCustomField] | None,
    fields_in: list[dict] | None,
) -> None:
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
            session.add(
                ExperimentCustomField(
                    experiment_id=experiment_id,
                    field_name=name,
                    field_value=value,
                    field_type="text",
                )
            )
    await session.flush()


async def _resolve_project_id(session: AsyncSession, org_id: int, body: ExperimentCreate) -> int | None:
    if body.project_id is not None:
        # Verify it belongs to the caller's org.
        result = await session.execute(
            select(Project.id).where(Project.id == body.project_id, Project.organization_id == org_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "project_not_found")
        return body.project_id
    if body.project_external_id is not None:
        result = await session.execute(
            select(Project.id).where(
                Project.organization_id == org_id,
                Project.external_id == body.project_external_id,
            )
        )
        row_id = result.scalar_one_or_none()
        if row_id is None:
            raise HTTPException(404, "project_not_found")
        return row_id
    return None


def _reject_status_in_payload(body: dict) -> None:
    if "status" in body and body["status"] is not None:
        raise HTTPException(400, "status_writes_not_permitted")


@router.post(
    "",
    response_model=ExperimentOut,
    summary="Create or upsert an experiment",
)
async def create_experiment(
    body: ExperimentCreate,
    request: Request,
    response: Response,
    user: dict = require_api_key_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    raw_body = await request.body()
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                _reject_status_in_payload(parsed)
        except json.JSONDecodeError:
            pass

    api_key_id = int(user["api_key_id"])
    org_id = int(user["org_id"])
    body_dict = body.model_dump(mode="json")

    if idempotency_key is not None:
        cached = await idempotency_service.lookup(session, api_key_id, idempotency_key)
        if cached is not None:
            current_fp = idempotency_service.fingerprint("POST", "/api/v1/integrations/experiments", body_dict)
            if cached.request_fingerprint != current_fp:
                raise HTTPException(422, "idempotency_key_reused_with_different_body")
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = cached.response_status
            return cached.response_body

    project_id = await _resolve_project_id(session, org_id, body)

    existing: Experiment | None = None
    if body.external_id is not None:
        result = await session.execute(
            select(Experiment)
            .options(selectinload(Experiment.custom_fields))
            .where(
                Experiment.organization_id == org_id,
                Experiment.external_id == body.external_id,
            )
        )
        existing = result.scalar_one_or_none()

    if existing is not None:
        for field in ("name", "hypothesis", "description", "expected_sample_count", "variables_json"):
            new_val = getattr(body, field, None)
            if new_val is not None:
                setattr(existing, field, new_val)
        if project_id is not None:
            existing.project_id = project_id
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
            entity_type="experiment",
            entity_id=existing.id,
            action="upsert_matched",
            details={"external_id": body.external_id},
        )
        result = await session.execute(
            select(Experiment).options(selectinload(Experiment.custom_fields)).where(Experiment.id == existing.id)
        )
        existing = result.scalar_one()
        out = _experiment_out(existing)
        await event_bus.emit(
            INTEGRATION_EXPERIMENT_UPDATED,
            {
                "organization_id": org_id,
                "data": {"experiment_id": existing.id, "external_id": existing.external_id},
            },
        )
        if idempotency_key is not None:
            await idempotency_service.record(
                session,
                api_key_id,
                idempotency_key,
                idempotency_service.fingerprint("POST", "/api/v1/integrations/experiments", body_dict),
                200,
                json.loads(out.model_dump_json()),
            )
        await session.commit()
        response.status_code = 200
        return out

    exp = Experiment(
        organization_id=org_id,
        project_id=project_id,
        external_id=body.external_id,
        name=body.name,
        hypothesis=body.hypothesis,
        description=body.description,
        expected_sample_count=body.expected_sample_count,
        variables_json=body.variables_json,
        owner_user_id=int(user["sub"]),
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await _apply_custom_fields(
        session,
        exp.id,
        None,
        [cf.model_dump() for cf in body.custom_fields] if body.custom_fields else None,
    )
    await log_action(
        session,
        user_id=int(user["sub"]),
        api_key_id=api_key_id,
        entity_type="experiment",
        entity_id=exp.id,
        action="created",
        details={"external_id": body.external_id, "name": body.name},
    )
    result = await session.execute(
        select(Experiment).options(selectinload(Experiment.custom_fields)).where(Experiment.id == exp.id)
    )
    exp = result.scalar_one()
    out = _experiment_out(exp)
    await event_bus.emit(
        INTEGRATION_EXPERIMENT_CREATED,
        {
            "organization_id": org_id,
            "data": {
                "experiment_id": exp.id,
                "external_id": exp.external_id,
                "project_id": exp.project_id,
            },
        },
    )
    if idempotency_key is not None:
        await idempotency_service.record(
            session,
            api_key_id,
            idempotency_key,
            idempotency_service.fingerprint("POST", "/api/v1/integrations/experiments", body_dict),
            201,
            json.loads(out.model_dump_json()),
        )
    await session.commit()
    response.status_code = 201
    return out


@router.get(
    "",
    response_model=ExperimentListOut,
    summary="List experiments",
)
async def list_experiments(
    user: dict = require_api_key_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
    project_id: int | None = Query(None),
    status: str | None = Query(None),
    external_id: str | None = Query(None),
    q: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    org_id = int(user["org_id"])
    stmt = (
        select(Experiment).options(selectinload(Experiment.custom_fields)).where(Experiment.organization_id == org_id)
    )
    if project_id is not None:
        stmt = stmt.where(Experiment.project_id == project_id)
    if status:
        stmt = stmt.where(Experiment.status == status)
    if external_id:
        stmt = stmt.where(Experiment.external_id == external_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Experiment.name.ilike(like)) | (Experiment.code.ilike(like)))
    if cursor:
        try:
            stmt = stmt.where(Experiment.id < int(cursor))
        except ValueError as e:
            raise HTTPException(400, "invalid_cursor") from e
    stmt = stmt.order_by(Experiment.id.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = str(rows[limit - 1].id)
        rows = rows[:limit]
    return ExperimentListOut(items=[_experiment_out(e) for e in rows], next_cursor=next_cursor)


@router.get(
    "/{experiment_id}",
    response_model=ExperimentOut,
)
async def get_experiment(
    experiment_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Experiment)
        .options(selectinload(Experiment.custom_fields))
        .where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "experiment_not_found")
    return _experiment_out(row)


@router.get(
    "/by-external/{external_id}",
    response_model=ExperimentOut,
)
async def get_experiment_by_external(
    external_id: str,
    user: dict = require_api_key_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Experiment)
        .options(selectinload(Experiment.custom_fields))
        .where(Experiment.organization_id == org_id, Experiment.external_id == external_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "experiment_not_found")
    return _experiment_out(row)


@router.patch(
    "/{experiment_id}",
    response_model=ExperimentOut,
    summary="Update an experiment (status not permitted)",
)
async def patch_experiment(
    body: ExperimentUpdate,
    request: Request,
    experiment_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    raw_body = await request.body()
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                _reject_status_in_payload(parsed)
        except json.JSONDecodeError:
            pass

    org_id = int(user["org_id"])
    result = await session.execute(
        select(Experiment)
        .options(selectinload(Experiment.custom_fields))
        .where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "experiment_not_found")

    updates: dict = {}
    for field in ("name", "hypothesis", "description", "expected_sample_count", "variables_json"):
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
        entity_type="experiment",
        entity_id=row.id,
        action="updated",
        details=updates,
    )
    await event_bus.emit(
        INTEGRATION_EXPERIMENT_UPDATED,
        {
            "organization_id": org_id,
            "data": {
                "experiment_id": row.id,
                "external_id": row.external_id,
                "changed_fields": list(updates.keys()),
            },
        },
    )
    await session.commit()
    await session.refresh(row, attribute_names=["custom_fields"])
    return _experiment_out(row)
