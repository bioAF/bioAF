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


@dataclass(frozen=True)
class IssuedLink:
    uri: str
    url: str
    expires_at: datetime

    @property
    def expires_in_hours(self) -> float:
        """The usable lifetime at issuance, in hours (the response field the frontend reads)."""
        seconds = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return round(max(seconds, 0) / 3600, 2)


def _min_ttl() -> int:
    return settings.export_signed_min_ttl_seconds


def ensure_usable(link: IssuedLink) -> None:
    """Refuse a link that would not stay usable for the minimum lifetime from now."""
    remaining = (link.expires_at - datetime.now(timezone.utc)).total_seconds()
    if remaining < _min_ttl():
        raise StorageUnavailableError(
            "A download link could not be issued for long enough right now. Try again later, or download the "
            "export directly."
        )


async def check_signing_available() -> None:
    """Early, cheap refusal before any archive work when the store cannot sign a usable link."""
    from app.adapters.registry import get_storage_adapter

    await get_storage_adapter().check_signed_download(min_ttl_seconds=_min_ttl())


def export_key(org_id: int) -> str:
    """``exports/<org-id>/<uuid>.zip``: unique per export, whatever the clock or entity name."""
    return f"{EXPORT_PREFIX}/{org_id}/{uuid.uuid4()}.zip"


async def issue_signed_link(prepared, *, org_id: int) -> IssuedLink:
    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()
    uri = await adapter.resolve_uri(StorageStore.CONFIG_BACKUPS, export_key(org_id))
    try:
        async with asyncio.timeout(resources.ISSUE_DEADLINE):
            await adapter.upload_filename(uri, prepared.path, content_type="application/zip")
            signed = await adapter.sign_download(
                uri, max_ttl_seconds=SIGNED_MAX_TTL_SECONDS, min_ttl_seconds=_min_ttl()
            )
        link = IssuedLink(uri=uri, url=signed.url, expires_at=signed.expires_at)
        ensure_usable(link)
    except BaseException as exc:
        await runtime.shielded(_delete(uri), role="data-export-withdraw")
        if isinstance(exc, (DomainError, asyncio.CancelledError)):
            raise
        if isinstance(exc, TimeoutError):
            raise StorageUnavailableError("Uploading the export took too long. Try again later.") from exc
        logger.error("Export upload or signing failed (%s)", type(exc).__name__)
        raise StorageUnavailableError("The export could not be uploaded for a download link. Try again later.") from exc
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
    """Release a direct export whose response never ran (its own close is idempotent)."""

    async def backstop() -> None:
        await asyncio.sleep(resources.TRANSFER_DEADLINE + 2 * resources.SETTLE_SECONDS)
        prepared.release()

    runtime.spawn(backstop(), role="data-export-backstop")
