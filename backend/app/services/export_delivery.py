"""Handing a prepared export to its consumer: a signed link, or a direct response's backstop.

Signed delivery uploads the archive under a unique key in the config-backups store and signs a
GET link. Any failure before the link is handed over (upload, signing, the audit commit) deletes
that request's object, and only that object; a cleanup failure is logged without masking the
original error. Successful objects outlive local scratch and are retired by the hourly cleanup
(``data_export/retention.py``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app import runtime
from app.adapters.models import StorageStore
from app.config import settings
from app.exceptions import DomainError, StorageUnavailableError
from app.services.data_export import resources

logger = logging.getLogger("bioaf.export")

SIGNED_MAX_TTL_SECONDS = 24 * 3600
EXPORT_PREFIX = "exports"


# Time allowed between issuing a link and handing it over (the audit commit and the response).
HANDOFF_SECONDS = 60

TOO_SHORT = (
    "A download link could not be issued for long enough right now. Try again later, or download the export directly."
)


@dataclass(frozen=True)
class IssuedLink:
    uri: str
    url: str
    expires_at: datetime

    def remaining_seconds(self) -> float:
        return (self.expires_at - datetime.now(timezone.utc)).total_seconds()

    def expires_in_hours(self) -> float:
        """The link's remaining lifetime now, in hours, rounded down to 0.01 h: never overstated."""
        seconds = min(self.remaining_seconds(), SIGNED_MAX_TTL_SECONDS)
        return math.floor(max(seconds, 0) / 36) / 100

    def handoff_deadline(self) -> float:
        """The event-loop time by which the link must be handed over to still meet the minimum."""
        return asyncio.get_running_loop().time() + self.remaining_seconds() - _min_ttl()


def _min_ttl() -> int:
    return settings.export_signed_min_ttl_seconds


def ensure_usable(link: IssuedLink, *, handoff: float = 0) -> None:
    """Refuse a link that would not stay usable for the minimum (plus ``handoff``) from now."""
    if link.remaining_seconds() < _min_ttl() + handoff:
        raise StorageUnavailableError(TOO_SHORT)


async def check_signing_available() -> None:
    """Early, cheap refusal before any archive work when the store cannot sign a usable link."""
    from app.adapters.registry import get_storage_adapter

    await get_storage_adapter().check_signed_download(min_ttl_seconds=_min_ttl())


def export_key(org_id: int) -> str:
    """``exports/<org-id>/<uuid>.zip``: unique per export, whatever the clock or entity name."""
    return f"{EXPORT_PREFIX}/{org_id}/{uuid.uuid4()}.zip"


async def issue_signed_link(prepared, *, org_id: int) -> IssuedLink:
    """Upload and sign; the archive stays owned by ``prepared`` until its upload worker settles."""
    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()
    loop = asyncio.get_running_loop()
    started = loop.time()
    uri = await adapter.resolve_uri(StorageStore.CONFIG_BACKUPS, export_key(org_id))
    try:
        await prepared.job.upload(adapter, uri, deadline=resources.ISSUE_DEADLINE, undo=lambda: _delete(uri))
    except (DomainError, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.error("Export upload failed (%s)", type(exc).__name__)
        raise StorageUnavailableError("The export could not be uploaded for a download link. Try again later.") from exc
    try:
        async with asyncio.timeout_at(started + resources.ISSUE_DEADLINE):
            signed = await adapter.sign_download(
                uri, max_ttl_seconds=SIGNED_MAX_TTL_SECONDS, min_ttl_seconds=_min_ttl()
            )
        link = IssuedLink(uri=uri, url=signed.url, expires_at=signed.expires_at)
        ensure_usable(link, handoff=HANDOFF_SECONDS)
    except BaseException as exc:
        await runtime.shielded(_delete(uri), role="data-export-withdraw")
        if isinstance(exc, (DomainError, asyncio.CancelledError)):
            raise
        if isinstance(exc, TimeoutError):
            raise StorageUnavailableError("Signing the download link took too long. Try again later.") from exc
        logger.error("Export link signing failed (%s)", type(exc).__name__)
        raise StorageUnavailableError("A download link could not be signed. Try again later.") from exc
    return link


async def withdraw(link: IssuedLink) -> None:
    """Delete an issued link's object (its success was not recorded)."""
    await runtime.shielded(_delete(link.uri), role="data-export-withdraw")


async def _delete(uri: str) -> None:
    from app.adapters.registry import get_storage_adapter

    try:
        await get_storage_adapter().delete(uri)
    except Exception as exc:
        logger.error("Export object cleanup failed (%s)", type(exc).__name__)


def watch_unclaimed(prepared) -> None:
    """Release a direct export whose response never started; a started response releases itself."""

    async def backstop() -> None:
        await asyncio.sleep(resources.TRANSFER_DEADLINE + 2 * resources.SETTLE_SECONDS)
        if not prepared.claimed:
            logger.warning("A prepared data export was never sent; releasing it")
            prepared.release()

    if not prepared.claimed:
        prepared.backstop = runtime.spawn(backstop(), role="data-export-backstop")
