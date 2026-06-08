"""Messaging provider factory (Phase 9E).

Bootstrap-style platform provider selected by config (default GCP). The Pub/Sub
listener resolves credentials from platform_config at start and passes them in.
"""

from __future__ import annotations

from typing import Any

from app.adapters.messaging.base import MessagingProvider, ReceivedMessage

__all__ = ["MessagingProvider", "ReceivedMessage", "create_messaging_provider"]

VALID_MESSAGING_BACKENDS = ("gcp",)
DEFAULT_MESSAGING_BACKEND = "gcp"


def create_messaging_provider(credentials: Any = None, backend: str = DEFAULT_MESSAGING_BACKEND) -> MessagingProvider:
    """Instantiate the messaging provider for ``backend`` (default GCP)."""
    if backend not in VALID_MESSAGING_BACKENDS:
        raise ValueError(f"Unknown messaging backend '{backend}'. Valid options: {VALID_MESSAGING_BACKENDS}")
    from app.adapters.messaging.gcp import GcpMessagingProvider

    return GcpMessagingProvider(credentials)
