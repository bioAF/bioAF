"""Whether the running backend may be replaced without losing a Work Node's sync key (plan 03A).

Work Nodes launched before durable credentials (``node_trust_version`` NULL) hold their backend
sync key only in the memory of the backend process that launched them. Replacing that process
while such a node may still exist loses the only way to save its outputs.

This runs from the *target* release's image against the *installed* schema, before any migration,
so it reads with raw SQL and does not assume the new columns exist. It reconciles database records
with an authenticated provider inventory:

- a legacy record in an active status whose instance is still listed, or that has no instance yet
  (an in-flight launch), is unsafe;
- any listed instance whose session is not a durable record (legacy, or no record at all) is
  unsafe, whatever its database status says;
- a legacy record whose instance the provider no longer lists is confirmed gone and never blocks.

An unavailable inventory raises; it is never treated as "nothing is running". Nothing here stops,
changes or contacts a node.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Statuses in which a node may still exist or be about to exist.
_ACTIVE = ("pending", "starting", "running", "stopping", "stop_failed")


class InventoryUnavailable(Exception):
    """The provider could not list this installation's Work Node instances."""


@dataclass(frozen=True)
class ManagedInstance:
    """One existing (not terminated/deleted) instance labelled as a bioAF Work Node."""

    name: str
    session_id: str | None
    zone: str | None = None
    provider_instance_id: str | None = None
    launch_attempt_id: str | None = None


@dataclass(frozen=True)
class Readiness:
    ready: bool
    unsafe_count: int = 0


async def _has_trust_column(session: AsyncSession) -> bool:
    found = await session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'compute_sessions' "
            "AND column_name = 'node_trust_version'"
        )
    )
    return found.scalar() is not None


async def assess_update_readiness(
    session: AsyncSession,
    inventory: Callable[[], Awaitable[list[ManagedInstance]]],
) -> Readiness:
    trust = "node_trust_version" if await _has_trust_column(session) else "NULL"
    rows = (
        await session.execute(
            text(
                f"SELECT id, status, gce_instance_name, {trust} AS trust "
                "FROM compute_sessions WHERE session_type = 'ssh'"
            )
        )
    ).all()
    if not rows:
        # No Work Node was ever launched, so no backend holds a node key.
        return Readiness(ready=True)

    instances = await inventory()
    live_names = {i.name for i in instances}
    durable = {str(r.id) for r in rows if r.trust is not None and r.trust >= 1}

    unsafe: set[str] = set()
    for r in rows:
        if str(r.id) in durable or r.status not in _ACTIVE:
            continue
        if not r.gce_instance_name or r.gce_instance_name in live_names:
            unsafe.add(f"session:{r.id}")
    for i in instances:
        if i.session_id not in durable:
            unsafe.add(f"session:{i.session_id}" if i.session_id else f"instance:{i.name}")
    return Readiness(ready=not unsafe, unsafe_count=len(unsafe))
