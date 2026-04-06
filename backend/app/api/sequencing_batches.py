from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.sequencing_batch import SequencingBatch
from app.schemas.sequencing_batch import (
    ManifestEntryResponse,
    SequencingBatchCreate,
    SequencingBatchDetailResponse,
    SequencingBatchResponse,
    SequencingBatchUpdate,
)
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/sequencing-batches", tags=["sequencing_batches"])


def _batch_response(batch: SequencingBatch) -> SequencingBatchResponse:
    return SequencingBatchResponse(
        id=batch.id,
        organization_id=batch.organization_id,
        name=batch.name,
        code=batch.code,
        status=batch.status,
        instrument_model=batch.instrument_model,
        instrument_platform=batch.instrument_platform,
        quality_score_encoding=batch.quality_score_encoding,
        sequencer_run_id=batch.sequencer_run_id,
        manifest_received_at=batch.manifest_received_at,
        expected_file_count=batch.expected_file_count,
        ingested_file_count=batch.ingested_file_count,
        notes=batch.notes,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


def _detail_response(batch: SequencingBatch) -> SequencingBatchDetailResponse:
    entries = [
        ManifestEntryResponse(
            id=e.id,
            expected_filename=e.expected_filename,
            expected_md5=e.expected_md5,
            resolved_sample_id=e.resolved_sample_id,
            resolved_experiment_id=e.resolved_experiment_id,
            resolved_project_id=e.resolved_project_id,
            file_id=e.file_id,
            status=e.status,
            last_check_at=e.last_check_at,
            retry_count=e.retry_count,
            error_message=e.error_message,
            created_at=e.created_at,
        )
        for e in (batch.manifest_entries or [])
    ]
    return SequencingBatchDetailResponse(
        **_batch_response(batch).model_dump(),
        manifest_entries=entries,
    )


@router.get("", response_model=list[SequencingBatchResponse])
async def list_sequencing_batches(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])
    result = await session.execute(
        select(SequencingBatch)
        .where(SequencingBatch.organization_id == org_id)
        .order_by(SequencingBatch.created_at.desc())
    )
    return [_batch_response(b) for b in result.scalars().all()]


@router.get("/{batch_id}", response_model=SequencingBatchDetailResponse)
async def get_sequencing_batch(
    batch_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(SequencingBatch)
        .options(selectinload(SequencingBatch.manifest_entries))
        .where(SequencingBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(404, "Sequencing batch not found")
    return _detail_response(batch)


@router.post("", response_model=SequencingBatchResponse)
async def create_sequencing_batch(
    body: SequencingBatchCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    batch = SequencingBatch(
        organization_id=org_id,
        name=body.name,
        code=body.code,
        status="pending",
        instrument_model=body.instrument_model,
        instrument_platform=body.instrument_platform,
        quality_score_encoding=body.quality_score_encoding,
        sequencer_run_id=body.sequencer_run_id,
        notes=body.notes,
    )
    session.add(batch)
    await session.flush()

    await log_action(
        session,
        user_id=user_id,
        entity_type="sequencing_batch",
        entity_id=batch.id,
        action="create",
        details={"name": body.name, "code": body.code},
    )
    await session.commit()
    return _batch_response(batch)


@router.patch("/{batch_id}", response_model=SequencingBatchResponse)
async def update_sequencing_batch(
    batch_id: int,
    body: SequencingBatchUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    result = await session.execute(select(SequencingBatch).where(SequencingBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(404, "Sequencing batch not found")

    updates = {}
    for field in [
        "name",
        "code",
        "status",
        "instrument_model",
        "instrument_platform",
        "quality_score_encoding",
        "sequencer_run_id",
        "expected_file_count",
        "ingested_file_count",
        "notes",
    ]:
        new_val = getattr(body, field, None)
        if new_val is not None:
            setattr(batch, field, new_val)
            updates[field] = str(new_val)

    if updates:
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="sequencing_batch",
            entity_id=batch.id,
            action="update",
            details=updates,
        )

    await session.commit()
    result = await session.execute(select(SequencingBatch).where(SequencingBatch.id == batch_id))
    batch = result.scalar_one()
    return _batch_response(batch)
