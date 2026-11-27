"""Work Node reports (plan 03.5D): the backend reads each node's liveness/policy report over the
verified SSH route Stop uses, instead of waiting for a node-to-backend callback.

One round (run by the existing ``work-node-heartbeat`` periodic job) selects eligible nodes, least
recently attempted first, and for each:

1. claims it with a conditional UPDATE (the Stop-claim pattern) so two workers never contact the
   same node within ``REPORT_INTERVAL``;
2. asks the provider adapter for a report, with no database session or row lock held;
3. locks and reloads the row and patches only the reporting fields, and only while the node is
   still eligible under the same launch attempt.

``heartbeat_at`` moves only for an authenticated, well-formed reply, to the backend's receipt time.
A failed attempt records ``report_failure`` and leaves ``heartbeat_at`` alone. Nothing here stops a
node: missing reports are not evidence of idleness (owner decision 2026-11-25).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from app import runtime
from app.adapters.registry import get_work_node_adapter
from app.adapters.work_nodes.node_ssh import REPORT_FAILURES, REPORT_REPORTED, REPORT_UNAVAILABLE, REPORT_UNREACHABLE
from app.models.notebook_session import ComputeSession

logger = logging.getLogger("bioaf.work_node_reports")

REPORT_INTERVAL = timedelta(minutes=5)
REPORT_STALE_AFTER = timedelta(minutes=15)
REPORT_CONCURRENCY = 8
NODE_DEADLINE_SECONDS = 15
ROUND_BUDGET_SECONDS = 120

# Running nodes, and nodes a failed Stop kept running.
ELIGIBLE_STATUSES = ("running", "stop_failed")
KNOWN_POLICY_STATES = ("applied", "failed", "failed_closed")


@dataclass(frozen=True)
class _Target:
    id: int
    instance_name: str
    zone: str
    gcp_project_id: str
    launch_attempt_id: str | None
    provider_instance_id: str | None
    sync_route: str | None
    sync_private_key: str
    host_public_key: str


def _eligible():
    return (
        ComputeSession.session_type == "ssh",
        ComputeSession.status.in_(ELIGIBLE_STATUSES),
        ComputeSession.node_trust_version.is_not(None),
        ComputeSession.sync_private_key.is_not(None),
        ComputeSession.host_public_key.is_not(None),
        ComputeSession.gce_instance_name.is_not(None),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def report_status(node, now: datetime) -> str | None:
    """How a node's reporting stands, for display: current | stale | unreachable | awaiting | unavailable.

    None for nodes that are not running (or kept running by a failed Stop).
    """
    if node.status not in ELIGIBLE_STATUSES:
        return None
    if node.node_trust_version is None:
        return "unavailable"
    last = _aware(node.heartbeat_at)
    if last is not None and last >= now - REPORT_STALE_AFTER:
        return "current"
    if node.report_failure:
        return "unreachable"
    return "stale" if last is not None else "awaiting"


async def _candidates(factory, now: datetime) -> list[_Target]:
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(ComputeSession)
                    .where(
                        *_eligible(),
                        or_(
                            ComputeSession.report_attempted_at.is_(None),
                            ComputeSession.report_attempted_at < now - REPORT_INTERVAL,
                        ),
                    )
                    .order_by(ComputeSession.report_attempted_at.asc().nulls_first(), ComputeSession.id)
                )
            )
            .scalars()
            .all()
        )
        return [
            _Target(
                id=r.id,
                instance_name=r.gce_instance_name,
                zone=r.gce_zone or "",
                gcp_project_id=r.gce_project_id or "",
                launch_attempt_id=r.launch_attempt_id,
                provider_instance_id=r.provider_instance_id,
                sync_route=r.sync_route,
                sync_private_key=r.sync_private_key,
                host_public_key=r.host_public_key,
            )
            for r in rows
        ]


def _same_launch(target: _Target):
    if target.launch_attempt_id is None:
        return ComputeSession.launch_attempt_id.is_(None)
    return ComputeSession.launch_attempt_id == target.launch_attempt_id


async def _claim(factory, target: _Target, now: datetime) -> bool:
    async with factory() as session:
        claimed = await session.execute(
            update(ComputeSession)
            .where(
                ComputeSession.id == target.id,
                *_eligible(),
                _same_launch(target),
                or_(
                    ComputeSession.report_attempted_at.is_(None),
                    ComputeSession.report_attempted_at < now - REPORT_INTERVAL,
                ),
            )
            .values(report_attempted_at=now)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return bool(claimed.rowcount)


async def _complete(factory, target: _Target, outcome: str, policy: str | None, received_at: datetime) -> None:
    async with factory() as session:
        node = (
            await session.execute(
                select(ComputeSession).where(ComputeSession.id == target.id, _same_launch(target)).with_for_update()
            )
        ).scalar_one_or_none()
        if node is None or node.status not in ELIGIBLE_STATUSES:
            # Stopped, failed or replaced while the report was in flight: a late reply changes nothing.
            await session.rollback()
            return
        if outcome == REPORT_REPORTED:
            node.heartbeat_at = received_at
            node.report_failure = None
            if policy in KNOWN_POLICY_STATES and (node.provider_metadata or {}).get("ssh_policy_status") != policy:
                node.provider_metadata = {**(node.provider_metadata or {}), "ssh_policy_status": policy}
        else:
            node.report_failure = outcome if outcome in REPORT_FAILURES else REPORT_UNAVAILABLE
        await session.commit()


async def _report_one(factory, adapter, target: _Target, now: datetime, node_deadline: float, claimed: set[int]):
    if not await _claim(factory, target, now):
        return
    claimed.add(target.id)
    policy = None
    try:
        result = await asyncio.wait_for(
            adapter.report_vm(
                target.instance_name,
                target.zone,
                gcp_project_id=target.gcp_project_id,
                session_id=target.id,
                sync_private_key=target.sync_private_key,
                host_public_key=target.host_public_key,
                provider_instance_id=target.provider_instance_id,
                launch_attempt_id=target.launch_attempt_id,
                sync_route=target.sync_route,
                timeout=node_deadline,
            ),
            timeout=node_deadline,
        )
        outcome, policy = result.outcome, result.ssh_policy_status
    except asyncio.TimeoutError:
        outcome = REPORT_UNREACHABLE
    except Exception as exc:
        logger.warning("Work node %d report raised %s", target.id, type(exc).__name__)
        outcome = REPORT_UNAVAILABLE
    await _complete(factory, target, outcome, policy, datetime.now(timezone.utc))
    claimed.discard(target.id)


async def report_round(
    factory,
    *,
    concurrency: int = REPORT_CONCURRENCY,
    node_deadline: float = NODE_DEADLINE_SECONDS,
    round_budget: float = ROUND_BUDGET_SECONDS,
) -> int:
    """One bounded pass over eligible nodes. Returns the number of nodes attempted."""
    now = datetime.now(timezone.utc)
    targets = await _candidates(factory, now)
    if not targets:
        return 0
    adapter = get_work_node_adapter()
    gate = asyncio.Semaphore(concurrency)
    in_flight: set[int] = set()

    async def bounded(target: _Target):
        async with gate:
            await _report_one(factory, adapter, target, now, node_deadline, in_flight)

    # Owned by the bound task owner (shutdown cancels and awaits them), like other fan-out work.
    tasks = [runtime.spawn(bounded(t), role="work-node-report") for t in targets]
    done, pending = await asyncio.wait(tasks, timeout=round_budget)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Claimed but cut off by the round budget: the attempt is recorded as a failure.
    for target in targets:
        if target.id in in_flight:
            await _complete(factory, target, REPORT_UNREACHABLE, None, datetime.now(timezone.utc))
    for task in done:
        if task.exception() is not None:
            logger.warning("Work node report task failed: %s", type(task.exception()).__name__)
    return len(targets) - len(pending)
