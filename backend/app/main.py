import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app import runtime, startup
from app.config import settings
from app.database import engine
from app.logging_config import configure_logging
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.maintenance import MaintenanceMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.task_owner import TaskOwnerMiddleware
from app.runtime import TaskOwner

configure_logging(debug=settings.debug)
logger = logging.getLogger("bioaf")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ordered startup (app/startup.py), owned workers, and bounded shutdown.

    Everything startup installs is undone, in reverse, on a partial startup, normal exit, an
    exception through the lifespan, or cancellation.
    """
    logger.info("bioAF backend starting up (env=%s)", settings.environment)
    owner = TaskOwner()
    app.state.runtime = owner
    # Read the module's engine when the lifespan starts (tests substitute it).
    ctx = startup.StartupContext(engine=engine, stack=AsyncExitStack(), subscriptions=AsyncExitStack())
    # Unwound in reverse: owned work stops and settles, then this lifespan's subscriptions go, then
    # the engine is disposed. Resources later steps acquire are released before all three.
    async with ctx.stack:
        ctx.stack.push_async_callback(_dispose_engine, ctx.engine, owner)
        await ctx.stack.enter_async_context(ctx.subscriptions)
        ctx.stack.push_async_callback(_stop_owned_work, owner)
        with runtime.bound(owner):
            await startup.run_steps(startup.PRE_GATE_STEPS, ctx)

            # The upgrade gate (plan 9). Until the database is at this code's completed head, do none
            # of the normal work below: no startup writes, no background loops, and every API request
            # answers 503 (MaintenanceMiddleware). Diagnostics stay readable and a controlled retry
            # (`python -m app.schema_upgrade run`) is how it is lifted; the watcher then asks for a
            # restart.
            from app import schema_upgrade

            gate = await schema_upgrade.compatibility(ctx.engine)
            if not gate["compatible"]:
                app.state.maintenance = gate
                logger.error(
                    "Database not ready for this version (%s: at %s, need %s); serving maintenance responses only. Details: %s",
                    gate["reason"],
                    gate.get("revision"),
                    gate.get("target"),
                    schema_upgrade.status_path(),
                )
                watcher = asyncio.create_task(_maintenance_watch(app), name="bioaf-maintenance-watch")
                ctx.stack.push_async_callback(_cancel_and_wait, [watcher])
                yield
                return
            app.state.maintenance = None

            await startup.run_steps(startup.NORMAL_STEPS, ctx)
            logger.info("bioAF backend started successfully")
            await _start_workers(owner, ctx)
            logger.info("Background tasks started")
        yield


# Periodic jobs, in their existing start order, by role.
WORKERS: list[tuple[str, str]] = [
    ("job-status-sync", "_job_status_sync_loop"),
    ("idle-session-check", "_idle_session_check_loop"),
    ("quota-reset", "_quota_reset_loop"),
    ("pipeline-monitor", "_pipeline_monitor_loop"),
    ("plot-archive-watcher", "_plot_archive_watcher_loop"),
    ("storage-stats-refresh", "_storage_stats_refresh_loop"),
    ("notification-cleanup", "_notification_cleanup_loop"),
    ("work-dir-reaper", "_work_dir_reaper_loop"),
    ("validation-fetch-reaper", "_validation_fetch_reaper_loop"),
    ("backup-health-check", "_backup_health_check_loop"),
    ("postgres-backup", "_postgres_backup_loop"),
    ("config-backup", "_config_backup_loop"),
    ("lit-review-auto", "_lit_review_auto_loop"),
    ("cost-billing-sync", "_cost_billing_sync_loop"),
    ("version-check", "_version_check_loop"),
    ("review-reminder", "_review_reminder_loop"),
    ("auto-run-launch", "_auto_run_launch_loop"),
    ("validation-driver", "_validation_driver_loop"),
]
WORKERS_AFTER_LISTENER: list[tuple[str, str]] = [
    ("session-monitor", "_session_monitor_loop"),
    ("notebook-image-build", "_notebook_image_build_loop"),
    ("cellxgene-image-build", "_cellxgene_image_build_loop"),
    ("environment-build-poll", "_environment_build_poll_loop"),
    ("work-node-heartbeat", "_work_node_heartbeat_loop"),
    ("export-cleanup", "_export_cleanup_loop"),
    ("nf-core-registry-refresh", "_nf_core_registry_refresh_loop"),
    ("sdr-trigger", "_sdr_trigger_loop"),
]


async def _start_workers(owner: TaskOwner, ctx: startup.StartupContext) -> None:
    """Start the periodic jobs, the Pub/Sub listener, and the webhook worker, all owned."""
    from app.services.pubsub_listener import listener_manager

    module = globals()
    for role, name in WORKERS:
        owner.spawn(module[name](), role=role)
    await listener_manager().start()
    for role, name in WORKERS_AFTER_LISTENER:
        owner.spawn(module[name](), role=role)

    # LIMS integration (ADR-051): subscribe webhook dispatcher to internal
    # events, start the delivery worker, and add an idempotency-key cleanup
    # tick alongside the existing background loops.
    from app.services import webhook_dispatcher
    from app.services.webhook_worker import run_worker_loop as _webhook_run_worker_loop

    async def subscribe_webhooks(_ctx: startup.StartupContext) -> None:
        webhook_dispatcher.subscribe_all()

    await startup.run_steps([startup.Step("webhook_dispatcher", subscribe_webhooks)], ctx)
    owner.spawn(_webhook_run_worker_loop(), role="webhook-worker")
    owner.spawn(_idempotency_cleanup_loop(), role="idempotency-cleanup")


async def _stop_owned_work(owner: TaskOwner) -> None:
    """Stop producers at once, let active work settle, then cancel what remains (bounded)."""
    from app.services.pubsub_listener import stop_listener

    owner.close_admission()
    await startup.bounded(stop_listener(), startup.CANCEL_SECONDS, "pubsub_listener")
    # The periodic loops sleep between iterations; they are producers, so they stop now rather
    # than holding the drain open.
    workers = [t for t in owner.tasks() if t.get_name().removeprefix("bioaf:") in _WORKER_ROLES]
    await _cancel_and_wait(workers)
    await owner.shutdown(drain_seconds=startup.DRAIN_SECONDS, cancel_seconds=startup.CANCEL_SECONDS)


_WORKER_ROLES = {role for role, _ in WORKERS + WORKERS_AFTER_LISTENER} | {"webhook-worker", "idempotency-cleanup"}


async def _dispose_engine(engine_to_dispose, owner: TaskOwner) -> None:
    """Dispose the engine last, unless owned work is still running and may be using it."""
    if owner.report is not None and not owner.report.clean:
        logger.error("shutdown step=dispose_engine outcome=skipped reason=work_still_running")
    else:
        await startup.bounded(engine_to_dispose.dispose(), startup.RESOURCE_SECONDS, "dispose_engine")
    logger.info("bioAF backend shut down")


async def _cancel_and_wait(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=startup.CANCEL_SECONDS)


MAINTENANCE_RECHECK_SECONDS = 15.0


def _request_restart() -> None:
    """Exit so the container restarts into normal startup (Compose: restart: unless-stopped)."""
    import os
    import signal

    os.kill(os.getpid(), signal.SIGTERM)


async def _maintenance_watch(app: FastAPI) -> None:
    """While in maintenance, re-read the gate; once it opens, restart rather than start work mid-flight."""
    from app import schema_upgrade

    while True:
        await asyncio.sleep(MAINTENANCE_RECHECK_SECONDS)
        gate = await schema_upgrade.compatibility(engine)
        if gate["compatible"]:
            logger.info("Database upgrade completed; restarting to start normally")
            _request_restart()
            return
        app.state.maintenance = gate


async def _job_status_sync_loop():
    """Sync SLURM job statuses every 60 seconds. No-op on non-SLURM deployments."""
    from app.config import settings

    if settings.compute_mode != "slurm":
        logger.debug("Compute mode is %r, SLURM job sync disabled", settings.compute_mode)
        return

    from app.database import async_session_factory
    from app.services.slurm_service import SlurmService

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                await SlurmService.sync_job_statuses(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Job status sync error: %s", e)


async def _idle_session_check_loop():
    """Check for idle notebook sessions every 5 minutes."""
    from app.database import async_session_factory
    from app.services.notebook_service import NotebookService

    while True:
        try:
            await asyncio.sleep(300)
            async with async_session_factory() as session:
                await NotebookService.check_idle_sessions(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Idle session check error: %s", e)


async def _quota_reset_loop():
    """Check for monthly quota resets every hour."""
    from app.database import async_session_factory
    from app.services.quota_service import QuotaService

    while True:
        try:
            await asyncio.sleep(3600)
            async with async_session_factory() as session:
                await QuotaService.reset_monthly_quotas(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Quota reset error: %s", e)


async def _pipeline_monitor_loop():
    """Sync pipeline run statuses every 30 seconds."""
    from app.database import async_session_factory
    from app.services.pipeline_monitor_service import PipelineMonitorService

    while True:
        try:
            await asyncio.sleep(30)
            async with async_session_factory() as session:
                await PipelineMonitorService.sync_run_statuses(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Pipeline monitor error: %s", e)


async def _plot_archive_watcher_loop():
    """Scan results bucket for new image files every 60 seconds."""
    from app.database import async_session_factory
    from app.services.plot_archive_service import PlotArchiveService

    backfilled = False
    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                if not backfilled:
                    await PlotArchiveService.backfill_metadata(session)
                    backfilled = True
                await PlotArchiveService.scan_and_index(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Plot archive watcher error: %s", e)


async def _storage_stats_refresh_loop():
    """Refresh storage stats every hour."""
    from app.database import async_session_factory
    from app.services.storage_service import StorageService
    from app.models.organization import Organization
    from sqlalchemy import select

    while True:
        try:
            await asyncio.sleep(3600)
            async with async_session_factory() as session:
                result = await session.execute(select(Organization))
                orgs = list(result.scalars().all())
                for org in orgs:
                    try:
                        await StorageService.refresh_storage_stats(session, org.id)
                        await session.commit()
                    except Exception as e:
                        logger.warning("Storage refresh failed for org %d: %s", org.id, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Storage stats refresh error: %s", e)


async def _notification_cleanup_loop():
    """Delete read notifications older than 90 days, runs once daily."""
    from app.database import async_session_factory
    from app.services.notification_service import NotificationService

    while True:
        try:
            await asyncio.sleep(86400)  # 24 hours
            async with async_session_factory() as session:
                await NotificationService.cleanup_old_notifications(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Notification cleanup error: %s", e)


async def _work_dir_reaper_loop():
    """Delete abandoned runs' Nextflow work dirs once daily.

    The shared work prefix reached 2.13 TB across five runs because nothing ever
    removed a failed run's intermediates. They are billed monthly and are useless
    two days after the run died.
    """
    from app.database import async_session_factory
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.work_dir_reaper import WorkDirReaper

    while True:
        try:
            await asyncio.sleep(86400)  # 24 hours
            async with async_session_factory() as session:
                raw_bucket = await PlatformConfigService.get(session, "raw_bucket_name")
                if raw_bucket:
                    reaped = await WorkDirReaper.reap(session, raw_bucket=raw_bucket)
                    await session.commit()
                    if reaped:
                        logger.info("Reaped work dirs for runs: %s", reaped)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Work dir reaper error: %s", e)


async def _validation_fetch_reaper_loop():
    """Free the downloaded data of stopped validation studies nobody retried, once daily.

    A study in `error` keeps its fetch so a retry can reuse it, and nothing ever deleted it: the
    fetch is a completed run, which no reaper covered. Past the retry window the objects and the
    rows that point at them go together, so a later retry returns to the approval gate rather than
    relaunching against files that are gone.
    """
    from app.database import async_session_factory
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.validation_fetch_reaper import ValidationFetchReaper

    while True:
        try:
            await asyncio.sleep(86400)  # 24 hours
            async with async_session_factory() as session:
                raw_bucket = await PlatformConfigService.get(session, "raw_bucket_name")
                if raw_bucket:
                    reaped = await ValidationFetchReaper.reap(session, raw_bucket=raw_bucket)
                    await session.commit()
                    if reaped:
                        logger.info("Reaped fetched data for validation studies: %s", reaped)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Validation fetch reaper error: %s", e)


async def _idempotency_cleanup_loop():
    """Sweep expired idempotency_keys rows once an hour (ADR-050)."""
    from app import database as database_module
    from app.services import idempotency_service

    while True:
        try:
            await asyncio.sleep(3600)
            async with database_module.async_session_factory() as session:
                deleted = await idempotency_service.cleanup_expired(session)
                if deleted:
                    await session.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Idempotency cleanup error: %s", e)


async def _backup_health_check_loop():
    """Check backup health every hour, emit events if backups are overdue."""
    from app.database import async_session_factory
    from app.services.backup_service import BackupService
    from app.models.organization import Organization
    from sqlalchemy import select

    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            async with async_session_factory() as session:
                result = await session.execute(select(Organization))
                orgs = list(result.scalars().all())
                for org in orgs:
                    try:
                        await BackupService.check_backup_health(session, org.id)
                    except Exception as e:
                        logger.warning("Backup health check failed for org %d: %s", org.id, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Backup health check error: %s", e)


async def _postgres_backup_loop():
    """Check every 60s whether a scheduled postgres backup is due and run it."""
    from app.database import async_session_factory
    from app.services.backup_service import BackupService

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                due = await BackupService.is_backup_due(session, "postgres")
                if not due:
                    continue
                result = await BackupService.run_postgres_backup(session, org_id=1)
                if result["status"] == "completed":
                    await BackupService.advance_next_run(session, "postgres")
                    await session.commit()
                    logger.info("Scheduled pg_dump completed: %s", result.get("filename"))
                else:
                    logger.error("Scheduled pg_dump failed: %s", result.get("message"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Postgres backup loop error: %s", e)


async def _config_backup_loop():
    """Check every 60s whether a scheduled config backup is due and run it."""
    from app.database import async_session_factory
    from app.services.backup_service import BackupService

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                due = await BackupService.is_backup_due(session, "config")
                if not due:
                    continue
                result = await BackupService.run_config_backup(session, org_id=1)
                if result["status"] == "completed":
                    await BackupService.advance_next_run(session, "config")
                    await session.commit()
                    logger.info("Scheduled config backup completed: %s", result.get("filename"))
                else:
                    logger.error("Scheduled config backup failed: %s", result.get("message"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Config backup loop error: %s", e)


async def _lit_review_auto_loop():
    """Check every 60s whether the automated AI Lit Review cadence is due and,
    if so, sweep experiments with new activity (capped per tick)."""
    from app.database import async_session_factory
    from app.services.literature import lit_review_auto_service

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                await lit_review_auto_service.ensure_next_run_seeded(session, org_id=1)
                due = await lit_review_auto_service.is_tick_due(session, org_id=1)
                await session.commit()
            if not due:
                continue
            async with async_session_factory() as session:
                result = await lit_review_auto_service.run_due_sweep(session, org_id=1)
                await lit_review_auto_service.advance_next_run(session, org_id=1)
                await session.commit()
                logger.info(
                    "Automated AI Lit Review tick: ran %d experiment(s)%s",
                    len(result.get("ran", [])),
                    f" (skipped: {result['skipped_reason']})" if result.get("skipped_reason") else "",
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Lit review auto loop error: %s", e)


async def _cost_billing_sync_once():
    """One pass of the billing sync: every org, committed per org."""
    from app.database import async_session_factory
    from app.services.billing_period import billing_period
    from app.services.cost_service import CostService
    from app.models.organization import Organization
    from sqlalchemy import select

    # One billing period for the whole pass, so every org is synced and checked
    # against the same UTC usage month even if the pass runs past midnight.
    period = billing_period()
    async with async_session_factory() as session:
        result = await session.execute(select(Organization))
        orgs = list(result.scalars().all())
        for org in orgs:
            try:
                report = await CostService.sync_billing_data(session, org.id, period)
                if report is not None and report.status != "imported":
                    logger.warning(
                        "Billing export import %s for org %d (usage %s to %s, end exclusive); existing cost records kept",
                        report.status,
                        org.id,
                        report.usage_start,
                        report.usage_end,
                    )
                await CostService.check_budget_thresholds(session, org.id, period)
                # Per org, so one org's failure cannot discard another's sync.
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.warning("Billing sync failed for org %d: %s", org.id, e)


async def _cost_billing_sync_loop():
    """Sync billing data daily and check budget thresholds."""
    # Sync shortly after boot, then daily. Sleeping the full day first meant an
    # installation restarted more often than once a day never synced at all.
    delay = 60
    while True:
        try:
            await asyncio.sleep(delay)
            delay = 86400  # 24 hours
            await _cost_billing_sync_once()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Cost billing sync error: %s", e)


async def _version_check_loop():
    """Check for platform updates daily."""
    from app.database import async_session_factory
    from app.services.upgrade_service import UpgradeService
    from app.models.organization import Organization
    from sqlalchemy import select

    while True:
        try:
            await asyncio.sleep(86400)  # 24 hours
            async with async_session_factory() as session:
                result = await session.execute(select(Organization))
                orgs = list(result.scalars().all())
                for org in orgs:
                    try:
                        await UpgradeService.background_version_check(org.id)
                    except Exception as e:
                        logger.warning("Version check failed for org %d: %s", org.id, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Version check error: %s", e)


async def _review_reminder_loop():
    """Check for unreviewed pipeline runs every 6 hours."""
    from app.database import async_session_factory
    from app.tasks.review_reminder import check_unreviewed_runs

    while True:
        try:
            await asyncio.sleep(21600)  # 6 hours
            async with async_session_factory() as session:
                await check_unreviewed_runs(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Review reminder error: %s", e)


async def _sdr_trigger_loop():
    """Evaluate SDR re-assessment triggers daily (ADR-064).

    Flags active SDRs whose trigger date has been reached and sends the once-only
    7-day advance warning. The actual logic lives in ``SdrService.evaluate_triggers``
    so it is unit-testable with a controlled clock; the loop just ticks it.
    """
    from app.database import async_session_factory
    from app.services.sdr_service import SdrService

    while True:
        try:
            await asyncio.sleep(86400)  # 24 hours
            async with async_session_factory() as session:
                result = await SdrService.evaluate_triggers(session)
                await session.commit()
                if result["flagged"] or result["warned"]:
                    logger.info(
                        "SDR trigger sweep: flagged %d, warned %d",
                        result["flagged"],
                        result["warned"],
                    )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("SDR trigger loop error: %s", e)


async def _auto_run_launch_loop():
    """Launch pending auto-runs every 30 seconds."""
    from app.database import async_session_factory
    from app.services.auto_run_service import AutoRunService

    while True:
        try:
            await asyncio.sleep(30)
            async with async_session_factory() as session:
                processed = await AutoRunService.process_pending_runs(session)
                if processed:
                    await session.commit()
                    logger.info("Auto-run loop: processed %d pending runs", processed)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Auto-run launch loop error: %s", e)


async def _validation_driver_loop():
    """Advance active literature-validation studies through the execution back half every 30s.

    The A2 back-half driver reacts to committed pipeline-run state (fetchngs done, analysis done),
    so a periodic tick that polls the DB is the right shape (like pipeline-monitor and auto-run),
    not an event subscriber racing the run's completion emit. advance_active_studies isolates and
    commits each study on its own."""
    from app.database import async_session_factory
    from app.services.validation_driver_service import ValidationDriverService

    while True:
        try:
            await asyncio.sleep(30)
            async with async_session_factory() as session:
                advanced = await ValidationDriverService.advance_active_studies(session)
                if advanced:
                    logger.info("Validation driver loop: advanced %d studies", advanced)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Validation driver loop error: %s", e)


async def _session_monitor_loop():
    """Poll notebook sessions for idle timeout every 60 seconds."""
    from app.database import async_session_factory
    from app.services.session_monitor import SessionMonitorService

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                await SessionMonitorService.poll_notebook_sessions(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Session monitor error: %s", e)


async def _notebook_image_build_loop():
    """Poll active notebook image builds every 30 seconds."""
    from app.database import async_session_factory
    from app.services.notebook_image_service import poll_image_build

    while True:
        try:
            await asyncio.sleep(30)
            async with async_session_factory() as session:
                status = await poll_image_build(session)
                if status and status not in ("SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"):
                    await session.commit()
                elif status:
                    await session.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Notebook image build monitor error: %s", e)


async def _cellxgene_image_build_loop():
    """Poll active cellxgene image builds every 30 seconds."""
    from app.database import async_session_factory
    from app.services.cellxgene_image_service import poll_image_build as poll_cellxgene_build

    while True:
        try:
            await asyncio.sleep(30)
            async with async_session_factory() as session:
                status = await poll_cellxgene_build(session)
                if status:
                    await session.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Cellxgene image build monitor error: %s", e)


async def _environment_build_poll_loop():
    """Poll in-progress environment version builds every 30 seconds."""
    from app.database import async_session_factory
    from app.services.environment_build_service import EnvironmentBuildService

    while True:
        try:
            await asyncio.sleep(30)
            async with async_session_factory() as session:
                changed = await EnvironmentBuildService.poll_in_progress_builds(session)
                if changed:
                    await session.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Environment build poll error: %s", e)


async def _work_node_heartbeat_loop():
    """Check work node heartbeat timeouts every 60 seconds."""
    from app.database import async_session_factory
    from app.services.work_node_service import WorkNodeService

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                await WorkNodeService.check_heartbeat_timeouts(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Work node heartbeat check error: %s", e)


async def _export_cleanup_loop():
    """Delete export ZIPs older than 24 hours from GCS every hour."""
    from datetime import datetime, timezone, timedelta

    from app.adapters.registry import get_storage_adapter
    from app.database import async_session_factory
    from app.platform.platform_config_service import PlatformConfigService

    while True:
        try:
            await asyncio.sleep(3600)
            async with async_session_factory() as session:
                bucket_name = await PlatformConfigService.get(session, "config_backups_bucket_name")
                if not bucket_name or bucket_name == "null":
                    continue
                adapter = get_storage_adapter()
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                deleted = 0
                for obj in await adapter.list_objects(adapter.build_uri(bucket_name, "exports/")):
                    created = obj.provider_details.get("time_created")
                    if created and created < cutoff:
                        await adapter.delete(obj.storage_uri)
                        deleted += 1
                if deleted:
                    logger.info(
                        "Export cleanup: deleted %d expired ZIP(s) from the %s exports/ prefix", deleted, bucket_name
                    )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Export cleanup error: %s", e)


async def _nf_core_registry_refresh_loop():
    """Refresh the nf-core registry cache once per day. Warm the cache 60 s
    after startup so the first browse modal is populated, then sleep 24 h
    between refreshes. Failures preserve cached rows."""
    from app.database import async_session_factory
    from app.services.nf_core_registry_service import NfCoreRegistryService

    await asyncio.sleep(60)
    while True:
        try:
            async with async_session_factory() as session:
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
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("nf-core registry refresh loop error: %s", e)
        await asyncio.sleep(86400)


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Docs/OpenAPI endpoints are only enabled when BIOAF_ENVIRONMENT is
    "development".  Production deployments return 404 for /docs and
    /openapi.json (pentest finding #2).
    """
    is_dev = settings.environment == "development"

    application = FastAPI(
        title="bioAF API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if is_dev else None,
        openapi_url="/openapi.json" if is_dev else None,
    )

    # Middleware (applied in reverse order -- last added is outermost). Maintenance sits outside
    # auth and rate limiting: while the upgrade gate is closed nothing reaches them or the database.
    application.add_middleware(AuthMiddleware)
    application.add_middleware(LoggingMiddleware)
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(MaintenanceMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(TaskOwnerMiddleware)

    from app.api.router import api_router

    application.include_router(api_router)

    # Map CapabilityNotSupported (raised by require_capability guards) to a clean
    # 4xx envelope instead of a 500 (Phase 4b). Registered on both the main app
    # and the mounted integration sub-app, since a mounted Starlette sub-app does
    # not inherit the parent's exception handlers.
    from app.adapters.capabilities import CapabilityNotSupported
    from fastapi import Request
    from fastapi.responses import JSONResponse

    async def _capability_not_supported_handler(request: Request, exc: CapabilityNotSupported) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc),
                "code": "capability_not_supported",
                "capability": exc.capability,
            },
        )

    application.add_exception_handler(CapabilityNotSupported, _capability_not_supported_handler)

    # Map the domain exception hierarchy (app.exceptions) to its declared
    # status codes and {detail, code} envelope, so services can raise typed
    # errors and routes need no per-call except ValueError blocks.
    from app.error_handlers import register_error_handlers

    register_error_handlers(application)

    # Public integration API sub-app (ADR-048). Owns its own OpenAPI document;
    # docs are served in production regardless of the main app's gating.
    from app.api.v1.integrations import build_integrations_app

    integrations_app = build_integrations_app()
    integrations_app.add_exception_handler(CapabilityNotSupported, _capability_not_supported_handler)
    register_error_handlers(integrations_app)
    application.mount("/api/v1/integrations", integrations_app)
    return application


app = create_app()
