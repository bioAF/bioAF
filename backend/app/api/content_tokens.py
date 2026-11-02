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

import jwt
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.content_access import authorize_file, authorize_thumbnail
from app.config import settings
from app.database import get_session
from app.exceptions import ValidationError
from app.services.auth_service import CONTENT_TOKEN_PURPOSE
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.content_tokens")

router = APIRouter(prefix="/api/content-tokens", tags=["content-tokens"])

CONTENT_TOKEN_TTL_SECONDS = 60


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
    """Decode a content token and verify every claim it must carry.

    Returns the payload dict or raises ValidationError. Matching the claims to the
    requested resource is the auth middleware's job; it knows the path.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError as e:
        raise ValidationError(f"Invalid content token: {e}") from e

    if payload.get("purpose") != CONTENT_TOKEN_PURPOSE:
        raise ValidationError("Not a content token")
    if payload.get("resource_type") not in {t.value for t in ContentTokenResourceType}:
        raise ValidationError("Content token names no resource type")
    if not isinstance(payload.get("resource_id"), int) or not isinstance(payload.get("org_id"), int):
        raise ValidationError("Content token names no resource or organization")

    return payload


@router.post("", response_model=ContentTokenResponse)
async def create_token(
    body: ContentTokenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Issue a short-lived content token for inline resource display."""
    current_user = request.state.current_user

    # A token carries no role, so this is the only point the caller is checked.
    if body.resource_type == ContentTokenResourceType.file:
        await authorize_file(session, current_user, body.resource_id)
    elif body.resource_type == ContentTokenResourceType.plot_thumbnail:
        await authorize_thumbnail(session, current_user, body.resource_id)

    token = create_content_token(body.resource_type.value, body.resource_id, int(current_user["org_id"]))
    return ContentTokenResponse(token=token, expires_in=CONTENT_TOKEN_TTL_SECONDS)
