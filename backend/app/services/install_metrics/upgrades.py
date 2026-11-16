"""Successful host upgrade operations as installation metrics reports (ADR-075).

``./bioaf update`` (and the web update agent, which runs it) records each update as a host operation with
a UUID, its original start and source version, and an immutable completion time. After readiness and final
host completion it runs ``app.cli.record_upgrade_metric``, which reserves the report here without any
network I/O. If that call fails, ``reconcile_from_status`` reserves the same report from the completion
evidence the updater published in ``update-status/current.json``. Both use the operation's source key and
completion day, so they reserve one identical report.

Only an operation that began inside the current uninterrupted enabled interval qualifies: activity before
consent, while withdrawn or under the operator override, and legacy operations without an ID never do.
The shared status file is written by the host but mounted writable into this container; it is read only to
reserve a report, never as authority for host state.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.install_metrics import outbox
from app.services.install_metrics import payload as allowlist
from app.services.install_metrics.state import _locked, override_active, setup_finished, utcnow

logger = logging.getLogger("bioaf.install_metrics")

STATUS_FILE = "current.json"
MAX_STATUS_BYTES = 1024 * 1024
# A completion older than a report may wait is not reconciled into one.
RECONCILE_WINDOW = timedelta(days=7)


class InvalidOperation(ValueError):
    """Operation facts that cannot be recorded. The message names the field, never its value."""


def _moment(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidOperation(field)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidOperation(field) from None
    if moment.tzinfo is None:
        raise InvalidOperation(field)
    return moment


@dataclass(frozen=True)
class UpgradeOperation:
    operation_id: str
    from_version: str
    to_version: str
    started_at: datetime
    completed_at: datetime

    @classmethod
    def parse(
        cls, *, operation_id: object, from_version: object, to_version: object, started_at: object, completed_at: object
    ) -> UpgradeOperation:
        try:
            canonical = str(uuid.UUID(str(operation_id)))
        except ValueError:
            raise InvalidOperation("operation_id") from None
        if not isinstance(operation_id, str) or canonical != operation_id.lower():
            raise InvalidOperation("operation_id")
        if not isinstance(to_version, str) or not to_version:
            raise InvalidOperation("to_version")
        started = _moment(started_at, "started_at")
        completed = _moment(completed_at, "completed_at")
        if completed < started:
            raise InvalidOperation("completed_at")
        return cls(
            operation_id=canonical,
            from_version=allowlist.official_version(from_version),
            to_version=allowlist.official_version(to_version),
            started_at=started,
            completed_at=completed,
        )


async def record(session: AsyncSession, operation: UpgradeOperation, now: datetime | None = None) -> str:
    """Reserve the operation's report if it qualifies (the caller commits); a safe outcome code."""
    state = await _locked(session)
    if not state.effective(override_active()) or state.installation_id is None:
        outcome = "not_reporting"
    elif not await setup_finished(session, state):
        outcome = "setup_incomplete"
    elif state.enabled_since is None or operation.started_at < state.enabled_since:
        outcome = "before_interval"
    elif await outbox.pending_upgrades(session) >= outbox.MAX_PENDING_UPGRADES:
        outcome = "backlog_full"
    else:
        reserved = await outbox.reserve(
            session,
            source_key=outbox.upgrade_source(state.installation_id, operation.operation_id),
            name=allowlist.UPGRADE_COMPLETED,
            installation_id=state.installation_id,
            moment=operation.completed_at,
            bioaf_version=operation.to_version,
            from_version=operation.from_version,
        )
        outcome = "reserved" if reserved else "duplicate"
    logger.info("install_metrics upgrade=%s", outcome)
    return outcome


def _read_status(directory: str) -> dict | None:
    """The shared status record, read without following a link; None when absent or unusable."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        fd = os.open(STATUS_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        os.close(dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATUS_BYTES:
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = json.loads(handle.read(MAX_STATUS_BYTES + 1).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    return data if isinstance(data, dict) else None


async def reconcile_from_status(session: AsyncSession, now: datetime | None = None) -> str | None:
    """Reserve the latest completed operation from the shared update status, if the CLI missed it."""
    from app.config import settings

    data = _read_status(str(Path(settings.update_status_dir)))
    if data is None or data.get("status") != "completed" or "operation_id" not in data:
        return None
    try:
        operation = UpgradeOperation.parse(
            operation_id=data.get("operation_id"),
            from_version=data.get("from_version"),
            to_version=data.get("to_version"),
            started_at=data.get("operation_started_at"),
            completed_at=data.get("operation_completed_at"),
        )
    except InvalidOperation:
        return None
    if (now or utcnow()) - operation.completed_at > RECONCILE_WINDOW:
        return None
    return await record(session, operation, now)
