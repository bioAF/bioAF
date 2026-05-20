"""Automated AI Lit Review cadence (single-org deployment).

A background loop in ``app.main`` calls this on a fixed interval. When the org
has ``lit_review_auto_enabled`` and the cadence is due, the sweep finds
experiments with new samples or pipeline runs since their last automated
(``trigger='scheduled'``) Lit Review Run, runs up to ``max_runs_per_tick`` of
them oldest-activity-first, then advances ``next_run`` by the cadence.

Cadence config (enabled / cadence / cap) lives on the ``organizations`` row,
edited via Settings > Integrations > LLMs. The ``next_run`` bookkeeping lives in
``platform_config`` (mirrors the backup loops). Everything else (excluding
library + dismissed + below-threshold papers) is unchanged from on-demand runs.

The leftover-over-the-cap experiments are not tracked explicitly: the due set is
recomputed from data each tick, so anything not run this tick stays due and is
picked up on the next one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.literature import TRIGGER_SCHEDULED, LiteratureReviewRun
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.role import Role
from app.models.sample import Sample
from app.models.user import User
from app.services import llm_provider_config_service
from app.services.literature import lit_review_run_service
from app.services.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.literature.lit_review_auto_service")

CADENCE_HOURS = {"daily": 24, "weekly": 168, "monthly": 720}
DEFAULT_CADENCE = "weekly"
VALID_CADENCES = tuple(CADENCE_HOURS.keys())
NEXT_RUN_KEY = "lit_review_auto_next_run"


def cadence_hours(cadence: str) -> int:
    return CADENCE_HOURS.get(cadence, CADENCE_HOURS[DEFAULT_CADENCE])


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Config + schedule bookkeeping
# ---------------------------------------------------------------------------


async def get_auto_config(session: AsyncSession, org_id: int) -> dict:
    row = (
        await session.execute(
            select(
                Organization.lit_review_auto_enabled,
                Organization.lit_review_auto_cadence,
                Organization.lit_review_max_runs_per_tick,
            ).where(Organization.id == org_id)
        )
    ).one_or_none()
    if row is None:
        return {"enabled": False, "cadence": DEFAULT_CADENCE, "max_runs_per_tick": 5}
    return {
        "enabled": bool(row[0]),
        "cadence": row[1] or DEFAULT_CADENCE,
        "max_runs_per_tick": int(row[2] or 5),
    }


async def _get_next_run(session: AsyncSession) -> datetime | None:
    raw = await PlatformConfigService.get(session, NEXT_RUN_KEY)
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(raw))
    except ValueError:
        return None


async def schedule_from_now(session: AsyncSession, org_id: int, *, now: datetime | None = None) -> None:
    """Set next_run one cadence out from now. Called when automation is enabled
    or its cadence changes, so the timer starts fresh and never fires the moment
    it is switched on."""
    cfg = await get_auto_config(session, org_id)
    now = now or datetime.now(UTC)
    next_run = now + timedelta(hours=cadence_hours(cfg["cadence"]))
    await PlatformConfigService.set(session, NEXT_RUN_KEY, next_run.isoformat())


async def clear_schedule(session: AsyncSession) -> None:
    await PlatformConfigService.set(session, NEXT_RUN_KEY, None)


async def ensure_next_run_seeded(session: AsyncSession, org_id: int, *, now: datetime | None = None) -> None:
    """Safety net for the loop: if automation is enabled but next_run was never
    set (e.g. enabled directly in the DB), seed it so it does not fire blindly."""
    cfg = await get_auto_config(session, org_id)
    if not cfg["enabled"]:
        return
    if await _get_next_run(session) is None:
        await schedule_from_now(session, org_id, now=now)


async def is_tick_due(session: AsyncSession, org_id: int, *, now: datetime | None = None) -> bool:
    cfg = await get_auto_config(session, org_id)
    if not cfg["enabled"]:
        return False
    next_run = await _get_next_run(session)
    if next_run is None:
        return False
    now = now or datetime.now(UTC)
    return now >= next_run


async def advance_next_run(session: AsyncSession, org_id: int, *, now: datetime | None = None) -> None:
    """Push next_run one cadence past now after a sweep. Advancing from *now*
    (not the prior next_run) bounds cost: a loop that was down for a long time
    runs one sweep, not a backlog storm."""
    cfg = await get_auto_config(session, org_id)
    now = now or datetime.now(UTC)
    next_run = now + timedelta(hours=cadence_hours(cfg["cadence"]))
    await PlatformConfigService.set(session, NEXT_RUN_KEY, next_run.isoformat())


# ---------------------------------------------------------------------------
# Due-set computation
# ---------------------------------------------------------------------------


async def due_experiments(session: AsyncSession, org_id: int) -> list[int]:
    """Experiment ids with new samples or pipeline runs since their last
    automated review, ordered oldest-activity-first. Experiments that have never
    had an automated review are due if they have any sample or run at all."""
    exp_ids = list(
        (
            await session.execute(select(Experiment.id).where(Experiment.organization_id == org_id))
        ).scalars().all()
    )
    if not exp_ids:
        return []

    last_scheduled = dict(
        (
            await session.execute(
                select(LiteratureReviewRun.experiment_id, func.max(LiteratureReviewRun.created_at))
                .where(
                    LiteratureReviewRun.experiment_id.in_(exp_ids),
                    LiteratureReviewRun.trigger == TRIGGER_SCHEDULED,
                )
                .group_by(LiteratureReviewRun.experiment_id)
            )
        ).all()
    )
    sample_activity = dict(
        (
            await session.execute(
                select(Sample.experiment_id, func.max(Sample.created_at))
                .where(Sample.experiment_id.in_(exp_ids))
                .group_by(Sample.experiment_id)
            )
        ).all()
    )
    run_activity = dict(
        (
            await session.execute(
                select(
                    PipelineRun.experiment_id,
                    func.max(func.coalesce(PipelineRun.completed_at, PipelineRun.created_at)),
                )
                .where(PipelineRun.experiment_id.in_(exp_ids))
                .group_by(PipelineRun.experiment_id)
            )
        ).all()
    )

    due: list[tuple[datetime, int]] = []
    for eid in exp_ids:
        activities = [
            _aware(sample_activity.get(eid)),
            _aware(run_activity.get(eid)),
        ]
        activities = [a for a in activities if a is not None]
        if not activities:
            continue
        latest = max(activities)
        last = _aware(last_scheduled.get(eid))
        if last is None or latest > last:
            due.append((latest, eid))

    due.sort(key=lambda x: (x[0], x[1]))
    return [eid for _, eid in due]


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


async def resolve_actor_user_id(session: AsyncSession, org_id: int) -> int | None:
    """Pick an active admin to attribute scheduled runs to (falls back to any
    active user). Resolved at sweep time so a later-deactivated admin does not
    break automation."""
    admin = (
        await session.execute(
            select(User.id)
            .join(Role, User.role_id == Role.id)
            .where(User.organization_id == org_id, User.status == "active", Role.name == "admin")
            .order_by(User.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if admin is not None:
        return admin
    return (
        await session.execute(
            select(User.id)
            .where(User.organization_id == org_id, User.status == "active")
            .order_by(User.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def run_due_sweep(session: AsyncSession, org_id: int, *, triggered_by_user_id: int | None = None) -> dict:
    """Run scheduled Lit Review Runs for up to max_runs_per_tick due experiments.

    Returns a summary dict. A single experiment failing is logged and isolated;
    the rest of the capped set still run.
    """
    cfg = await get_auto_config(session, org_id)
    if await llm_provider_config_service.get_active(session, org_id) is None:
        logger.info("Automated Lit Review skipped: no active LLM provider for org %s", org_id)
        return {"ran": [], "due": [], "skipped_reason": "no_active_llm_provider"}

    actor = triggered_by_user_id
    if actor is None:
        actor = await resolve_actor_user_id(session, org_id)
    if actor is None:
        logger.info("Automated Lit Review skipped: no active user to attribute runs to (org %s)", org_id)
        return {"ran": [], "due": [], "skipped_reason": "no_actor_user"}

    due = await due_experiments(session, org_id)
    cap = max(1, cfg["max_runs_per_tick"])
    selected = due[:cap]

    ran: list[int] = []
    for eid in selected:
        try:
            run = await lit_review_run_service.create_run(
                session,
                org_id=org_id,
                experiment_id=eid,
                triggered_by_user_id=actor,
                trigger=TRIGGER_SCHEDULED,
            )
            await session.commit()
            await lit_review_run_service._execute_run(run.id)
            ran.append(eid)
        except Exception:
            logger.exception("Automated Lit Review failed for experiment %s", eid)
            await session.rollback()

    return {"ran": ran, "due": due, "skipped_reason": None}
