"""Admin endpoints for LIMS integration: service accounts, API keys, and
webhooks. JWT-authenticated; the UI 'Users and Accounts' page targets these.

The public integration sub-app at /api/v1/integrations/* is a separate
surface authenticated by API keys. These endpoints stay on the internal
/api/admin/* prefix so the UI uses its existing JWT pipeline.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import api_key_service, service_account_service, webhook_service

router = APIRouter(prefix="/api/admin", tags=["admin-integrations"])


# Service accounts -----------------------------------------------------------


class ServiceAccountCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
    role_id: int


class ServiceAccountUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=255)
    role_id: int | None = None


class ServiceAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str | None
    email: str
    role_id: int
    status: str
    created_at: datetime
    last_login: datetime | None = None


@router.get("/service-accounts", response_model=list[ServiceAccountOut])
async def list_service_accounts(
    user: dict = require_permission("users", "view"),
    session: AsyncSession = Depends(get_session),
):
    rows = await service_account_service.list_for_org(session, int(user["org_id"]))
    return [ServiceAccountOut.model_validate(r) for r in rows]


@router.post("/service-accounts", response_model=ServiceAccountOut, status_code=201)
async def create_service_account(
    body: ServiceAccountCreate,
    user: dict = require_permission("users", "invite"),
    session: AsyncSession = Depends(get_session),
):
    # role must belong to this org
    role = (
        await session.execute(select(Role).where(Role.id == body.role_id, Role.organization_id == int(user["org_id"])))
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(404, "role_not_found")
    sa = await service_account_service.create(
        session,
        org_id=int(user["org_id"]),
        display_name=body.display_name,
        role_id=body.role_id,
        created_by_user_id=int(user["sub"]),
    )
    await session.commit()
    return ServiceAccountOut.model_validate(sa)


@router.patch("/service-accounts/{sa_id}", response_model=ServiceAccountOut)
async def update_service_account(
    sa_id: int,
    body: ServiceAccountUpdate,
    user: dict = require_permission("users", "edit_role"),
    session: AsyncSession = Depends(get_session),
):
    if body.display_name is not None:
        await service_account_service.update_display_name(session, sa_id, body.display_name, int(user["sub"]))
    if body.role_id is not None:
        await service_account_service.update_role(session, sa_id, body.role_id, int(user["sub"]))
    await session.commit()
    sa = (await session.execute(select(User).where(User.id == sa_id))).scalar_one()
    return ServiceAccountOut.model_validate(sa)


@router.post("/service-accounts/{sa_id}/disable", response_model=ServiceAccountOut)
async def disable_service_account(
    sa_id: int,
    user: dict = require_permission("users", "deactivate"),
    session: AsyncSession = Depends(get_session),
):
    sa = await service_account_service.disable(session, sa_id, int(user["sub"]))
    await session.commit()
    return ServiceAccountOut.model_validate(sa)


# API keys -------------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str]


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    service_account_user_id: int


class ApiKeyMintOut(BaseModel):
    api_key: ApiKeyOut
    secret: str = Field(..., description="The full biokey_<prefix>.<secret>; shown exactly once")


@router.get(
    "/service-accounts/{sa_id}/api-keys",
    response_model=list[ApiKeyOut],
)
async def list_api_keys(
    sa_id: int,
    user: dict = require_permission("users", "view"),
    session: AsyncSession = Depends(get_session),
):
    # Confirm SA belongs to caller's org.
    sa = (
        await session.execute(select(User).where(User.id == sa_id, User.organization_id == int(user["org_id"])))
    ).scalar_one_or_none()
    if sa is None or not sa.is_service_account:
        raise HTTPException(404, "service_account_not_found")
    rows = await api_key_service.list_for_service_account(session, sa_id)
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.post(
    "/service-accounts/{sa_id}/api-keys",
    response_model=ApiKeyMintOut,
    status_code=201,
)
async def mint_api_key(
    sa_id: int,
    body: ApiKeyCreate,
    user: dict = require_permission("users", "edit_role"),
    session: AsyncSession = Depends(get_session),
):
    sa = (
        await session.execute(select(User).where(User.id == sa_id, User.organization_id == int(user["org_id"])))
    ).scalar_one_or_none()
    if sa is None or not sa.is_service_account:
        raise HTTPException(404, "service_account_not_found")
    try:
        row, secret = await api_key_service.mint(
            session,
            org_id=int(user["org_id"]),
            sa_user_id=sa_id,
            name=body.name,
            scopes=body.scopes,
            created_by_user_id=int(user["sub"]),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await session.commit()
    return ApiKeyMintOut(api_key=ApiKeyOut.model_validate(row), secret=secret)


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_api_key(
    key_id: int,
    user: dict = require_permission("users", "edit_role"),
    session: AsyncSession = Depends(get_session),
):
    key = (
        await session.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == int(user["org_id"])))
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(404, "api_key_not_found")
    revoked = await api_key_service.revoke(session, key_id, int(user["sub"]))
    await session.commit()
    return ApiKeyOut.model_validate(revoked)


@router.get("/api-keys/scope-alphabet")
async def list_scope_alphabet(
    _: dict = require_permission("users", "view"),
):
    """Return the set of valid scope strings the UI's chip picker uses."""
    return {"scopes": sorted(api_key_service.PUBLIC_SCOPE_ALPHABET)}


