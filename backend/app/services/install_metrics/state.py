"""The installation-wide metrics choice and the policy that reserves reports (ADR-075).

``InstallMetricsService`` is the only writer of ``platform_config.install_metrics_state``. Every write
locks that row; a choice made against a stale revision is refused, so two prompts cannot overwrite the
first committed decision. The Installation ID is created on the first opt-in and never rotated.

Reporting is *effective* while the admin's choice is enabled and the operator override
(``BIOAF_INSTALL_METRICS_DISABLED``) is off. ``enabled_since`` is the start of the current
uninterrupted effective interval: it resets when either side disables or re-enables reporting, never
on an ordinary restart. Leaving the interval cancels every pending report.

The consent evidence (choice, notice version, decision time, revision) stays here, in the
installation's own database. It is never part of a report.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.platform_config_service import PlatformConfigService
from app.services.install_metrics import outbox
from app.services.install_metrics import payload as allowlist

logger = logging.getLogger("bioaf.install_metrics")

STATE_KEY = "install_metrics_state"
STATE_SCHEMA = 1
# The version of the disclosure an admin chooses against. A new version alone does not re-open the
# prompt; materially different collection needs its own consent design.
NOTICE_VERSION = "2026-11-15"
CHOICES = ("enabled", "disabled")

Preference = Literal["pending", "enabled", "disabled"]
Classification = Literal["new", "existing"]


class InstallMetricsError(Exception):
    """A refused metrics state change. The message is a safe code, never a supplied value."""


class InvalidChoice(InstallMetricsError):
    pass


class NoticeVersionMismatch(InstallMetricsError):
    pass


class RevisionConflict(InstallMetricsError):
    pass


class StateUnavailable(InstallMetricsError):
    """The stored state cannot be read; it is never replaced silently."""


@dataclass(frozen=True)
class InstallMetricsState:
    installation_id: str | None = None
    classification: Classification = "new"
    preference: Preference = "pending"
    notice_version: str | None = None
    decided_at: datetime | None = None
    enabled_since: datetime | None = None
    override_active: bool = False
    setup_completed: bool = False
    # The event that reserved this installation's one baseline, or None.
    baseline: str | None = None
    revision: int = 0

    def to_json(self) -> str:
        data = asdict(self)
        for key in ("decided_at", "enabled_since"):
            data[key] = data[key].isoformat() if data[key] is not None else None
        return json.dumps({"schema": STATE_SCHEMA, **data}, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> InstallMetricsState:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.pop("schema", None) != STATE_SCHEMA:
            raise ValueError("schema")
        for key in ("decided_at", "enabled_since"):
            if data.get(key) is not None:
                data[key] = datetime.fromisoformat(data[key])
        state = cls(**data)
        if state.preference not in ("pending", *CHOICES) or state.classification not in ("new", "existing"):
            raise ValueError("value")
        if state.installation_id is not None:
            uuid.UUID(state.installation_id)
        return state

    def effective(self, override: bool) -> bool:
        return self.preference == "enabled" and not override


def override_active() -> bool:
    """The operator override, read from settings on every decision."""
    from app.config import settings

    return bool(settings.install_metrics_disabled)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def current_version() -> str:
    from app.config import settings

    return settings.app_version


async def setup_complete(session: AsyncSession) -> bool:
    row = (await session.execute(text("SELECT 1 FROM organizations WHERE setup_complete LIMIT 1"))).first()
    return row is not None


async def _initial(session: AsyncSession) -> InstallMetricsState:
    """State for a database the migration never classified (created from the models)."""
    complete = await setup_complete(session)
    return InstallMetricsState(classification="existing" if complete else "new", setup_completed=complete)


async def _locked(session: AsyncSession) -> InstallMetricsState:
    await PlatformConfigService.insert_if_absent(session, STATE_KEY, (await _initial(session)).to_json())
    raw = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = :k FOR UPDATE"), {"k": STATE_KEY})
    ).scalar_one()
    try:
        return InstallMetricsState.from_json(raw)
    except (ValueError, TypeError):
        logger.error("install_metrics state=unreadable")
        raise StateUnavailable("state_unreadable") from None


async def _write(session: AsyncSession, state: InstallMetricsState) -> None:
    await PlatformConfigService.set(session, STATE_KEY, state.to_json())


async def _reserve_baseline(
    session: AsyncSession, state: InstallMetricsState, now: datetime, name: str
) -> InstallMetricsState:
    """Reserve this installation's one baseline as ``name`` if it has none yet; the new state."""
    if state.baseline is not None or state.installation_id is None:
        return state
    await outbox.reserve(
        session,
        source_key=outbox.BASELINE_SOURCE,
        name=name,
        installation_id=state.installation_id,
        moment=now,
        bioaf_version=current_version(),
    )
    return replace(state, baseline=name)


