"""Org-level literature settings: Lit Review automation, and literature validation autonomy."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.literature import (
    LitReviewSettingsPayload,
    LitReviewSettingsUpdateRequest,
    LitValidationSettingsPayload,
    LitValidationSettingsUpdateRequest,
)

router = APIRouter()


@router.get("/settings/lit-review", response_model=LitReviewSettingsPayload)
async def get_lit_review_settings_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    from app.models.organization import Organization
    from app.services.literature import lit_review_auto_service

    org_id = int(current_user["org_id"])
    rs = await session.execute(
        select(
            Organization.lit_review_relevance_threshold,
            Organization.lit_review_auto_enabled,
            Organization.lit_review_auto_cadence,
            Organization.lit_review_max_runs_per_tick,
        ).where(Organization.id == org_id)
    )
    row = rs.one_or_none()
    if row is None:
        return LitReviewSettingsPayload(
            relevance_threshold=0.65, auto_enabled=False, auto_cadence="weekly", max_runs_per_tick=5
        )
    next_run = await lit_review_auto_service.get_next_run(session)
    return LitReviewSettingsPayload(
        relevance_threshold=float(row[0] if row[0] is not None else 0.65),
        auto_enabled=bool(row[1]),
        auto_cadence=row[2] or "weekly",
        max_runs_per_tick=int(row[3] or 5),
        next_run=next_run.isoformat() if next_run else None,
    )


@router.put("/settings/lit-review", response_model=LitReviewSettingsPayload)
async def update_lit_review_settings_endpoint(
    body: LitReviewSettingsUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    from app.models.organization import Organization
    from app.services import audit_service
    from app.services.literature import lit_review_auto_service

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    rs = await session.execute(select(Organization).where(Organization.id == org_id))
    org = rs.scalar_one()

    previous = {
        "relevance_threshold": org.lit_review_relevance_threshold,
        "auto_enabled": org.lit_review_auto_enabled,
        "auto_cadence": org.lit_review_auto_cadence,
        "max_runs_per_tick": org.lit_review_max_runs_per_tick,
    }
    changes: dict = {}

    if body.relevance_threshold is not None:
        if not (0.0 <= body.relevance_threshold <= 1.0):
            raise HTTPException(400, "relevance_threshold must be between 0.0 and 1.0")
        org.lit_review_relevance_threshold = body.relevance_threshold
        changes["relevance_threshold"] = body.relevance_threshold

    # Parse the optional explicit first-run time.
    first_run_dt: datetime | None = None
    if body.first_run is not None and body.first_run.strip():
        try:
            first_run_dt = datetime.fromisoformat(body.first_run.strip().replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "first_run must be an ISO 8601 datetime")
        if first_run_dt.tzinfo is None:
            first_run_dt = first_run_dt.replace(tzinfo=UTC)
        changes["first_run"] = first_run_dt.isoformat()

    # Toggling enable, changing cadence, or setting a first-run time (re)schedules
    # the timer; changing only the cap does not.
    reschedule = first_run_dt is not None
    if body.auto_cadence is not None:
        if body.auto_cadence not in lit_review_auto_service.VALID_CADENCES:
            raise HTTPException(400, f"auto_cadence must be one of {lit_review_auto_service.VALID_CADENCES}")
        org.lit_review_auto_cadence = body.auto_cadence
        changes["auto_cadence"] = body.auto_cadence
        reschedule = True
    if body.max_runs_per_tick is not None:
        if body.max_runs_per_tick < 1:
            raise HTTPException(400, "max_runs_per_tick must be at least 1")
        org.lit_review_max_runs_per_tick = body.max_runs_per_tick
        changes["max_runs_per_tick"] = body.max_runs_per_tick
    if body.auto_enabled is not None:
        org.lit_review_auto_enabled = body.auto_enabled
        changes["auto_enabled"] = body.auto_enabled
        reschedule = True

    await session.flush()

    if reschedule:
        if org.lit_review_auto_enabled:
            # Use the admin's chosen first-run time when given; otherwise fall
            # back to one cadence from now.
            if first_run_dt is not None:
                await lit_review_auto_service.set_next_run(session, first_run_dt)
            else:
                await lit_review_auto_service.schedule_from_now(session, org_id)
        else:
            await lit_review_auto_service.clear_schedule(session)

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="organization",
        entity_id=org_id,
        action="update_lit_review_settings",
        details=changes,
        previous_value=previous,
    )
    await session.commit()
    next_run = await lit_review_auto_service.get_next_run(session)
    return LitReviewSettingsPayload(
        relevance_threshold=float(org.lit_review_relevance_threshold),
        auto_enabled=bool(org.lit_review_auto_enabled),
        auto_cadence=org.lit_review_auto_cadence,
        max_runs_per_tick=int(org.lit_review_max_runs_per_tick),
        next_run=next_run.isoformat() if next_run else None,
    )


@router.get("/settings/lit-validation", response_model=LitValidationSettingsPayload)
async def get_lit_validation_settings_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    from app.models.organization import Organization
    from app.services.validation_autonomy import AUTONOMY_ASSISTED

    org_id = int(current_user["org_id"])
    row = (
        await session.execute(select(Organization.lit_validation_autonomy).where(Organization.id == org_id))
    ).one_or_none()
    return LitValidationSettingsPayload(autonomy=(row[0] if row else None) or AUTONOMY_ASSISTED)


@router.put("/settings/lit-validation", response_model=LitValidationSettingsPayload)
async def update_lit_validation_settings_endpoint(
    body: LitValidationSettingsUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    from app.models.organization import Organization
    from app.services import audit_service
    from app.services.validation_autonomy import VALID_AUTONOMY

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()

    if body.autonomy is not None:
        if body.autonomy not in VALID_AUTONOMY:
            raise HTTPException(400, f"autonomy must be one of {VALID_AUTONOMY}")
        previous = {"autonomy": org.lit_validation_autonomy}
        org.lit_validation_autonomy = body.autonomy
        await session.flush()
        # Audited because it changes who is answerable for a study's scientific judgments, which is
        # exactly the kind of change a lab needs to be able to reconstruct after the fact.
        await audit_service.log_action(
            session,
            user_id=user_id,
            entity_type="organization",
            entity_id=org_id,
            action="update_lit_validation_settings",
            details={"autonomy": body.autonomy},
            previous_value=previous,
        )
        await session.commit()

    return LitValidationSettingsPayload(autonomy=org.lit_validation_autonomy)
