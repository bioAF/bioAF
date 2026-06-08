"""LogSinkProvider: the BAL seam for centralized log shipping (Phase 9G).

A platform-service provider: it builds a logging.Handler that ships the app's
logs to the backend's centralized sink. The caller (logging_config) owns stdout
logging and secret redaction; the provider owns only the backend-specific handler
so a non-GCP install (CloudWatch, Loki, stdout-only) does not require the Cloud
Logging SDK.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod


class LogSinkProvider(ABC):
    """Builds a centralized-logging handler for the active backend."""

    @abstractmethod
    def build_handler(self, *, debug: bool = False) -> logging.Handler | None:
        """Return a handler that ships logs to the backend sink, or None if the
        sink is unavailable (SDK missing, auth error). The caller attaches it and
        adds secret redaction; on None it stays stdout-only."""
