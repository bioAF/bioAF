"""GCP Cloud Logging implementation of LogSinkProvider (Phase 9G)."""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.observability.base import LogSinkProvider

try:
    import google.cloud.logging as cloud_logging
except ImportError:  # pragma: no cover
    cloud_logging = None  # type: ignore[assignment]


class GcpLogSinkProvider(LogSinkProvider):
    """Ships logs to Google Cloud Logging using the app's service account."""

    def __init__(self, project_id: str, credentials: Any = None):
        self.project_id = project_id
        self.credentials = credentials

    def build_handler(self, *, debug: bool = False) -> logging.Handler | None:
        if cloud_logging is None:
            return None
        try:
            client = cloud_logging.Client(project=self.project_id, credentials=self.credentials)
            handler = client.get_default_handler()
            handler.setLevel(logging.DEBUG if debug else logging.INFO)
            return handler
        except Exception:
            # Auth/network failure: fall back to stdout-only (caller logs it).
            return None
