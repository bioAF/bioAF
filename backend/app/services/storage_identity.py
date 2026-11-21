"""Store health and identity evidence for the installation's storage containers.

Three facts are kept apart:

- the *recorded* bucket name (platform_config), which Terraform outputs alone never change;
- the *observed* container, read through the storage adapter (``inspect_container``);
- the *established identity* (``StorageIdentity``), which says this bucket incarnation is the one
  bioAF deployed.

Identity is established in two ways. Discovery records it when provider evidence and recorded
deployment unambiguously agree: the bucket carries bioAF's labels, its name carries the recorded
storage generation, and the provider's creation time falls inside a completed storage apply of that
generation. Otherwise an administrator can enroll what was observed, which records an audited
baseline as of that day, never proof of history. A known mismatch (another project, another account,
a bucket recreated after it was recorded) is never overridden.

Inspection is read-only: it never creates, mounts, adopts an ambiguous bucket, or rewrites
configuration. A store that is present but not confirmed stays usable for ordinary reads and writes;
only automatic repair and destruction require an established identity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import ContainerObservation, ContainerPresence
from app.exceptions import StateError, ValidationError
from app.models.component import TerraformRun
from app.models.storage_identity import StorageIdentity, StorageStoreCheck
from app.platform.platform_config_service import PlatformConfigService
from app.services.audit_service import log_action
from app.services.deployment_outputs import identifier_value

logger = logging.getLogger("bioaf.storage_identity")

# Stores in display order, each with the platform_config key naming its bucket.
STORES: tuple[str, ...] = ("ingest", "raw", "working", "results", "references", "literature", "config_backups")

# How far a provider's creation time may sit outside a recorded apply's start/finish.
_WINDOW_SLACK = timedelta(minutes=2)
# An apply without a completion time is credited this long after it started.
_OPEN_RUN_SPAN = timedelta(hours=2)
_CREATED_TOLERANCE = timedelta(seconds=1)


class StoreHealth(str, Enum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    NO_ACCESS = "no_access"
    NOT_RESPONDING = "not_responding"
    NOT_CONFIRMED = "not_confirmed"
    WRONG_OWNER = "wrong_owner"
    NOT_MOUNTED = "not_mounted"
    CANNOT_CHECK = "cannot_check"


_PRESENCE_HEALTH = {
    ContainerPresence.MISSING: StoreHealth.NOT_FOUND,
    ContainerPresence.FORBIDDEN: StoreHealth.NO_ACCESS,
    ContainerPresence.UNAVAILABLE: StoreHealth.NOT_RESPONDING,
    ContainerPresence.NOT_MOUNTED: StoreHealth.NOT_MOUNTED,
    ContainerPresence.UNSUPPORTED: StoreHealth.CANNOT_CHECK,
}


@dataclass(frozen=True)
class StoreReport:
    store: str
    bucket_name: str
    health: StoreHealth
    basis: str | None = None
    checked_at: datetime | None = None
    observed_owner: str | None = None
    observed_created_at: datetime | None = None

    @property
    def trusted(self) -> bool:
        """Established as this installation's: automatic repair and destruction may act on it."""
        return self.health is StoreHealth.AVAILABLE

    @property
    def usable(self) -> bool:
        """Present and not known to be someone else's: ordinary reads and writes continue."""
        return self.health in (StoreHealth.AVAILABLE, StoreHealth.NOT_CONFIRMED)


def _adapter():
    from app.adapters.registry import get_storage_adapter

    return get_storage_adapter()


async def _configured_buckets(session: AsyncSession) -> dict[str, str]:
    adapter = _adapter()
    if not getattr(adapter, "named_containers", True):
        # A mounted filesystem names each store's directory after the store itself.
        return {store: store for store in STORES}
    values = await PlatformConfigService.get_many(session, [f"{s}_bucket_name" for s in STORES])
    out: dict[str, str] = {}
    for store in STORES:
        name = identifier_value(values.get(f"{store}_bucket_name"))
        if name:
            out[store] = name
    return out


def _generation_from_name(name: str) -> str:
    tail = name.rsplit("-", 1)[-1] if "-" in name else ""
    return tail if len(tail) == 6 and all(c in "0123456789abcdef" for c in tail) else ""


