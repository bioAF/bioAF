"""AWS configuration settings API endpoints (stage 8d).

The structural parallel of ``app.api.gcp_config``. AWS auth is the EC2 instance
profile (ambient), so there is no service-account key to store: the panel and
setup wizard save the account / region / role ARNs / org_slug the install runs
on and validate that the ambient credentials resolve and match the configured
account (``adapters.validation.aws`` -> STS).
"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

# Import the re-exported symbol from the package __init__ (inside adapters/,
# where boto3 lives) rather than the submodule. The BAL layering guard scans the
# service layer for a lowercase CLI shell token, and a submodule import of the
# AWS validation provider would trip it as a false positive.
from app.adapters.validation import validate_aws_credentials
from app.api.dependencies import require_permission
from app.database import get_session
from app.platform.platform_config_service import PlatformConfigService
from app.schemas.aws_config import AwsConfigResponse, AwsConfigUpdate, AwsValidationResult
from app.services import audit_service

router = APIRouter(prefix="/api/v1/settings/aws", tags=["aws_config"])

_AWS_KEYS = [
    "aws_account_id",
    "aws_region",
    "aws_app_role_arn",
    "aws_bootstrap_role_arn",
    "org_slug",
    "aws_credential_source",
    "aws_credentials_configured",
    "aws_validation_status",
]

_DEFAULTS: dict[str, str] = {
    "aws_account_id": "",
    "aws_region": "us-east-1",
    "aws_app_role_arn": "",
    "aws_bootstrap_role_arn": "",
    "org_slug": "",
    "aws_credential_source": "instance_profile",
    "aws_credentials_configured": "false",
    "aws_validation_status": "",
}


async def _read_config(session: AsyncSession) -> dict[str, str]:
    config = dict(_DEFAULTS)
    config.update(await PlatformConfigService.get_many(session, _AWS_KEYS))
    return config


def _to_response(config: dict[str, str]) -> AwsConfigResponse:
    return AwsConfigResponse(
        aws_account_id=config.get("aws_account_id") or None,
        aws_region=config.get("aws_region") or "us-east-1",
        aws_app_role_arn=config.get("aws_app_role_arn") or None,
        aws_bootstrap_role_arn=config.get("aws_bootstrap_role_arn") or None,
        org_slug=config.get("org_slug") or None,
        aws_credential_source=config.get("aws_credential_source", "instance_profile"),
        aws_credentials_configured=config.get("aws_credentials_configured", "false") == "true",
        aws_validation_status=config.get("aws_validation_status") or None,
    )


@router.get("", response_model=AwsConfigResponse)
async def get_aws_config(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> AwsConfigResponse:
    """Return the current AWS configuration."""
    return _to_response(await _read_config(session))


@router.put("", response_model=AwsConfigResponse)
async def update_aws_config(
    body: AwsConfigUpdate,
    current_user: dict = require_permission("infrastructure", "edit"),
    session: AsyncSession = Depends(get_session),
) -> AwsConfigResponse:
    """Save AWS configuration fields and reset validation state."""
    user_id = int(current_user["sub"])

    field_map: dict[str, str | None] = {
        "aws_account_id": body.aws_account_id,
        "aws_region": body.aws_region,
        "aws_app_role_arn": body.aws_app_role_arn,
        "aws_bootstrap_role_arn": body.aws_bootstrap_role_arn,
        "org_slug": body.org_slug,
    }
    for key, value in field_map.items():
        if value is not None:
            await PlatformConfigService.set(session, key, value)

    # Reset validation status whenever config changes.
    await PlatformConfigService.set(session, "aws_validation_status", "")
    await PlatformConfigService.set(session, "aws_credentials_configured", "false")

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="update_aws_config",
        details={k: v for k, v in field_map.items() if v is not None},
    )
    await session.commit()
    return _to_response(await _read_config(session))


@router.post("/validate", response_model=AwsValidationResult)
async def validate_aws_config(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> AwsValidationResult:
    """Validate the ambient AWS credentials against STS GetCallerIdentity."""
    user_id = int(current_user["sub"])
    config = await _read_config(session)

    result = validate_aws_credentials(
        account_id=config.get("aws_account_id") or None,
        region=config.get("aws_region") or None,
    )

    status_value = json.dumps([c.model_dump() for c in result.checks])
    await PlatformConfigService.set(session, "aws_validation_status", status_value)
    await PlatformConfigService.set(
        session,
        "aws_credentials_configured",
        "true" if result.passed else "false",
    )

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="validate_aws_credentials",
        details={"passed": result.passed, "check_count": len(result.checks)},
    )
    await session.commit()
    return result
