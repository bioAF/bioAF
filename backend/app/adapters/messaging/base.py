"""MessagingProvider: the BAL seam for event-source subscriptions (Phase 9E).

Drives auto-ingest from object-store finalize events (ADR-024). GCP pulls from a
Pub/Sub subscription; AWS would use SNS/SQS or S3 events; on-prem could poll or
watch the filesystem. Messages are normalized to ``ReceivedMessage`` so the
listener never touches a backend SDK or its message shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ReceivedMessage:
    """A pulled message: an opaque ack handle plus the raw payload bytes."""

    ack_id: str
    data: bytes


class MessagingProvider(ABC):
    """Pull/ack/nack against a subscription on the active messaging backend."""

    @abstractmethod
    async def pull(
        self, subscription_path: str, *, max_messages: int = 10, timeout: int = 30
    ) -> list[ReceivedMessage]:
        """Pull up to ``max_messages`` messages, blocking up to ``timeout`` seconds."""

    @abstractmethod
    async def acknowledge(self, subscription_path: str, ack_ids: list[str]) -> None:
        """Acknowledge messages so they are not redelivered."""

    @abstractmethod
    async def nack(self, subscription_path: str, ack_ids: list[str]) -> None:
        """Negative-acknowledge (redeliver soon) by zeroing the ack deadline."""
