"""Samples endpoints for /api/v1/integrations.

QC and status writes are not permitted on this surface.
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
from app.models.sample import Sample
from app.models.sample_custom_field import SampleCustomField
from app.schemas.integrations.common import CustomFieldOut
from app.schemas.integrations.sample import (
    SampleCreate,
    SampleListOut,
    SampleOut,
    SampleUpdate,
)
from app.services import idempotency_service
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import (
    INTEGRATION_SAMPLE_CREATED,
    INTEGRATION_SAMPLE_UPDATED,
)

router = APIRouter(prefix="/samples", tags=["Samples"])


def _sample_out(sample: Sample) -> SampleOut:
    return SampleOut(
        id=sample.id,
        external_id=sample.external_id,
        experiment_id=sample.experiment_id,
        organism=sample.organism,
        tissue_type=sample.tissue_type,
        donor_source=sample.donor_source,
        treatment_condition=sample.treatment_condition,
        chemistry_version=sample.chemistry_version,
        cell_count=sample.cell_count,
        prep_notes=sample.prep_notes,
        molecule_type=sample.molecule_type,
        library_prep_method=sample.library_prep_method,
        qc_status=sample.qc_status,
        status=sample.status,
        created_at=sample.created_at,
        custom_fields=[
            CustomFieldOut(field_name=cf.field_name, field_value=cf.field_value) for cf in (sample.custom_fields or [])
        ],
    )


async def _apply_custom_fields(
    session: AsyncSession,
    sample_id: int,
    existing_fields: list[SampleCustomField] | None,
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
            session.add(SampleCustomField(sample_id=sample_id, field_name=name, field_value=value))
    await session.flush()


def _reject_qc_status_in_payload(body: dict) -> None:
    if "qc_status" in body and body["qc_status"] is not None:
        raise HTTPException(400, "qc_writes_not_permitted")
    if "status" in body and body["status"] is not None:
        raise HTTPException(400, "status_writes_not_permitted")


@router.post(
    "",
    response_model=SampleOut,
    summary="Create a sample",
    description=(
        "Creates a sample. `external_id` is required and must be unique within "
        "the experiment; duplicate `external_id` returns 409. Returns 201 on "
        "success. `Idempotency-Key` is honored on retries."
    ),
)
async def create_sample(
    body: SampleCreate,
    request: Request,
    response: Response,
    user: dict = require_api_key_permission("samples", "create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    raw_body = await request.body()
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                _reject_qc_status_in_payload(parsed)
        except json.JSONDecodeError:
            pass

    api_key_id = int(user["api_key_id"])
    org_id = int(user["org_id"])
    body_dict = body.model_dump(mode="json")

    if idempotency_key is not None:
        cached = await idempotency_service.lookup(session, api_key_id, idempotency_key)
        if cached is not None:
            current_fp = idempotency_service.fingerprint("POST", "/api/v1/integrations/samples", body_dict)
            if cached.request_fingerprint != current_fp:
                raise HTTPException(422, "idempotency_key_reused_with_different_body")
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = cached.response_status
            return cached.response_body

    exp = (
        await session.execute(
            select(Experiment).where(Experiment.id == body.experiment_id, Experiment.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if exp is None:
        raise HTTPException(404, "experiment_not_found")

    existing_dup = (
        await session.execute(
            select(Sample.id).where(
                Sample.experiment_id == body.experiment_id,
                Sample.external_id == body.external_id,
            )
        )
    ).scalar_one_or_none()
    if existing_dup is not None:
        raise HTTPException(409, "external_id_already_exists")

    sample = Sample(
        experiment_id=body.experiment_id,
        external_id=body.external_id,
        organism=body.organism,
        tissue_type=body.tissue_type,
        donor_source=body.donor_source,
        treatment_condition=body.treatment_condition,
        chemistry_version=body.chemistry_version,
        cell_count=body.cell_count,
        prep_notes=body.prep_notes,
        molecule_type=body.molecule_type,
        library_prep_method=body.library_prep_method,
        status="registered",
    )
    session.add(sample)
    await session.flush()
    await _apply_custom_fields(
        session,
        sample.id,
        None,
        [cf.model_dump() for cf in body.custom_fields] if body.custom_fields else None,
    )
    await log_action(
        session,
        user_id=int(user["sub"]),
        api_key_id=api_key_id,
        entity_type="sample",
        entity_id=sample.id,
        action="created",
        details={"external_id": body.external_id},
    )
    result = await session.execute(
        select(Sample).options(selectinload(Sample.custom_fields)).where(Sample.id == sample.id)
    )
    sample = result.scalar_one()
    out = _sample_out(sample)
    await event_bus.emit(
        INTEGRATION_SAMPLE_CREATED,
        {
            "organization_id": org_id,
            "data": {
                "sample_id": sample.id,
                "external_id": sample.external_id,
                "experiment_id": sample.experiment_id,
            },
        },
    )
    if idempotency_key is not None:
        await idempotency_service.record(
            session,
            api_key_id,
            idempotency_key,
            idempotency_service.fingerprint("POST", "/api/v1/integrations/samples", body_dict),
            201,
            json.loads(out.model_dump_json()),
        )
    await session.commit()
    response.status_code = 201
    return out


@router.get("", response_model=SampleListOut, summary="List samples")
async def list_samples(
    user: dict = require_api_key_permission("samples", "view"),
    session: AsyncSession = Depends(get_session),
    experiment_id: int | None = Query(None),
    external_id: str | None = Query(None),
    q: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    org_id = int(user["org_id"])
    stmt = (
        select(Sample)
        .join(Experiment, Sample.experiment_id == Experiment.id)
        .options(selectinload(Sample.custom_fields))
        .where(Experiment.organization_id == org_id)
    )
    if experiment_id is not None:
        stmt = stmt.where(Sample.experiment_id == experiment_id)
    if external_id:
        stmt = stmt.where(Sample.external_id == external_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Sample.external_id.ilike(like)) | (Sample.organism.ilike(like)))
    if cursor:
        try:
            stmt = stmt.where(Sample.id < int(cursor))
        except ValueError as e:
            raise HTTPException(400, "invalid_cursor") from e
    stmt = stmt.order_by(Sample.id.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = str(rows[limit - 1].id)
        rows = rows[:limit]
    return SampleListOut(items=[_sample_out(s) for s in rows], next_cursor=next_cursor)


@router.get(
    "/{sample_id}",
    response_model=SampleOut,
)
async def get_sample(
    sample_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("samples", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Sample)
        .join(Experiment, Sample.experiment_id == Experiment.id)
        .options(selectinload(Sample.custom_fields))
        .where(Sample.id == sample_id, Experiment.organization_id == org_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "sample_not_found")
    return _sample_out(row)


@router.get(
    "/by-external/{external_id}",
    response_model=SampleOut,
    summary="Get a sample by external id (within an experiment)",
)
async def get_sample_by_external(
    external_id: str,
    experiment_id: int = Query(..., description="Experiment id to disambiguate"),
    user: dict = require_api_key_permission("samples", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(
        select(Sample)
        .join(Experiment, Sample.experiment_id == Experiment.id)
        .options(selectinload(Sample.custom_fields))
        .where(
            Experiment.organization_id == org_id,
            Sample.experiment_id == experiment_id,
            Sample.external_id == external_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "sample_not_found")
    return _sample_out(row)


@router.patch(
    "/{sample_id}",
    response_model=SampleOut,
    summary="Update a sample (QC and status writes not permitted)",
)
async def patch_sample(
    body: SampleUpdate,
    request: Request,
    sample_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("samples", "edit"),
    session: AsyncSession = Depends(get_session),
):
    raw_body = await request.body()
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                _reject_qc_status_in_payload(parsed)
        except json.JSONDecodeError:
            pass

    org_id = int(user["org_id"])
    result = await session.execute(
        select(Sample)
        .join(Experiment, Sample.experiment_id == Experiment.id)
        .options(selectinload(Sample.custom_fields))
        .where(Sample.id == sample_id, Experiment.organization_id == org_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "sample_not_found")

    updates: dict = {}
    for field in (
        "organism",
        "tissue_type",
        "donor_source",
        "treatment_condition",
        "chemistry_version",
        "cell_count",
        "prep_notes",
        "molecule_type",
        "library_prep_method",
    ):
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
        entity_type="sample",
        entity_id=row.id,
        action="updated",
        details=updates,
    )
    await event_bus.emit(
        INTEGRATION_SAMPLE_UPDATED,
        {
            "organization_id": org_id,
            "data": {
                "sample_id": row.id,
                "external_id": row.external_id,
                "changed_fields": list(updates.keys()),
            },
        },
    )
    await session.commit()
    await session.refresh(row, attribute_names=["custom_fields"])
    return _sample_out(row)