async def _active_identity(session: AsyncSession, bucket_name: str) -> StorageIdentity | None:
    return (
        await session.execute(
            select(StorageIdentity).where(
                StorageIdentity.bucket_name == bucket_name, StorageIdentity.retired_at.is_(None)
            )
        )
    ).scalar_one_or_none()


async def _installation_owner(session: AsyncSession) -> str | None:
    """The owner recorded for any established bucket: every store shares one project/account."""
    return (
        await session.execute(
            select(StorageIdentity.owner)
            .where(StorageIdentity.retired_at.is_(None), StorageIdentity.owner.is_not(None))
            .limit(1)
        )
    ).scalar_one_or_none()


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= _CREATED_TOLERANCE


async def _created_by_recorded_apply(session: AsyncSession, generation: str, created_at: datetime) -> bool:
    runs = (
        await session.execute(
            select(TerraformRun).where(
                TerraformRun.module_name == "storage",
                TerraformRun.action == "apply",
                TerraformRun.status == "completed",
            )
        )
    ).scalars()
    for run in runs:
        if identifier_value(str((run.tfvars_json or {}).get("stack_uid") or "")) != generation:
            continue
        started = run.started_at
        if started is None:
            continue
        finished = run.completed_at or started + _OPEN_RUN_SPAN
        if started - _WINDOW_SLACK <= created_at <= finished + _WINDOW_SLACK:
            return True
    return False


async def _discoverable(session: AsyncSession, bucket_name: str, seen: ContainerObservation) -> str | None:
    """The generation when provider evidence and recorded deployment agree, else None."""
    if seen.created_at is None or seen.owner in (None, "other-account") or "labels" in seen.limits:
        return None
    if seen.labels.get("managed_by") != "bioaf":
        return None
    generation = identifier_value(await PlatformConfigService.get(session, "storage_stack_uid"))
    generation = generation or _generation_from_name(bucket_name)
    if not generation or not bucket_name.endswith(f"-{generation}"):
        return None
    label_generation = seen.labels.get("storage_uid")
    if label_generation is not None and label_generation != generation:
        return None
    installation_owner = await _installation_owner(session)
    if installation_owner is not None and installation_owner != seen.owner:
        return None
    if not await _created_by_recorded_apply(session, generation, seen.created_at):
        return None
    return generation


async def _classify(session: AsyncSession, store: str, bucket_name: str, seen: ContainerObservation) -> StoreReport:
    if seen.presence is not ContainerPresence.PRESENT:
        return StoreReport(store, bucket_name, _PRESENCE_HEALTH[seen.presence])
    if seen.simulated or "identity" in seen.limits:
        return StoreReport(store, bucket_name, StoreHealth.AVAILABLE)
    if seen.owner == "other-account":
        return StoreReport(store, bucket_name, StoreHealth.WRONG_OWNER)

    recorded = await _active_identity(session, bucket_name)
    if recorded is not None:
        owner_matches = recorded.owner is None or seen.owner is None or recorded.owner == seen.owner
        if not owner_matches or not _same_instant(recorded.provider_created_at, seen.created_at):
            return StoreReport(store, bucket_name, StoreHealth.WRONG_OWNER, recorded.basis)
        return StoreReport(store, bucket_name, StoreHealth.AVAILABLE, recorded.basis)

    installation_owner = await _installation_owner(session)
    if installation_owner is not None and seen.owner is not None and installation_owner != seen.owner:
        return StoreReport(store, bucket_name, StoreHealth.WRONG_OWNER)

    generation = await _discoverable(session, bucket_name, seen)
    if generation is None:
        return StoreReport(store, bucket_name, StoreHealth.NOT_CONFIRMED)
    session.add(
        StorageIdentity(
            store=store,
            bucket_name=bucket_name,
            storage_uid=generation,
            owner=seen.owner,
            provider_created_at=seen.created_at,
            basis="discovered",
        )
    )
    await session.flush()
    logger.info("Recorded storage identity for store %s from agreeing deployment evidence", store)
    return StoreReport(store, bucket_name, StoreHealth.AVAILABLE, "discovered")


async def inspect_bucket(session: AsyncSession, store: str, bucket_name: str) -> StoreReport:
    """Inspect one bucket for ``store`` without recording a health check."""
    adapter = _adapter()
    seen = await adapter.inspect_container(adapter.build_uri(bucket_name, ""))
    return _with_observation(await _classify(session, store, bucket_name, seen), seen)


