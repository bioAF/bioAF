"""Short-lived, scoped tokens for inline content endpoints.

These replace full session JWTs in URL query parameters for
<img src> and similar tags that cannot send Authorization headers.
A content token is scoped to a single resource, expires in 60 seconds,
and carries no user identity -- so if leaked to logs, referrer headers,
or browser history, the exposure is minimal.
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from pydantic import BaseModel

from app.config import settings
from app.database import get_session
from app.services.file_service import FileService
from app.services.plot_archive_service import PlotArchiveService
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.content_tokens")

router = APIRouter(prefix="/api/content-tokens", tags=["content-tokens"])

CONTENT_TOKEN_TTL_SECONDS = 60
CONTENT_TOKEN_PURPOSE = "content_access"


class ContentTokenResourceType(str, Enum):
    file = "file"
    plot_thumbnail = "plot_thumbnail"


class ContentTokenRequest(BaseModel):
    resource_type: ContentTokenResourceType
    resource_id: int


class ContentTokenResponse(BaseModel):
    token: str
    expires_in: int


def create_content_token(resource_type: str, resource_id: int, org_id: int) -> str:
    """Create a short-lived JWT scoped to one resource."""
    payload = {
        "purpose": CONTENT_TOKEN_PURPOSE,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "org_id": org_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=CONTENT_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def validate_content_token(token: str) -> dict:
    """Decode a content token and verify its purpose claim.

    Returns the payload dict or raises ValueError.
    """
    from jose import JWTError

    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise ValueError(f"Invalid content token: {e}") from e

    if payload.get("purpose") != CONTENT_TOKEN_PURPOSE:
        raise ValueError("Not a content token")

    return payload


@router.post("", response_model=ContentTokenResponse)
async def create_token(
    body: ContentTokenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Issue a short-lived content token for inline resource display."""
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    # Verify the resource exists and belongs to the caller's org
    if body.resource_type == ContentTokenResourceType.file:
        file = await FileService.get_file(session, body.resource_id, org_id)
        if not file:
            raise HTTPException(404, "File not found")
    elif body.resource_type == ContentTokenResourceType.plot_thumbnail:
        plot = await PlotArchiveService.get_plot(session, org_id, body.resource_id)
        if not plot:
            raise HTTPException(404, "Plot not found")

    token = create_content_token(body.resource_type.value, body.resource_id, org_id)
    return ContentTokenResponse(token=token, expires_in=CONTENT_TOKEN_TTL_SECONDS)
