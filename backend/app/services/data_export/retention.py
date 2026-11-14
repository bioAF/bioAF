"""Retiring signed-export objects: the hourly ``export-cleanup`` job's pass (plan 24 decision 7).

Signed exports live under ``exports/<org-id>/`` in the config-backups store, the same store and
prefix the upload resolves. A pass lists that prefix through the storage adapter, so GCS, S3 and the
local/NFS stores are all retired by their adapter-neutral ``uploaded_at``:

- Current objects (``exports/<org>/<uuid>.zip``) are deleted 48 h after upload: longer than the
  15 min issuance budget plus the 24 h maximum link lifetime, so no advertised link loses its
  object early. A late or failed pass only extends retention.
- Objects from before this scheme (``exports/<org>/<YYYYmmdd_HHMMSS>_<name>.zip``) may have been
  signed for 24 h by code that did not record when; they are deleted only once both 24 h have passed
  since this scheme was activated and 48 h since their upload.
- Anything else under the prefix, or with an unknown upload time, is left alone.

Activation is ``platform_config.export_retention_activated_at``: inserted once, if absent, by the
first pass, and committed before anything is listed, so a later failure never moves it. An existing
value is never overwritten, even an unreadable one; that blocks legacy deletion with a diagnostic.

Deleting the live object ends access; bucket versioning may keep the bytes. The config-backups
lifecycle expires noncurrent ``exports/`` versions after a day (see the storage Terraform modules).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import StorageStore
from app.exceptions import ValidationError
from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.export.retention")

ACTIVATION_KEY = "export_retention_activated_at"
LIVE_RETENTION = timedelta(hours=48)
LEGACY_GRACE = timedelta(hours=24)
PREFIX = "exports/"

_CURRENT = re.compile(r"^exports/\d+/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.zip$")
_LEGACY = re.compile(r"^exports/\d+/\d{8}_\d{6}_[^/]*\.zip$")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


async def activation(session: AsyncSession) -> datetime | None:
    """The persisted activation time, recording it first if absent. None blocks legacy deletion."""
    try:
        stored = await PlatformConfigService.insert_if_absent(
            session, ACTIVATION_KEY, datetime.now(timezone.utc).isoformat()
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error("Export retention activation could not be recorded (%s); legacy exports kept", type(exc).__name__)
        return None
    parsed = _parse(stored)
    if parsed is None:
        logger.error("Export retention activation is unreadable; legacy exports kept until it is corrected")
    return parsed


def _eligible(key: str, uploaded_at: datetime | None, activated: datetime | None, now: datetime) -> bool:
    if uploaded_at is None or uploaded_at.tzinfo is None:
        return False
    if _CURRENT.match(key):
        return now >= uploaded_at + LIVE_RETENTION
    if _LEGACY.match(key):
        return activated is not None and now >= activated + LEGACY_GRACE and now >= uploaded_at + LIVE_RETENTION
    return False


async def run(session: AsyncSession) -> int:
    """One cleanup pass; returns how many objects it deleted."""
    from app.adapters.registry import get_storage_adapter

    activated = await activation(session)
    adapter = get_storage_adapter()
    try:
        prefix = await adapter.resolve_uri(StorageStore.CONFIG_BACKUPS, PREFIX)
    except ValidationError:
        return 0  # no config-backups store on this install: nothing was ever exported to it

    now = datetime.now(timezone.utc)
    deleted = unknown_times = 0
    for obj in await adapter.list_objects(prefix):
        _, key = adapter.parse_uri(obj.storage_uri)
        if obj.uploaded_at is None or obj.uploaded_at.tzinfo is None:
            unknown_times += 1
            continue
        if not _eligible(key, obj.uploaded_at, activated, now):
            continue
        try:
            await adapter.delete(obj.storage_uri)
            deleted += 1
        except Exception as exc:
            logger.warning("Export cleanup could not delete an object (%s)", type(exc).__name__)
    if unknown_times:
        logger.warning("Export cleanup skipped %d object(s) with no upload time", unknown_times)
    if deleted:
        logger.info("Export cleanup: deleted %d expired export(s)", deleted)
    return deleted
