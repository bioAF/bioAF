"""Slack OAuth API endpoints: auth URL, callback, status, disconnect, channels, mappings."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.api.dependencies import require_permission
from app.models.organization import Organization
from app.schemas.notification import (
    SlackAuthUrlResponse,
    SlackChannelMappingCreate,
    SlackChannelMappingResponse,
    SlackChannelMappingUpdate,
    SlackChannelResponse,
    SlackCredentialsResponse,
    SlackCredentialsSave,
    SlackStatusResponse,
)
from app.services.slack_oauth_service import SlackOAuthService


async def _get_slack_credentials(session: AsyncSession, org_id: int) -> tuple[str, str]:
    """Return (client_id, client_secret), preferring org DB values over env vars."""
    result = await session.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if org and org.slack_client_id and org.slack_client_secret:
        return org.slack_client_id, org.slack_client_secret
    return settings.slack_client_id, settings.slack_client_secret


logger = logging.getLogger("bioaf.api.slack_oauth")

router = APIRouter(prefix="/api/notifications/slack", tags=["slack"])


@router.get("/manifest")
async def get_manifest(
    request: Request,
    origin: str = Query(""),
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Generate the Slack App manifest JSON. Uses ?origin= from the frontend."""
    base = origin.rstrip("/") if origin else str(request.base_url).rstrip("/")
    callback_url = f"{base}/api/notifications/slack/callback"

    return {
        "display_information": {
            "name": "bioAF",
            "description": "Bioinformatics analysis platform notifications",
        },
        "features": {
            "bot_user": {
                "display_name": "bioAF",
                "always_online": True,
            }
        },
        "oauth_config": {
            "scopes": {"bot": ["chat:write", "channels:read", "groups:read"]},
            "redirect_urls": [callback_url],
        },
        "settings": {
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


@router.get("/credentials", response_model=SlackCredentialsResponse)
async def get_credentials(
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Check whether Slack credentials are configured."""
    client_id, client_secret = await _get_slack_credentials(session, current_user["org_id"])
    configured = bool(client_id and client_secret)
    preview = (client_id[:6] + "..." + client_id[-4:]) if client_id and len(client_id) > 10 else None
    return SlackCredentialsResponse(configured=configured, client_id_preview=preview)


@router.post("/credentials", response_model=SlackCredentialsResponse)
async def save_credentials(
    body: SlackCredentialsSave,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Save Slack App credentials for this organization."""
    result = await session.execute(select(Organization).where(Organization.id == current_user["org_id"]))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")

    org.slack_client_id = body.client_id.strip()
    org.slack_client_secret = body.client_secret.strip()
    org.slack_signing_secret = body.signing_secret.strip()
    await session.flush()

    # Hot-patch runtime settings so auth-url works immediately
    settings.slack_client_id = org.slack_client_id
    settings.slack_client_secret = org.slack_client_secret
    settings.slack_signing_secret = org.slack_signing_secret

    await session.commit()

    preview = org.slack_client_id[:6] + "..." + org.slack_client_id[-4:] if len(org.slack_client_id) > 10 else None
    return SlackCredentialsResponse(configured=True, client_id_preview=preview)


@router.delete("/credentials")
async def clear_credentials(
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Clear saved Slack credentials and disconnect if connected."""
    org_id = current_user["org_id"]

    # Disconnect installation and mappings if any
    await SlackOAuthService.disconnect(session, org_id)

    # Clear org credentials
    result = await session.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if org:
        org.slack_client_id = ""
        org.slack_client_secret = ""
        org.slack_signing_secret = ""

    # Clear runtime settings
    settings.slack_client_id = ""
    settings.slack_client_secret = ""
    settings.slack_signing_secret = ""

    await session.commit()
    return {"cleared": True}


@router.get("/auth-url", response_model=SlackAuthUrlResponse)
async def get_auth_url(
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    client_id, client_secret = await _get_slack_credentials(session, current_user["org_id"])
    if not client_id or not client_secret:
        raise HTTPException(400, "Slack App credentials have not been saved yet.")

    auth_url = SlackOAuthService.build_auth_url(
        org_id=current_user["org_id"],
        user_id=int(current_user["sub"]),
        client_id_override=client_id,
    )
    return SlackAuthUrlResponse(auth_url=auth_url)


@router.get("/callback")
async def oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Handle the OAuth redirect from Slack, then redirect back to bioAF."""
    try:
        claims = SlackOAuthService.decode_state(state)
    except Exception:
        raise HTTPException(400, "Invalid or expired state token")

    org_id = claims["org_id"]
    user_id = claims["user_id"]

    client_id, client_secret = await _get_slack_credentials(session, org_id)

    try:
        token_data = await SlackOAuthService.exchange_code(code, client_id, client_secret)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await SlackOAuthService.save_installation(session, org_id, user_id, token_data)
    await session.commit()

    # Redirect browser back to the Slack settings page
    callback_path = "/api/notifications/slack/callback"
    request_url = str(request.url).split("?")[0]
    if request_url.endswith(callback_path):
        origin = request_url[: -len(callback_path)]
    else:
        origin = str(request.base_url).rstrip("/")
    return RedirectResponse(url=f"{origin}/settings/slack?connected=true")


@router.get("/status", response_model=SlackStatusResponse)
async def get_status(
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    status = await SlackOAuthService.get_status(session, current_user["org_id"])
    return SlackStatusResponse(**status)


@router.delete("/disconnect")
async def disconnect(
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    removed = await SlackOAuthService.disconnect(session, current_user["org_id"])
    if not removed:
        raise HTTPException(404, "No Slack installation found")
    await session.commit()
    return {"disconnected": True}


@router.get("/channels", response_model=list[SlackChannelResponse])
async def list_channels(
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    install = await SlackOAuthService.get_installation(session, current_user["org_id"])
    if not install:
        raise HTTPException(404, "Slack not connected")

    channels = await SlackOAuthService.list_channels(session, current_user["org_id"])
    return [SlackChannelResponse(**ch) for ch in channels]


# ---- Channel mappings ----


@router.get("/channel-mappings", response_model=list[SlackChannelMappingResponse])
async def list_channel_mappings(
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    mappings = await SlackOAuthService.list_channel_mappings(session, current_user["org_id"])
    return [SlackChannelMappingResponse.model_validate(m) for m in mappings]


@router.post("/channel-mappings", response_model=SlackChannelMappingResponse)
async def create_channel_mapping(
    body: SlackChannelMappingCreate,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    install = await SlackOAuthService.get_installation(session, current_user["org_id"])
    if not install:
        raise HTTPException(404, "Slack not connected")

    mapping = await SlackOAuthService.create_channel_mapping(session, current_user["org_id"], body.model_dump())
    await session.commit()
    return SlackChannelMappingResponse.model_validate(mapping)


@router.put("/channel-mappings/{mapping_id}", response_model=SlackChannelMappingResponse)
async def update_channel_mapping(
    mapping_id: int,
    body: SlackChannelMappingUpdate,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    mapping = await SlackOAuthService.update_channel_mapping(
        session, mapping_id, current_user["org_id"], body.model_dump(exclude_unset=True)
    )
    if not mapping:
        raise HTTPException(404, "Channel mapping not found")
    await session.commit()
    return SlackChannelMappingResponse.model_validate(mapping)


@router.delete("/channel-mappings/{mapping_id}")
async def delete_channel_mapping(
    mapping_id: int,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    deleted = await SlackOAuthService.delete_channel_mapping(session, mapping_id, current_user["org_id"])
    if not deleted:
        raise HTTPException(404, "Channel mapping not found")
    await session.commit()
    return {"deleted": True}
