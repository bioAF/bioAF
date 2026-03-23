from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.notification import (
    NotificationListResponse,
    NotificationPreferenceResponse,
    NotificationPreferencesUpdate,
    NotificationResponse,
    NotificationRuleResponse,
    NotificationRulesUpdate,
    SlackWebhookCreate,
    SlackWebhookResponse,
    SlackWebhookUpdate,
    TestDeliveryRequest,
    TestDeliveryResponse,
    UnreadCountResponse,
)
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    request: Request,
    read: bool | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    notifications, total = await NotificationService.list_notifications(
        session,
        int(current_user["sub"]),
        read,
        event_type,
        severity,
        page,
        page_size,
    )
    return NotificationListResponse(
        notifications=[NotificationResponse.model_validate(n) for n in notifications],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    count = await NotificationService.get_unread_count(session, int(current_user["sub"]))
    return UnreadCountResponse(count=count)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: int,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    notification = await NotificationService.mark_read(session, notification_id, int(current_user["sub"]))
    if not notification:
        raise HTTPException(404, "Notification not found")
    await session.commit()
    return NotificationResponse.model_validate(notification)


@router.post("/mark-all-read")
async def mark_all_read(
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    count = await NotificationService.mark_all_read(session, int(current_user["sub"]))
    await session.commit()
    return {"marked_read": count}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    deleted = await NotificationService.delete_notification(session, notification_id, int(current_user["sub"]))
    if not deleted:
        raise HTTPException(404, "Notification not found")
    await session.commit()
    return {"deleted": True}


# ---- Preferences ----


@router.get("/preferences", response_model=list[NotificationPreferenceResponse])
async def get_preferences(
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    prefs = await NotificationService.get_preferences(session, int(current_user["sub"]))
    return [NotificationPreferenceResponse.model_validate(p) for p in prefs]


@router.put("/preferences", response_model=list[NotificationPreferenceResponse])
async def update_preferences(
    body: NotificationPreferencesUpdate,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    prefs = await NotificationService.update_preferences(
        session,
        int(current_user["sub"]),
        [p.model_dump() for p in body.preferences],
    )
    await session.commit()
    return [NotificationPreferenceResponse.model_validate(p) for p in prefs]


# ---- Rules (admin only) ----


@router.get("/rules", response_model=list[NotificationRuleResponse])
async def get_rules(
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    rules = await NotificationService.get_rules(session, current_user["org_id"])
    return [NotificationRuleResponse.model_validate(r) for r in rules]


@router.put("/rules", response_model=list[NotificationRuleResponse])
async def update_rules(
    body: NotificationRulesUpdate,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    rules = await NotificationService.update_rules(
        session,
        current_user["org_id"],
        [r.model_dump() for r in body.rules],
    )
    await session.commit()
    return [NotificationRuleResponse.model_validate(r) for r in rules]


# ---- Slack webhooks (admin only) ----


@router.get("/slack-webhooks", response_model=list[SlackWebhookResponse])
async def list_webhooks(
    current_user: dict = require_permission("notifications", "view"),
    session: AsyncSession = Depends(get_session),
):
    webhooks = await NotificationService.list_webhooks(session, current_user["org_id"])
    return [SlackWebhookResponse.model_validate(w) for w in webhooks]


@router.post("/slack-webhooks", response_model=SlackWebhookResponse)
async def create_webhook(
    body: SlackWebhookCreate,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    webhook = await NotificationService.create_webhook(session, current_user["org_id"], body.model_dump())
    await session.commit()
    return SlackWebhookResponse.model_validate(webhook)


@router.put("/slack-webhooks/{webhook_id}", response_model=SlackWebhookResponse)
async def update_webhook(
    webhook_id: int,
    body: SlackWebhookUpdate,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    webhook = await NotificationService.update_webhook(
        session,
        webhook_id,
        current_user["org_id"],
        body.model_dump(exclude_unset=True),
    )
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    await session.commit()
    return SlackWebhookResponse.model_validate(webhook)


@router.delete("/slack-webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    deleted = await NotificationService.delete_webhook(session, webhook_id, current_user["org_id"])
    if not deleted:
        raise HTTPException(404, "Webhook not found")
    await session.commit()
    return {"deleted": True}


# ---- Test delivery ----


@router.post("/test", response_model=TestDeliveryResponse)
async def test_delivery(
    body: TestDeliveryRequest,
    current_user: dict = require_permission("notifications", "configure"),
    session: AsyncSession = Depends(get_session),
):
    result = await NotificationService.test_delivery(session, current_user["org_id"], body.channel)
    return TestDeliveryResponse(**result)
