"""The backend's periodic jobs: one iteration each, scheduled by app/tasks/periodic.py.

Each ``prepare`` binds what the old loop bound for its lifetime (most bound the session factory
current when the loop started; billing and idempotency cleanup resolve it per pass, as before) and
returns the iteration. Iterations keep their services' commit boundaries and log lines. Restore
coordination decides later whether workers follow a database swap; until then these
bindings are unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.tasks.periodic import Iteration, PeriodicJob

logger = logging.getLogger("bioaf")

SECOND = 1
MINUTE = 60
HOUR = 3600
DAY = 86400


def _factory():
    from app.database import async_session_factory

    return async_session_factory


def _with_session(run: Callable[..., Awaitable[object]]) -> Callable[[], Iteration]:
    """A job whose iteration opens one session from the factory bound at start and calls ``run``."""

    def prepare() -> Iteration:
        factory = _factory()

        async def iteration() -> None:
            async with factory() as session:
                await run(session)

        return iteration

    return prepare


# --- 30 s pollers --------------------------------------------------------------------------------


async def _pipeline_monitor(session) -> None:
    from app.services.pipeline_monitor_service import PipelineMonitorService

    await PipelineMonitorService.sync_run_statuses(session)


async def _auto_run_launch(session) -> None:
    from app.services.auto_run_service import AutoRunService

    processed = await AutoRunService.process_pending_runs(session)
    if processed:
        await session.commit()
        logger.info("Auto-run loop: processed %d pending runs", processed)


async def _validation_driver(session) -> None:
    """Advance active literature-validation studies; each study is isolated and committed on its own."""
    from app.services.validation_driver_service import ValidationDriverService

    advanced = await ValidationDriverService.advance_active_studies(session)
    if advanced:
        logger.info("Validation driver loop: advanced %d studies", advanced)


async def _notebook_image_build(session) -> None:
    from app.services.notebook_image_service import poll_image_build

    status = await poll_image_build(session)
    if status:
        await session.commit()


async def _cellxgene_image_build(session) -> None:
    from app.services.cellxgene_image_service import poll_image_build as poll_cellxgene_build

    status = await poll_cellxgene_build(session)
    if status:
        await session.commit()


async def _environment_build_poll(session) -> None:
    from app.services.environment_build_service import EnvironmentBuildService

    changed = await EnvironmentBuildService.poll_in_progress_builds(session)
    if changed:
        await session.commit()


# --- 60 s ------------------------------------------------------------------------------------------


def _slurm_enabled() -> bool:
    """SSH status polling only for an initialized SLURM stack that is not simulated.

    The stack is the one the registry initialized (restart-bound), never BIOAF_COMPUTE_MODE alone."""
    from app.adapters.registry import get_active_compute_stack
    from app.config import settings
    from app.settings_contract import is_simulated

    return get_active_compute_stack() == "slurm" and not is_simulated(settings.compute_mode)


async def _job_status_sync(session) -> None:
    from app.services.slurm_service import SlurmService

    await SlurmService.sync_job_statuses(session)


def _plot_archive_watcher() -> Iteration:
    """Scan the results bucket for new images; backfill metadata once per job lifetime."""
    from app.services.plot_archive_service import PlotArchiveService

    factory = _factory()
    state = {"backfilled": False}

    async def iteration() -> None:
        async with factory() as session:
            if not state["backfilled"]:
                await PlotArchiveService.backfill_metadata(session)
                state["backfilled"] = True
            await PlotArchiveService.scan_and_index(session)

    return iteration


def _scheduled_backup(kind: str, label: str) -> Callable[[], Iteration]:
    """Run the scheduled backup when its persisted due date says so, then advance it."""

    def prepare() -> Iteration:
        from app.services.backup_service import BackupService

        factory = _factory()

        async def iteration() -> None:
            async with factory() as session:
                if not await BackupService.is_backup_due(session, kind):
                    return
                if kind == "postgres":
                    result = await BackupService.run_postgres_backup(session, org_id=1)
                else:
                    result = await BackupService.run_config_backup(session, org_id=1)
                if result["status"] == "completed":
                    await BackupService.advance_next_run(session, kind)
                    await session.commit()
                    logger.info("Scheduled %s completed: %s", label, result.get("filename"))
                else:
                    logger.error("Scheduled %s failed: %s", label, result.get("message"))

        return iteration

    return prepare


def _lit_review_auto() -> Iteration:
    """When the automated AI Lit Review cadence is due, sweep experiments with new activity."""
    from app.services.literature import lit_review_auto_service

    factory = _factory()

    async def iteration() -> None:
        async with factory() as session:
            await lit_review_auto_service.ensure_next_run_seeded(session, org_id=1)
            due = await lit_review_auto_service.is_tick_due(session, org_id=1)
            await session.commit()
        if not due:
            return
        async with factory() as session:
            result = await lit_review_auto_service.run_due_sweep(session, org_id=1)
            await lit_review_auto_service.advance_next_run(session, org_id=1)
            await session.commit()
            logger.info(
                "Automated AI Lit Review tick: ran %d experiment(s)%s",
                len(result.get("ran", [])),
                f" (skipped: {result['skipped_reason']})" if result.get("skipped_reason") else "",
            )

    return iteration


async def _session_monitor(session) -> None:
    from app.services.session_monitor import SessionMonitorService

    await SessionMonitorService.poll_notebook_sessions(session)


async def _work_node_heartbeat(session) -> None:
    from app.services.work_node_service import WorkNodeService

    await WorkNodeService.check_heartbeat_timeouts(session)


# --- 5 min and hourly ------------------------------------------------------------------------------


async def _idle_session_check(session) -> None:
    from app.services.notebook_service import NotebookService

    await NotebookService.check_idle_sessions(session)


async def _quota_reset(session) -> None:
    from app.services.quota_service import QuotaService

    await QuotaService.reset_monthly_quotas(session)


async def _organizations(session) -> list:
    from sqlalchemy import select

    from app.models.organization import Organization

    result = await session.execute(select(Organization))
    return list(result.scalars().all())


async def _organization_ids(session) -> list[int]:
    """Ids, not instances: a rollback expires instances, and reading one afterwards would load."""
    return [org.id for org in await _organizations(session)]


async def _storage_stats_refresh(session) -> None:
    """Refresh storage stats per organization, committing each."""
    from app.services.storage_service import StorageService

    for org_id in await _organization_ids(session):
        try:
            await StorageService.refresh_storage_stats(session, org_id)
            await session.commit()
        except Exception as e:
            # A failed flush leaves the session unusable; the next organization needs a clean one.
            await session.rollback()
            logger.warning("Storage refresh failed for org %d: %s", org_id, e)


async def _backup_health_check(session) -> None:
    """Check backup health per organization; overdue backups emit events."""
    from app.services.backup_service import BackupService

    for org in await _organizations(session):
        try:
            await BackupService.check_backup_health(session, org.id)
        except Exception as e:
            logger.warning("Backup health check failed for org %d: %s", org.id, e)


async def _export_cleanup(session) -> None:
    """Retire signed-export objects past retention (app/services/data_export/retention.py)."""
    from app.services.data_export import retention

    await retention.run(session)


def _idempotency_cleanup() -> Iteration:
    """Sweep expired idempotency_keys rows (ADR-050); the factory is resolved per pass."""
    from app import database as database_module
    from app.services import idempotency_service

    async def iteration() -> None:
        async with database_module.async_session_factory() as session:
            deleted = await idempotency_service.cleanup_expired(session)
            if deleted:
                await session.commit()

    return iteration


# --- daily and 6-hourly ----------------------------------------------------------------------------


async def _notification_cleanup(session) -> None:
    """Delete read notifications older than 90 days."""
    from app.services.notification_service import NotificationService

    await NotificationService.cleanup_old_notifications(session)


async def _work_dir_reaper(session) -> None:
    """Delete abandoned runs' Nextflow work dirs.

    The shared work prefix reached 2.13 TB across five runs because nothing ever
    removed a failed run's intermediates. They are billed monthly and are useless
    two days after the run died.
    """
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.work_dir_reaper import WorkDirReaper

    raw_bucket = await PlatformConfigService.get(session, "raw_bucket_name")
    if raw_bucket:
        reaped = await WorkDirReaper.reap(session, raw_bucket=raw_bucket)
        await session.commit()
        if reaped:
            logger.info("Reaped work dirs for runs: %s", reaped)


async def _validation_fetch_reaper(session) -> None:
    """Free the downloaded data of stopped validation studies nobody retried.

    A study in `error` keeps its fetch so a retry can reuse it, and nothing ever deleted it: the
    fetch is a completed run, which no reaper covered. Past the retry window the objects and the
    rows that point at them go together, so a later retry returns to the approval gate rather than
    relaunching against files that are gone.
    """
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.validation_fetch_reaper import ValidationFetchReaper

    raw_bucket = await PlatformConfigService.get(session, "raw_bucket_name")
    if raw_bucket:
        reaped = await ValidationFetchReaper.reap(session, raw_bucket=raw_bucket)
        await session.commit()
        if reaped:
            logger.info("Reaped fetched data for validation studies: %s", reaped)


async def _version_check(session) -> None:
    from app.services.upgrade_service import UpgradeService

    for org in await _organizations(session):
        try:
            await UpgradeService.background_version_check(org.id)
        except Exception as e:
            logger.warning("Version check failed for org %d: %s", org.id, e)


async def _sdr_trigger(session) -> None:
    """Evaluate SDR re-assessment triggers (ADR-064); the logic lives in SdrService.evaluate_triggers."""
    from app.services.sdr_service import SdrService

    result = await SdrService.evaluate_triggers(session)
    await session.commit()
    if result["flagged"] or result["warned"]:
        logger.info("SDR trigger sweep: flagged %d, warned %d", result["flagged"], result["warned"])


async def _review_reminder(session) -> None:
    from app.tasks.review_reminder import check_unreviewed_runs

    await check_unreviewed_runs(session)


async def cost_billing_sync_once() -> None:
    """One pass of the billing sync: every org, committed per org."""
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.organization import Organization
    from app.services.billing_period import billing_period
    from app.services.cost_service import CostService

    # One billing period for the whole pass, so every org is synced and checked
    # against the same UTC usage month even if the pass runs past midnight.
    period = billing_period()
    async with async_session_factory() as session:
        result = await session.execute(select(Organization.id))
        # Ids, not instances: the rollback below expires instances, and reading one would load.
        for org_id in list(result.scalars().all()):
            try:
                report = await CostService.sync_billing_data(session, org_id, period)
                if report is not None and report.status != "imported":
                    logger.warning(
                        "Billing export import %s for org %d (usage %s to %s, end exclusive); existing cost records kept",
                        report.status,
                        org_id,
                        report.usage_start,
                        report.usage_end,
                    )
                await CostService.check_budget_thresholds(session, org_id, period)
                # Per org, so one org's failure cannot discard another's sync.
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.warning("Billing sync failed for org %d: %s", org_id, e)


def _nf_core_registry_refresh() -> Iteration:
    """Refresh the nf-core registry cache; failures preserve cached rows."""
    from app.services.nf_core_registry_service import NfCoreRegistryService

    factory = _factory()

    async def iteration() -> None:
        async with factory() as session:
            result = await NfCoreRegistryService.refresh_registry(session)
            await session.commit()
            if result.get("error"):
                logger.warning("nf-core registry refresh failed: %s", result["error"])
            else:
                logger.info(
                    "nf-core registry refresh: fetched=%d archived=%d",
                    result["fetched"],
                    result["archived"],
                )

    return iteration


def _job(role, prepare, first, interval, message, enabled=None) -> PeriodicJob:
    return PeriodicJob(
        role=role, prepare=prepare, first_delay=first, interval=interval, error_message=message, enabled=enabled
    )


# In the order the lifespan has always started them.
PERIODIC_JOBS: list[PeriodicJob] = [
    _job(
        "job-status-sync",
        _with_session(_job_status_sync),
        MINUTE,
        MINUTE,
        "Job status sync error: %s",
        enabled=_slurm_enabled,
    ),
    _job(
        "idle-session-check", _with_session(_idle_session_check), 5 * MINUTE, 5 * MINUTE, "Idle session check error: %s"
    ),
    _job("quota-reset", _with_session(_quota_reset), HOUR, HOUR, "Quota reset error: %s"),
    _job("pipeline-monitor", _with_session(_pipeline_monitor), 30, 30, "Pipeline monitor error: %s"),
    _job("plot-archive-watcher", _plot_archive_watcher, MINUTE, MINUTE, "Plot archive watcher error: %s"),
    _job("storage-stats-refresh", _with_session(_storage_stats_refresh), HOUR, HOUR, "Storage stats refresh error: %s"),
    _job("notification-cleanup", _with_session(_notification_cleanup), DAY, DAY, "Notification cleanup error: %s"),
    _job("work-dir-reaper", _with_session(_work_dir_reaper), DAY, DAY, "Work dir reaper error: %s"),
    _job(
        "validation-fetch-reaper",
        _with_session(_validation_fetch_reaper),
        DAY,
        DAY,
        "Validation fetch reaper error: %s",
    ),
    _job("backup-health-check", _with_session(_backup_health_check), HOUR, HOUR, "Backup health check error: %s"),
    _job("postgres-backup", _scheduled_backup("postgres", "pg_dump"), MINUTE, MINUTE, "Postgres backup loop error: %s"),
    _job(
        "config-backup",
        _scheduled_backup("config", "config backup"),
        MINUTE,
        MINUTE,
        "Config backup loop error: %s",
    ),
    _job("lit-review-auto", _lit_review_auto, MINUTE, MINUTE, "Lit review auto loop error: %s"),
    # Sync shortly after boot, then daily. Sleeping the full day first meant an
    # installation restarted more often than once a day never synced at all.
    _job("cost-billing-sync", lambda: cost_billing_sync_once, MINUTE, DAY, "Cost billing sync error: %s"),
    _job("version-check", _with_session(_version_check), DAY, DAY, "Version check error: %s"),
    _job("review-reminder", _with_session(_review_reminder), 6 * HOUR, 6 * HOUR, "Review reminder error: %s"),
    _job("auto-run-launch", _with_session(_auto_run_launch), 30, 30, "Auto-run launch loop error: %s"),
    _job("validation-driver", _with_session(_validation_driver), 30, 30, "Validation driver loop error: %s"),
    _job("session-monitor", _with_session(_session_monitor), MINUTE, MINUTE, "Session monitor error: %s"),
    _job(
        "notebook-image-build",
        _with_session(_notebook_image_build),
        30,
        30,
        "Notebook image build monitor error: %s",
    ),
    _job(
        "cellxgene-image-build",
        _with_session(_cellxgene_image_build),
        30,
        30,
        "Cellxgene image build monitor error: %s",
    ),
    _job(
        "environment-build-poll",
        _with_session(_environment_build_poll),
        30,
        30,
        "Environment build poll error: %s",
    ),
    _job(
        "work-node-heartbeat",
        _with_session(_work_node_heartbeat),
        MINUTE,
        MINUTE,
        "Work node heartbeat check error: %s",
    ),
    _job("export-cleanup", _with_session(_export_cleanup), HOUR, HOUR, "Export cleanup error: %s"),
    # Warm the cache 60 s after startup so the first browse modal is populated, then daily.
    _job(
        "nf-core-registry-refresh",
        _nf_core_registry_refresh,
        MINUTE,
        DAY,
        "nf-core registry refresh loop error: %s",
    ),
    _job("sdr-trigger", _with_session(_sdr_trigger), DAY, DAY, "SDR trigger loop error: %s"),
    _job("idempotency-cleanup", _idempotency_cleanup, HOUR, HOUR, "Idempotency cleanup error: %s"),
]
