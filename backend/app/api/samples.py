from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.sample import SampleBulkUpdate, SampleQCUpdate, SampleResponse, SampleStatusUpdate, SampleUpdate
from app.services.sample_service import SampleService


class SampleBulkDeleteRequest(BaseModel):
    sample_ids: list[int]


router = APIRouter(prefix="/api/samples", tags=["samples"])


def _sample_response(s) -> SampleResponse:
    return SampleResponse(
        id=s.id,
        sample_id_unique=s.sample_id_unique,
        organism=s.organism,
        tissue_type=s.tissue_type,
        donor_source=s.donor_source,
        treatment_condition=s.treatment_condition,
        chemistry_version=s.chemistry_version,
        sample_batch={"id": s.sample_batch.id, "name": s.sample_batch.name} if s.sample_batch else None,
        sequencing_batch={"id": s.sequencing_batch.id, "code": s.sequencing_batch.code} if s.sequencing_batch else None,
        sequencing_batch_position=s.sequencing_batch_position,
        viability_pct=float(s.viability_pct) if s.viability_pct is not None else None,
        cell_count=s.cell_count,
        prep_notes=s.prep_notes,
        molecule_type=s.molecule_type,
        library_prep_method=s.library_prep_method,
        library_layout=s.library_layout,
        qc_status=s.qc_status,
        qc_notes=s.qc_notes,
        parent_sample_id=s.parent_sample_id,
        collection_timestamp=s.collection_timestamp,
        collection_method=s.collection_method,
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.patch("/bulk/update")
async def bulk_update_samples(
    body: SampleBulkUpdate,
    current_user: dict = require_permission("samples", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    updated = 0
    errors = []
    for sample_id in body.sample_ids:
        sample = await SampleService.update_sample(session, sample_id, user_id, body.update)
        if not sample:
            errors.append(f"Sample {sample_id} not found")
        else:
            updated += 1
    if updated:
        await session.commit()
    return {"updated": updated, "errors": errors}


@router.get("/{sample_id}", response_model=SampleResponse)
async def get_sample(
    sample_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    sample = await SampleService.get_sample(session, sample_id)
    if not sample:
        raise HTTPException(404, "Sample not found")
    return _sample_response(sample)


@router.patch("/{sample_id}", response_model=SampleResponse)
async def update_sample(
    sample_id: int,
    body: SampleUpdate,
    current_user: dict = require_permission("samples", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    sample = await SampleService.update_sample(session, sample_id, user_id, body)
    if not sample:
        raise HTTPException(404, "Sample not found")
    await session.commit()
    sample = await SampleService.get_sample(session, sample_id)
    return _sample_response(sample)


@router.patch("/{sample_id}/qc", response_model=SampleResponse)
async def update_sample_qc(
    sample_id: int,
    body: SampleQCUpdate,
    current_user: dict = require_permission("samples", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    sample = await SampleService.update_qc_status(session, sample_id, user_id, body.qc_status, body.qc_notes)
    if not sample:
        raise HTTPException(404, "Sample not found")
    await session.commit()
    sample = await SampleService.get_sample(session, sample_id)
    return _sample_response(sample)


@router.patch("/{sample_id}/status", response_model=SampleResponse)
async def update_sample_status(
    sample_id: int,
    body: SampleStatusUpdate,
    current_user: dict = require_permission("samples", "change_status"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    sample = await SampleService.update_status(session, sample_id, user_id, body.status)
    await session.commit()
    sample = await SampleService.get_sample(session, sample_id)
    return _sample_response(sample)


@router.post("/bulk/delete")
async def bulk_delete_samples(
    body: SampleBulkDeleteRequest,
    current_user: dict = require_permission("samples", "delete"),
    session: AsyncSession = Depends(get_session),
):
    if not body.sample_ids:
        return {"deleted": 0}
    user_id = int(current_user["sub"])
    deleted = await SampleService.delete_samples(session, body.sample_ids, user_id)
    await session.commit()
    return {"deleted": deleted}
