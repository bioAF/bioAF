"""When to stop waiting for a notebook launch: one policy for every observer.

Provider-neutral. An adapter reports what it sees as a ``ServiceState`` (plus details); this
decides, against one overall deadline counted from the recorded launch, whether the launch is
ready, has failed, or is still worth waiting for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.adapters.models import ServiceState

# Session types whose launch, readiness and cleanup this lifecycle manages. Work Nodes ("ssh") and
# headless executions have their own lifecycles.
INTERACTIVE_SESSION_TYPES = ("jupyter", "rstudio")

# Node provisioning, image download, initialization, and an address, all together.
STARTUP_DEADLINE = timedelta(minutes=30)
STARTUP_TIMEOUT_REASON = "startup_timeout"


@dataclass(frozen=True)
class LaunchDecision:
    action: str  # "wait", "ready", or "fail"
    access_url: str | None = None
    reason: str | None = None
    message: str | None = None


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return _parse(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def launch_started_at(provider_metadata: dict | None, *, started_at, created_at) -> datetime | None:
    """The recorded launch time (the reservation), else the row's own start or creation time."""
    reserved = _parse(((provider_metadata or {}).get("lifecycle") or {}).get("reserved_at"))
    return reserved or _parse(started_at) or _parse(created_at)


def decide_launch(
    observed: ServiceState, access_url: str | None, details: dict, *, started: datetime | None, now: datetime
) -> LaunchDecision:
    if observed == ServiceState.RUNNING and access_url:
        return LaunchDecision("ready", access_url=access_url)
    if observed == ServiceState.ERROR:
        return LaunchDecision(
            "fail",
            reason=details.get("failure_reason") or "unknown",
            message=details.get("failure_message") or "The session failed to start.",
        )
    if started is not None and now - started >= STARTUP_DEADLINE:
        minutes = int(STARTUP_DEADLINE.total_seconds() // 60)
        message = f"The session did not become ready within {minutes} minutes."
        cause = details.get("waiting_message")
        return LaunchDecision(
            "fail",
            reason=details.get("waiting_reason") or STARTUP_TIMEOUT_REASON,
            message=f"{message} Last observed: {cause}" if cause else message,
        )
    return LaunchDecision("wait")
