"""GCP configuration settings API endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.gcp_config import GCPConfigResponse, GCPConfigUpdate, GCPValidationResult
from app.services import audit_service
from app.services.gcp_config import validate_gcp_credentials

router = APIRouter(prefix="/api/v1/settings/gcp", tags=["gcp_config"])

_GCP_KEYS = [
    "gcp_project_id",
    "gcp_region",
    "gcp_zone",
    "org_slug",
    "gcp_credentials_configured",
    "gcp_validation_status",
    "gcp_credential_source",
    "gcp_service_account_email",
    "gcp_bootstrap_sa_email",
]

_DEFAULTS: dict[str, str] = {
    "gcp_project_id": "",
    "gcp_region": "us-central1",
    "gcp_zone": "us-central1-a",
    "org_slug": "",
    "gcp_credentials_configured": "false",
    "gcp_validation_status": "",
    "gcp_credential_source": "vm_default",
    "gcp_service_account_email": "",
    "gcp_bootstrap_sa_email": "",
}


async def _read_config(session: AsyncSession) -> dict[str, str]:
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=_GCP_KEYS)
        )
    ).fetchall()
    config = dict(_DEFAULTS)
    config.update({r[0]: r[1] for r in rows})
    return config


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = now()"
        ).bindparams(k=key, v=value)
    )


def _to_response(config: dict[str, str]) -> GCPConfigResponse:
    return GCPConfigResponse(
        gcp_project_id=config.get("gcp_project_id") or None,
        gcp_region=config.get("gcp_region") or "us-central1",
        gcp_zone=config.get("gcp_zone") or "us-central1-a",
        org_slug=config.get("org_slug") or None,
        gcp_credentials_configured=config.get("gcp_credentials_configured", "false") == "true",
        gcp_validation_status=config.get("gcp_validation_status") or None,
        gcp_credential_source=config.get("gcp_credential_source", "vm_default"),
        gcp_service_account_email=config.get("gcp_service_account_email") or None,
        gcp_bootstrap_sa_email=config.get("gcp_bootstrap_sa_email") or None,
    )


@router.get("", response_model=GCPConfigResponse)
async def get_gcp_config(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> GCPConfigResponse:
    """Return current GCP configuration.  Service account key is never returned."""
    config = await _read_config(session)
    return _to_response(config)


@router.put("", response_model=GCPConfigResponse)
async def update_gcp_config(
    body: GCPConfigUpdate,
    current_user: dict = require_permission("infrastructure", "edit"),
    session: AsyncSession = Depends(get_session),
) -> GCPConfigResponse:
    """Save GCP configuration fields and reset validation state."""
    user_id = int(current_user["sub"])

    field_map: dict[str, str | None] = {
        "gcp_project_id": body.gcp_project_id,
        "gcp_region": body.gcp_region,
        "gcp_zone": body.gcp_zone,
        "org_slug": body.org_slug,
        "gcp_credential_source": body.gcp_credential_source,
        "gcp_service_account_email": body.gcp_service_account_email,
    }

    for key, value in field_map.items():
        if value is not None:
            await _upsert(session, key, value)

    if body.service_account_key is not None:
        await _upsert(session, "gcp_service_account_key", body.service_account_key)

    # Reset validation status whenever config changes
    await _upsert(session, "gcp_validation_status", "")
    await _upsert(session, "gcp_credentials_configured", "false")

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="update_gcp_config",
        details={k: v for k, v in field_map.items() if v is not None},
    )

    await session.commit()

    config = await _read_config(session)
    return _to_response(config)


@router.post("/validate", response_model=GCPValidationResult)
async def validate_gcp_config(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> GCPValidationResult:
    """Run GCP credential validation checks against live GCP APIs."""
    user_id = int(current_user["sub"])

    config = await _read_config(session)
    project_id = config.get("gcp_project_id", "")
    if not project_id:
        raise HTTPException(400, "gcp_project_id is not configured")

    credential_source = config.get("gcp_credential_source", "vm_default")
    # Prefer the new bootstrap key; fall back to legacy email for backward compat.
    impersonation_target = config.get("gcp_bootstrap_sa_email") or config.get("gcp_service_account_email") or ""

    sa_key_row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='gcp_service_account_key'"))
    ).scalar()

    result = validate_gcp_credentials(
        project_id=project_id,
        credential_source=credential_source,
        service_account_key=sa_key_row,
        service_account_email=impersonation_target or None,
    )

    # Persist validation outcome
    status_value = json.dumps([c.model_dump() for c in result.checks])
    await _upsert(session, "gcp_validation_status", status_value)
    await _upsert(
        session,
        "gcp_credentials_configured",
        "true" if result.passed else "false",
    )

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="validate_gcp_credentials",
        details={"passed": result.passed, "check_count": len(result.checks)},
    )

    await session.commit()
    return result