class InstallMetricsService:
    @staticmethod
    async def read(session: AsyncSession) -> InstallMetricsState:
        """The stored state without locking; pending defaults when absent or unreadable."""
        raw = await PlatformConfigService.get(session, STATE_KEY)
        if raw is None:
            return await _initial(session)
        try:
            return InstallMetricsState.from_json(raw)
        except (ValueError, TypeError):
            logger.error("install_metrics state=unreadable")
            return InstallMetricsState()

    @staticmethod
    async def choose(
        session: AsyncSession,
        *,
        choice: object,
        notice_version: object,
        revision: object,
        now: datetime | None = None,
    ) -> InstallMetricsState:
        """Commit an admin's choice (the caller commits the transaction)."""
        if not isinstance(choice, str) or choice not in CHOICES:
            raise InvalidChoice("invalid_choice")
        if notice_version != NOTICE_VERSION:
            raise NoticeVersionMismatch("notice_version_mismatch")
        current = await _locked(session)
        if isinstance(revision, bool) or revision != current.revision:
            raise RevisionConflict("revision_conflict")
        now = now or utcnow()
        override = override_active()
        was_effective = current.effective(current.override_active)

        if choice == "enabled":
            state = replace(
                current,
                installation_id=current.installation_id or str(uuid.uuid4()),
                preference="enabled",
                override_active=override,
            )
        else:
            state = replace(current, preference="disabled", override_active=override)
        state = replace(state, notice_version=notice_version, decided_at=now, revision=current.revision + 1)
        state = await _transition(session, state, was_effective, now)
        await _write(session, state)
        logger.info("install_metrics choice=%s revision=%d", choice, state.revision)
        return state

    @staticmethod
    async def setup_completed(session: AsyncSession, now: datetime | None = None) -> InstallMetricsState:
        """Mark setup complete; reserve the baseline if reporting is effective. Idempotent."""
        current = await _locked(session)
        if current.setup_completed:
            return current
        now = now or utcnow()
        state = replace(current, setup_completed=True)
        if state.effective(override_active()):
            name = (
                allowlist.INSTALLATION_COMPLETED if state.classification == "new" else allowlist.INSTALLATION_OBSERVED
            )
            state = await _reserve_baseline(session, state, now, name)
        await _write(session, state)
        return state

    @staticmethod
    async def reconcile(session: AsyncSession, now: datetime | None = None) -> InstallMetricsState:
        """Apply a change of the operator override since the state was last written."""
        current = await _locked(session)
        override = override_active()
        if override == current.override_active:
            return current
        now = now or utcnow()
        was_effective = current.effective(current.override_active)
        state = await _transition(session, replace(current, override_active=override), was_effective, now)
        await _write(session, state)
        logger.info("install_metrics override=%s", "on" if override else "off")
        return state


async def _transition(
    session: AsyncSession, state: InstallMetricsState, was_effective: bool, now: datetime
) -> InstallMetricsState:
    """Start or end the effective interval when ``state`` changes whether reporting is effective."""
    effective = state.effective(state.override_active)
    if effective == was_effective:
        return state
    if not effective:
        await outbox.cancel_pending(session, now)
        return replace(state, enabled_since=None)
    state = replace(state, enabled_since=now)
    if state.setup_completed or await setup_complete(session):
        state = await _reserve_baseline(
            session, replace(state, setup_completed=True), now, allowlist.INSTALLATION_OBSERVED
        )
    return state