def _with_observation(report: StoreReport, seen: ContainerObservation) -> StoreReport:
    return replace(report, observed_owner=seen.owner, observed_created_at=seen.created_at)


async def _record_check(session: AsyncSession, report: StoreReport) -> StoreReport:
    checked_at = datetime.now(timezone.utc)
    values = {
        "store": report.store,
        "bucket_name": report.bucket_name,
        "health": report.health.value,
        "basis": report.basis,
        "observed_owner": report.observed_owner,
        "observed_created_at": report.observed_created_at,
        "checked_at": checked_at,
    }
    stmt = insert(StorageStoreCheck).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(index_elements=[StorageStoreCheck.store], set_={k: stmt.excluded[k] for k in values})
    )
    return replace(report, checked_at=checked_at)


async def inspect_stores(session: AsyncSession, stores: list[str] | None = None) -> list[StoreReport]:
    """Inspect every configured store (or those named) and record the results; the caller commits."""
    configured = await _configured_buckets(session)
    reports: list[StoreReport] = []
    for store, bucket_name in configured.items():
        if stores is not None and store not in stores:
            continue
        report = await inspect_bucket(session, store, bucket_name)
        reports.append(await _record_check(session, report))
    await session.flush()
    return reports


async def configured_store_count(session: AsyncSession) -> int:
    return len(await _configured_buckets(session))


async def latest_reports(session: AsyncSession) -> list[StoreReport]:
    """The last recorded inspection per configured store, in store order."""
    rows = {row.store: row for row in (await session.execute(select(StorageStoreCheck))).scalars()}
    configured = await _configured_buckets(session)
    out: list[StoreReport] = []
    for store, bucket_name in configured.items():
        row = rows.get(store)
        if row is None or row.bucket_name != bucket_name:
            continue
        out.append(
            StoreReport(
                store,
                bucket_name,
                StoreHealth(row.health),
                row.basis,
                row.checked_at,
                row.observed_owner,
                row.observed_created_at,
            )
        )
    return out


async def enroll_stores(session: AsyncSession, user_id: int, stores: list[str], *, reason: str) -> list[StoreReport]:
    """Record an administrator's acknowledgment of the observed buckets as this installation's.

    Refused for a bucket that is not observed, or that bioAF knows belongs to another installation.
    The caller commits.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to confirm storage buckets.")
    configured = await _configured_buckets(session)
    unknown = [s for s in stores if s not in configured]
    if unknown or not stores:
        raise ValidationError("Choose configured storage buckets to confirm.")

    adapter = _adapter()
    reports: list[StoreReport] = []
    for store in stores:
        bucket_name = configured[store]
        seen = await adapter.inspect_container(adapter.build_uri(bucket_name, ""))
        report = await _classify(session, store, bucket_name, seen)
        if report.health is StoreHealth.WRONG_OWNER:
            raise StateError(f"{bucket_name} belongs to another installation, so it cannot be confirmed.")
        if seen.presence is not ContainerPresence.PRESENT:
            raise StateError(f"{bucket_name} could not be observed, so it cannot be confirmed.")
        if report.health is StoreHealth.NOT_CONFIRMED:
            generation = identifier_value(await PlatformConfigService.get(session, "storage_stack_uid"))
            session.add(
                StorageIdentity(
                    store=store,
                    bucket_name=bucket_name,
                    storage_uid=generation or _generation_from_name(bucket_name) or None,
                    owner=seen.owner,
                    provider_created_at=seen.created_at,
                    basis="enrolled",
                    reason=reason,
                    recorded_by_user_id=user_id,
                )
            )
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="infrastructure",
                entity_id=0,
                action="storage_identity_enrolled",
                details={
                    "store": store,
                    "bucket_name": bucket_name,
                    "owner": seen.owner,
                    "provider_created_at": seen.created_at.isoformat() if seen.created_at else None,
                    "reason": reason,
                },
            )
            report = StoreReport(store, bucket_name, StoreHealth.AVAILABLE, "enrolled")
        reports.append(await _record_check(session, _with_observation(report, seen)))
    await session.flush()
    return reports


async def retire_identities(session: AsyncSession) -> None:
    """Retire every established identity after a confirmed storage destroy. The caller commits."""
    await session.execute(
        update(StorageIdentity)
        .where(StorageIdentity.retired_at.is_(None))
        .values(retired_at=datetime.now(timezone.utc))
    )
    await session.execute(StorageStoreCheck.__table__.delete())
