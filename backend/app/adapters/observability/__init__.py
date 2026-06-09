"""Log-sink provider factory (Phase 9G).

Like secrets, this is a bootstrap-time platform provider selected by config
(default GCP) rather than the DB-backed registry: logging is attached during
startup, around the time the DB comes up.
"""

from __future__ import annotations

from typing import Any

from app.adapters.observability.base import LogSinkProvider
from app.exceptions import ValidationError

VALID_LOG_SINK_BACKENDS = ("gcp",)
DEFAULT_LOG_SINK_BACKEND = "gcp"


def create_log_sink_provider(
    project_id: str,
    credentials: Any = None,
    backend: str = DEFAULT_LOG_SINK_BACKEND,
) -> LogSinkProvider:
    """Instantiate the log-sink provider for ``backend`` (default GCP)."""
    if backend not in VALID_LOG_SINK_BACKENDS:
        raise ValidationError(f"Unknown log sink backend '{backend}'. Valid options: {VALID_LOG_SINK_BACKENDS}")
    from app.adapters.observability.gcp import GcpLogSinkProvider

    return GcpLogSinkProvider(project_id, credentials)
