from fastapi import APIRouter, Depends

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.compute import QuotaResponse, QuotaUpdateRequest
from app.services.quota_service import QuotaService

router = APIRouter(prefix="/api/quotas", tags=["quotas"])


def _quota_response(quota) -> QuotaResponse:
    return QuotaResponse(
        user_id=quota.user_id,
        user_name=quota.user.name if quota.user else None,
        user_email=quota.user.email if quota.user else None,
        user_role=None,  # role name resolved separately if needed
        cpu_hours_limit=quota.cpu_hours_monthly_limit,
        cpu_hours_used=float(quota.cpu_hours_used_current_month),
        quota_reset_at=quota.quota_reset_at,
    )


@router.get("", response_model=list[QuotaResponse])
async def list_quotas(
    current_user: dict = require_permission("quotas", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    quotas = await QuotaService.list_quotas(session, org_id)
    return [_quota_response(q) for q in quotas]


@router.get("/me", response_model=QuotaResponse)
async def get_own_quota(
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    quota = await QuotaService.get_or_create_quota(session, user_id, org_id)
    await session.commit()

    # Reload with user relationship
    quota = await QuotaService.get_quota(session, user_id)
    return _quota_response(quota)


@router.patch("/{target_user_id}", response_model=QuotaResponse)
async def set_user_quota(
    target_user_id: int,
    body: QuotaUpdateRequest,
    current_user: dict = require_permission("quotas", "configure"),
    session: AsyncSession = Depends(get_session),
):
    admin_user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    quota = await QuotaService.set_quota(session, target_user_id, admin_user_id, org_id, body.cpu_hours_monthly_limit)
    await session.commit()

    # Reload with user relationship
    quota = await QuotaService.get_quota(session, target_user_id)
    return _quota_response(quota)
