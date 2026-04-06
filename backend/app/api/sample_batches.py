from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.sample_batch import SampleBatchAssignSamples, SampleBatchResponse, SampleBatchUpdate
from app.schemas.experiment import UserSummary
from app.services.sample_batch_service import SampleBatchService

router = APIRouter(prefix="/api/sample-batches", tags=["sample_batches"])


def _user_summary(user) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _batch_response(batch) -> SampleBatchResponse:
    return SampleBatchResponse(
        id=batch.id,
        name=batch.name,
        prep_date=batch.prep_date,
        operator=_user_summary(batch.operator),
        sequencer_run_id=batch.sequencer_run_id,
        instrument_model=batch.instrument_model,
        instrument_platform=batch.instrument_platform,
        quality_score_encoding=batch.quality_score_encoding,
        notes=batch.notes,
        sample_count=len(batch.samples) if batch.samples else 0,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.get("/{batch_id}", response_model=SampleBatchResponse)
async def get_sample_batch(
    batch_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    batch = await SampleBatchService.get_batch(session, batch_id)
    if not batch:
        raise HTTPException(404, "Sample batch not found")
    return _batch_response(batch)


@router.patch("/{batch_id}", response_model=SampleBatchResponse)
async def update_sample_batch(
    batch_id: int,
    body: SampleBatchUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    batch = await SampleBatchService.update_batch(session, batch_id, user_id, body)
    if not batch:
        raise HTTPException(404, "Sample batch not found")
    await session.commit()
    batch = await SampleBatchService.get_batch(session, batch_id)
    return _batch_response(batch)


@router.post("/{batch_id}/assign-samples")
async def assign_samples(
    batch_id: int,
    body: SampleBatchAssignSamples,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    await SampleBatchService.assign_samples_to_batch(session, batch_id, body.sample_ids, user_id)
    await session.commit()
    return {"status": "ok", "assigned": len(body.sample_ids)}
