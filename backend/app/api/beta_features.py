"""Beta features API (lit_validation Phase 4, spec-07).

- ``GET /api/beta-features`` returns availability + per-feature enablement. Any authenticated caller
  can read it (the nav needs the flags to decide which items to show); the global auth layer already
  rejects the unauthenticated.
- ``PUT /api/beta-features/{key}`` toggles a flag. Admin-only (``infrastructure:configure``) AND only on
  a bioAF-operated instance (``is_available``); an unknown key is 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services import beta_features_service

router = APIRouter(prefix="/api/beta-features", tags=["beta-features"])


class BetaFeaturesState(BaseModel):
    available: bool
    flags: dict[str, bool]


class SetFlagRequest(BaseModel):
    enabled: bool


@router.get("", response_model=BetaFeaturesState)
async def get_beta_features(session: AsyncSession = Depends(get_session)):
    return await beta_features_service.get_state(session)


@router.put("/{key}", response_model=BetaFeaturesState)
async def set_beta_feature(
    key: str,
    body: SetFlagRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    if key not in beta_features_service.BETA_FEATURES:
        raise HTTPException(404, f"unknown beta feature: {key}")
    if not await beta_features_service.is_available(session):
        raise HTTPException(403, "beta features are not available on this instance")
    await beta_features_service.set_flag(session, key, body.enabled)
    await session.commit()
    return await beta_features_service.get_state(session)
