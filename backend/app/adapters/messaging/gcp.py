"""GCP Pub/Sub implementation of MessagingProvider (Phase 9E)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.adapters.messaging.base import MessagingProvider, ReceivedMessage


class GcpMessagingProvider(MessagingProvider):
    """Pulls OBJECT_FINALIZE events from a Pub/Sub subscription.

    Holds one SubscriberClient (built lazily with the app's credentials) and
    offloads its blocking calls to a thread, so callers stay async and SDK-free.
    """

    def __init__(self, credentials: Any = None):
        self._credentials = credentials
        self._subscriber: Any = None

    def _client(self) -> Any:
        if self._subscriber is None:
            from google.cloud import pubsub_v1

            self._subscriber = (
                pubsub_v1.SubscriberClient(credentials=self._credentials)
                if self._credentials
                else pubsub_v1.SubscriberClient()
            )
        return self._subscriber

    async def pull(
        self, subscription_path: str, *, max_messages: int = 10, timeout: int = 30
    ) -> list[ReceivedMessage]:
        response = await asyncio.to_thread(
            self._client().pull,
            subscription=subscription_path,
            max_messages=max_messages,
            timeout=timeout,
        )
        return [ReceivedMessage(ack_id=m.ack_id, data=m.message.data) for m in response.received_messages]

    async def acknowledge(self, subscription_path: str, ack_ids: list[str]) -> None:
        await asyncio.to_thread(
            self._client().acknowledge,
            subscription=subscription_path,
            ack_ids=ack_ids,
        )

    async def nack(self, subscription_path: str, ack_ids: list[str]) -> None:
        await asyncio.to_thread(
            self._client().modify_ack_deadline,
            subscription=subscription_path,
            ack_ids=ack_ids,
            ack_deadline_seconds=0,
        )