# Webhooks -------------------------------------------------------------------


class WebhookSubscriptionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=1024)
    events: list[str]


class WebhookSubscriptionUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookSubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookSubscriptionCreateOut(BaseModel):
    subscription: WebhookSubscriptionOut
    secret: str = Field(..., description="HMAC secret; shown exactly once")


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subscription_id: int
    event_id: str
    event_type: str
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    last_response_status: int | None
    last_attempted_at: datetime | None
    created_at: datetime
    delivered_at: datetime | None


@router.get("/webhooks", response_model=list[WebhookSubscriptionOut])
async def list_webhooks(
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    rows = await webhook_service.list_subscriptions(session, int(user["org_id"]))
    return [WebhookSubscriptionOut.model_validate(r) for r in rows]


@router.post("/webhooks", response_model=WebhookSubscriptionCreateOut, status_code=201)
async def create_webhook(
    body: WebhookSubscriptionCreate,
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    try:
        sub, secret = await webhook_service.create_subscription(
            session,
            org_id=int(user["org_id"]),
            name=body.name,
            url=body.url,
            events=body.events,
            created_by_user_id=int(user["sub"]),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await session.commit()
    return WebhookSubscriptionCreateOut(subscription=WebhookSubscriptionOut.model_validate(sub), secret=secret)


@router.patch("/webhooks/{sub_id}", response_model=WebhookSubscriptionOut)
async def update_webhook(
    sub_id: int,
    body: WebhookSubscriptionUpdate,
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    try:
        sub = await webhook_service.update_subscription(
            session,
            sub_id,
            int(user["org_id"]),
            int(user["sub"]),
            name=body.name,
            url=body.url,
            events=body.events,
            is_active=body.is_active,
        )
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await session.commit()
    return WebhookSubscriptionOut.model_validate(sub)


@router.delete("/webhooks/{sub_id}", response_model=WebhookSubscriptionOut)
async def disable_webhook(
    sub_id: int,
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    try:
        sub = await webhook_service.disable_subscription(session, sub_id, int(user["org_id"]), int(user["sub"]))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    await session.commit()
    return WebhookSubscriptionOut.model_validate(sub)


@router.post("/webhooks/{sub_id}/rotate-secret", response_model=WebhookSubscriptionCreateOut)
async def rotate_webhook_secret(
    sub_id: int,
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    try:
        sub, secret = await webhook_service.rotate_secret(session, sub_id, int(user["org_id"]), int(user["sub"]))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    await session.commit()
    return WebhookSubscriptionCreateOut(subscription=WebhookSubscriptionOut.model_validate(sub), secret=secret)


@router.get(
    "/webhooks/{sub_id}/deliveries",
    response_model=list[WebhookDeliveryOut],
)
async def list_webhook_deliveries(
    sub_id: int,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    sub = (
        await session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == sub_id,
                WebhookSubscription.organization_id == int(user["org_id"]),
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(404, "webhook_not_found")
    rows = await webhook_service.list_deliveries(session, sub_id, status=status, limit=limit)
    return [WebhookDeliveryOut.model_validate(r) for r in rows]


@router.post(
    "/webhooks/deliveries/{delivery_id}/replay",
    response_model=WebhookDeliveryOut,
)
async def replay_webhook_delivery(
    delivery_id: int,
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    # Confirm the delivery's subscription belongs to caller's org.
    delivery = (
        await session.execute(
            select(WebhookDelivery)
            .join(
                WebhookSubscription,
                WebhookSubscription.id == WebhookDelivery.subscription_id,
            )
            .where(
                WebhookDelivery.id == delivery_id,
                WebhookSubscription.organization_id == int(user["org_id"]),
            )
        )
    ).scalar_one_or_none()
    if delivery is None:
        raise HTTPException(404, "delivery_not_found")
    clone = await webhook_service.replay_delivery(session, delivery_id, int(user["sub"]))
    await session.commit()
    return WebhookDeliveryOut.model_validate(clone)


@router.post("/webhooks/{sub_id}/test", response_model=WebhookDeliveryOut)
async def fire_test_webhook(
    sub_id: int,
    user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    try:
        delivery = await webhook_service.fire_test_event(session, sub_id, int(user["org_id"]), int(user["sub"]))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    await session.commit()
    return WebhookDeliveryOut.model_validate(delivery)


# Audit log filtered to API-key activity ------------------------------------


class AuditRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    user_id: int | None
    api_key_id: int | None
    entity_type: str
    entity_id: int
    action: str
    details_json: dict | None


@router.get("/audit-log/api-activity", response_model=list[AuditRowOut])
async def list_api_activity(
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = Query(None),
    user: dict = require_permission("audit_log", "view"),
    session: AsyncSession = Depends(get_session),
):
    # Scope to rows whose actor user belongs to the caller's org.
    stmt = (
        select(AuditLog)
        .join(User, User.id == AuditLog.user_id)
        .where(
            AuditLog.api_key_id.is_not(None),
            User.organization_id == int(user["org_id"]),
        )
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    if cursor is not None:
        stmt = stmt.where(AuditLog.id < cursor)
    rows = (await session.execute(stmt)).scalars().all()
    return [AuditRowOut.model_validate(r) for r in rows]
