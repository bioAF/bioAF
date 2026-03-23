from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.models.controlled_vocabulary import ControlledVocabulary
from app.schemas.controlled_vocabulary import (
    ControlledVocabularyCreate,
    ControlledVocabularyDetailResponse,
    ControlledVocabularyFieldsResponse,
    ControlledVocabularyResponse,
    ControlledVocabularyUpdate,
    ControlledVocabularyValueResponse,
)
from app.services.audit_service import log_action
from app.services.vocabulary_validator import invalidate_cache

router = APIRouter(prefix="/api/vocabularies", tags=["vocabularies"])


@router.get("", response_model=ControlledVocabularyResponse)
async def list_vocabulary_values(
    field: str,
    active_only: bool = True,
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """List allowed values for a controlled vocabulary field."""
    query = select(ControlledVocabulary).where(ControlledVocabulary.field_name == field)
    if active_only:
        query = query.where(ControlledVocabulary.is_active == True)  # noqa: E712
    query = query.order_by(ControlledVocabulary.display_order)

    result = await session.execute(query)
    entries = list(result.scalars().all())

    values = [
        ControlledVocabularyValueResponse(
            id=e.id,
            value=e.allowed_value,
            display_label=e.display_label,
            display_order=e.display_order,
            is_default=e.is_default,
            is_active=e.is_active,
        )
        for e in entries
    ]
    return ControlledVocabularyResponse(field_name=field, values=values)


@router.get("/fields", response_model=ControlledVocabularyFieldsResponse)
async def list_vocabulary_fields(
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """List all distinct field names that have controlled vocabularies."""
    result = await session.execute(
        select(ControlledVocabulary.field_name).distinct().order_by(ControlledVocabulary.field_name)
    )
    fields = [row[0] for row in result.all()]
    return ControlledVocabularyFieldsResponse(fields=fields)


@router.post("", response_model=ControlledVocabularyDetailResponse, status_code=201)
async def create_vocabulary_value(
    body: ControlledVocabularyCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Add a new value to a controlled vocabulary. Admin only."""
    # Check for duplicate
    existing = await session.execute(
        select(ControlledVocabulary).where(
            ControlledVocabulary.field_name == body.field_name,
            ControlledVocabulary.allowed_value == body.allowed_value,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Value '{body.allowed_value}' already exists for field '{body.field_name}'")

    entry = ControlledVocabulary(
        field_name=body.field_name,
        allowed_value=body.allowed_value,
        display_label=body.display_label,
        display_order=body.display_order or 0,
        is_default=body.is_default or False,
    )
    session.add(entry)
    await session.flush()

    user_id = int(current_user["sub"])
    await log_action(
        session,
        user_id=user_id,
        entity_type="controlled_vocabulary",
        entity_id=entry.id,
        action="create",
        details={
            "field_name": body.field_name,
            "allowed_value": body.allowed_value,
        },
    )
    await session.commit()

    invalidate_cache(body.field_name)

    return ControlledVocabularyDetailResponse(
        id=entry.id,
        field_name=entry.field_name,
        allowed_value=entry.allowed_value,
        display_label=entry.display_label,
        display_order=entry.display_order,
        is_default=entry.is_default,
        is_active=entry.is_active,
        created_at=entry.created_at,
    )


@router.patch("/{vocab_id}", response_model=ControlledVocabularyDetailResponse)
async def update_vocabulary_value(
    vocab_id: int,
    body: ControlledVocabularyUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    """Update a controlled vocabulary value. Admin only."""
    result = await session.execute(select(ControlledVocabulary).where(ControlledVocabulary.id == vocab_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Vocabulary entry not found")

    previous = {}
    updates = {}
    for field in ("display_label", "display_order", "is_default", "is_active"):
        new_val = getattr(body, field, None)
        if new_val is not None:
            old_val = getattr(entry, field)
            previous[field] = str(old_val) if old_val is not None else None
            setattr(entry, field, new_val)
            updates[field] = str(new_val)

    if updates:
        await session.flush()
        user_id = int(current_user["sub"])
        await log_action(
            session,
            user_id=user_id,
            entity_type="controlled_vocabulary",
            entity_id=entry.id,
            action="update",
            details=updates,
            previous_value=previous,
        )
        await session.commit()

        invalidate_cache(entry.field_name)

    return ControlledVocabularyDetailResponse(
        id=entry.id,
        field_name=entry.field_name,
        allowed_value=entry.allowed_value,
        display_label=entry.display_label,
        display_order=entry.display_order,
        is_default=entry.is_default,
        is_active=entry.is_active,
        created_at=entry.created_at,
    )
