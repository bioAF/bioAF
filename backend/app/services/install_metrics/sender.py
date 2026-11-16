"""The bounded delivery of reserved installation metrics reports to PostHog (ADR-075).

One pass: apply any change of the operator override, expire reports older than 7 days, then send at
most 5 due reports, each over its own short HTTPS request (3-second timeout) to
``<host>/i/v0/e/``. Consent, the enabled interval and the override are rechecked before every send.
No database transaction is open during a network call. A failure backs the report off (doubling,
capped at 24 hours) and keeps its event UUID and reporting date, so a retry is the identical event
and PostHog deduplicates it. Diagnostics carry safe codes only: never the token, a payload, or
exception or HTTP text. Delivery is best effort and never affects setup, login, upgrades or readiness.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from app.services.install_metrics import outbox
from app.services.install_metrics import payload as allowlist
from app.services.install_metrics import upgrades
from app.services.install_metrics.state import InstallMetricsService, override_active, setup_finished, utcnow

logger = logging.getLogger("bioaf.install_metrics")

TIMEOUT_SECONDS = 3.0
BATCH = 5
FIRST_BACKOFF = timedelta(minutes=1)
MAX_BACKOFF = timedelta(hours=24)
EXPIRY = timedelta(days=7)
CAPTURE_PATH = "/i/v0/e/"

_TOKEN = re.compile(r"^phc_[A-Za-z0-9]{20,64}$")


@dataclass(frozen=True)
class Destination:
    token: str
    host: str

    @property
    def url(self) -> str:
        return f"{self.host}{CAPTURE_PATH}"


def _valid_host(host: str) -> bool:
    parts = urlsplit(host)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
    )


def resolve_destination() -> Destination | None:
    """Where reports go, or None (logged as a safe code) when delivery is disabled or misconfigured.

    The runtime pair replaces the release defaults only as a pair: a partial or invalid override is
    refused rather than mixing test and production configuration."""
    from app import install_metrics_release as release
    from app.config import settings

    if settings.install_metrics_disabled:
        return None
    token = settings.posthog_project_token.strip()
    host = settings.posthog_host.strip()
    if bool(token) != bool(host):
        logger.warning("install_metrics destination=refused code=partial_override")
        return None
    if not token:
        token, host = release.POSTHOG_PROJECT_TOKEN, release.POSTHOG_HOST
    if not _TOKEN.match(token or ""):
        logger.warning("install_metrics destination=refused code=invalid_token")
        return None
    if not _valid_host(host or ""):
        logger.warning("install_metrics destination=refused code=invalid_host")
        return None
    return Destination(token=token, host=host.rstrip("/"))


def transport_factory() -> httpx.AsyncBaseTransport | None:
    """The transport the sender uses when none is injected (None: httpx's real network).

    The test suite replaces this with one that refuses every request (tests/conftest.py)."""
    return None


def backoff(attempts: int) -> timedelta:
    return min(FIRST_BACKOFF * (2 ** max(attempts - 1, 0)), MAX_BACKOFF)


async def _post(destination: Destination, event: dict, transport) -> str | None:
    """Send one report; None on acceptance, otherwise a safe failure code."""
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(destination.url, json={"api_key": destination.token, **event})
    except httpx.TimeoutException:
        return "timeout"
    except httpx.HTTPError:
        return "network"
    except Exception:
        return "transport"
    if 200 <= response.status_code < 300:
        return None
    return f"http_{response.status_code}"


async def send_pending(
    factory: Callable,
    *,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int]:
    """One delivery pass; returns counts by outcome."""
    now = now or utcnow()
    counts = {"sent": 0, "failed": 0, "rejected": 0, "expired": 0}

    async with factory() as session:
        state = await InstallMetricsService.reconcile(session, now)
        # A completed host upgrade whose updater could not reach the backend (app/services/install_metrics/upgrades.py).
        await upgrades.reconcile_from_status(session, now)
        expired = await session.execute(
            text(
                "UPDATE install_metric_events SET status = 'expired', payload = NULL, settled_at = :now "
                "WHERE status = 'pending' AND created_at < :cutoff"
            ),
            {"now": now, "cutoff": now - EXPIRY},
        )
        counts["expired"] = expired.rowcount or 0
        await session.commit()
    if not state.effective(override_active()):
        return counts
    destination = resolve_destination()
    if destination is None:
        return counts

    async with factory() as session:
        due = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM install_metric_events WHERE status = 'pending' AND next_attempt_at <= :now "
                        "ORDER BY next_attempt_at, id LIMIT :batch"
                    ),
                    {"now": now, "batch": BATCH},
                )
            )
            .scalars()
            .all()
        )
        await session.commit()

    for event_id in due:
        async with factory() as session:
            state = await InstallMetricsService.read(session)
            row = (
                await session.execute(
                    text(
                        "SELECT payload, created_at, attempts FROM install_metric_events "
                        "WHERE id = :id AND status = 'pending'"
                    ),
                    {"id": event_id},
                )
            ).first()
            finished = await setup_finished(session, state)
            await session.commit()
        if not state.effective(override_active()):
            break
        if row is None:
            continue
        if not finished:
            await _settle(factory, event_id, now, status=outbox.CANCELLED, error="setup_incomplete")
            continue
        event, created_at, attempts = row
        if state.enabled_since is None or created_at < state.enabled_since:
            await _settle(factory, event_id, now, status=outbox.CANCELLED, error="outside_interval")
            continue
        try:
            allowlist.validate_event(event)
        except allowlist.PayloadRejected:
            counts["rejected"] += 1
            logger.error("install_metrics delivery=rejected code=payload_rejected")
            await _settle(factory, event_id, now, status=outbox.CANCELLED, error="payload_rejected")
            continue

        failure = await _post(destination, event, transport if transport is not None else transport_factory())

        if failure is None:
            counts["sent"] += 1
            await _settle(factory, event_id, now, status=outbox.SENT, error=None)
        else:
            counts["failed"] += 1
            logger.warning("install_metrics delivery=failed code=%s attempts=%d", failure, attempts + 1)
            await _retry(factory, event_id, now, attempts + 1, failure)
    if any(counts.values()):
        logger.info("install_metrics delivery=pass %s", " ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


async def _settle(factory, event_id: int, now: datetime, *, status: str, error: str | None) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE install_metric_events SET status = :status, payload = NULL, settled_at = :now, "
                "attempts = attempts + :sends, last_error = :error "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"status": status, "now": now, "sends": int(status == outbox.SENT), "error": error, "id": event_id},
        )
        await session.commit()


async def _retry(factory, event_id: int, now: datetime, attempts: int, error: str) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE install_metric_events SET attempts = :attempts, next_attempt_at = :next, last_error = :error "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"attempts": attempts, "next": now + backoff(attempts), "error": error, "id": event_id},
        )
        await session.commit()
