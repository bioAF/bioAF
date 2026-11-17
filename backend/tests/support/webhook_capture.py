"""Mutation-to-delivery assertions for webhook producers.

``WebhookCapture`` subscribes an organization to every public event, installs a scoped dispatcher
binding (so a test does not depend on the server lifespan), and reads the persisted delivery rows
back through an independent session after the producing transaction commits:

    capture = await WebhookCapture.start(session, admin_user)
    ...mutate and commit...
    rows = await capture.deliveries()          # [(public_event, data), ...] in insertion order
    capture.close()
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app import database as database_module
from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import webhook_dispatcher
from app.services.event_types import ALL_WEBHOOK_EVENT_TYPES
from app.services.post_commit_events import wait_for_deferred_events


class WebhookCapture:
    def __init__(self, org_id: int, binding) -> None:
        self.org_id = org_id
        self.binding = binding

    @classmethod
    async def start(cls, session, user, *, org_id: int | None = None) -> "WebhookCapture":
        org = org_id if org_id is not None else user.organization_id
        session.add(
            WebhookSubscription(
                organization_id=org,
                name="capture",
                url="https://hooks.example/capture",
                secret="whsec_capture",
                events=list(ALL_WEBHOOK_EVENT_TYPES),
                created_by_user_id=user.id,
            )
        )
        await session.commit()
        binding = webhook_dispatcher.subscribe_all(session_factory=None, collect_receipts=True)
        return cls(org, binding)

    async def deliveries(self, event: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        await wait_for_deferred_events()
        async with database_module.async_session_factory() as s:
            rows = (
                (
                    await s.execute(
                        select(WebhookDelivery)
                        .join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
                        .where(WebhookSubscription.organization_id == self.org_id)
                        .order_by(WebhookDelivery.id)
                    )
                )
                .scalars()
                .all()
            )
        out = [(r.event_type, r.payload_json["data"]) for r in rows]
        if event is not None:
            out = [(e, d) for e, d in out if e == event]
        return out

    async def data(self, event: str) -> list[dict[str, Any]]:
        return [d for _, d in await self.deliveries(event)]

    def failures(self):
        return [r for r in self.binding.receipts if r.outcome == "failed"]

    def close(self) -> None:
        self.binding.close()
