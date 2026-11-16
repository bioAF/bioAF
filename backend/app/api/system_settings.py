"""Settings > System Settings: installation-wide settings (ADR-075 installation metrics)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.system_settings import (
    InstallMetricsChoiceRequest,
    InstallMetricsSettings,
    SystemSettingsResponse,
)
from app.services.audit_service import log_action
from app.services.install_metrics import state as metrics_state
from app.services.install_metrics.state import InstallMetricsService, InstallMetricsState

router = APIRouter(prefix="/api/system-settings", tags=["system-settings"])

_CONFLICTS = (metrics_state.RevisionConflict, metrics_state.NoticeVersionMismatch, metrics_state.StateUnavailable)


def _human(current_user: dict) -> dict:
    """An installation-wide choice belongs to a person: API keys (service accounts) are refused."""
    if current_user.get("api_key_id") is not None:
        raise HTTPException(403, "human_session_required")
    return current_user


async def _settings(session: AsyncSession, state: InstallMetricsState) -> SystemSettingsResponse:
    override = metrics_state.override_active()
    return SystemSettingsResponse(
        install_metrics=InstallMetricsSettings(
            preference=state.preference,
            prompt_required=state.preference == "pending",
            effective=state.effective(override),
            operator_override=override,
            setup_completed=state.setup_completed or await metrics_state.setup_complete(session),
            installation_id=state.installation_id,
            notice_version=metrics_state.NOTICE_VERSION,
            decided_at=state.decided_at,
            revision=state.revision,
        )
    )


@router.get("", response_model=SystemSettingsResponse)
async def get_system_settings(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    _human(current_user)
    return await _settings(session, await InstallMetricsService.read(session))


@router.put("/install-metrics", response_model=SystemSettingsResponse)
async def put_install_metrics(
    body: InstallMetricsChoiceRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    _human(current_user)
    try:
        state = await InstallMetricsService.choose(
            session, choice=body.choice, notice_version=body.notice_version, revision=body.revision
        )
    except _CONFLICTS as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from None
    # The local audit names the admin; it is never part of a report.
    await log_action(
        session,
        user_id=int(current_user["sub"]),
        entity_type="installation_metrics",
        entity_id=0,
        action="choose",
        details={"choice": body.choice, "notice_version": body.notice_version},
    )
    await session.commit()
    return await _settings(session, state)
