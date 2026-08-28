import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings, validate_encryption_keys, validate_jwt_secret
from app.database import engine
from app.logging_config import attach_cloud_logging, configure_logging
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware

configure_logging(debug=settings.debug)
logger = logging.getLogger("bioaf")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("bioAF backend starting up (env=%s)", settings.environment)

    # Fetch secrets from Secret Manager in production
    if settings.use_secret_manager:
        from app.services.secrets_service import SecretsService

        try:
            secrets_service = SecretsService(settings.gcp_project_id)
            secrets = secrets_service.fetch_all()
            # Update settings from secrets
            if "bioaf-db-app-password" in secrets:
                settings.database_url = settings.database_url.replace("password", secrets["bioaf-db-app-password"])
            if "bioaf-jwt-signing-key" in secrets:
                settings.jwt_secret_key = secrets["bioaf-jwt-signing-key"]
            if "bioaf-smtp-credentials" in secrets:
                import json

                smtp_config = json.loads(secrets["bioaf-smtp-credentials"])
                if smtp_config.get("host"):
                    settings.smtp_host = smtp_config["host"]
                    settings.smtp_port = smtp_config.get("port", 587)
                    settings.smtp_username = smtp_config.get("username", "")
                    settings.smtp_password = smtp_config.get("password", "")
                    settings.smtp_from_address = smtp_config.get("from_address", "")
                    settings.smtp_configured = True
            logger.info("Secrets fetched from Secret Manager")
        except Exception as e:
            logger.error("Failed to fetch secrets from Secret Manager: %s", e)
            raise RuntimeError(f"Secret Manager unreachable: {e}") from e

    # Block startup if the JWT secret is a known insecure default
    validate_jwt_secret(settings.jwt_secret_key)

    # Block startup if at-rest encryption keys are missing or malformed.
    # The encryption_service module already validates at import time, but
    # call here as well so the failure surfaces in the same lifespan log
    # block as validate_jwt_secret.
    validate_encryption_keys(settings.encryption_keys)

    # Verify database connection
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")

    # Bootstrap identity from VM metadata. cloud_provider first (the foundational
    # identity every BAL seam resolves from): explicit installer-stamped value, else
    # auto-detect (GCE metadata vs EC2 IMDSv2), immutable once set. Then the
    # bioaf-bootstrap and bioaf-app SA emails: the installer attaches the bootstrap
    # email to instance metadata (--metadata=bioaf_bootstrap_sa_email=...) and
    # bioaf-app as the VM's attached SA, so the backend persists both to
    # platform_config on first startup. The app SA email is what Terraform grants
    # dataset read on the BQ billing export (ADR-028). No-op when not running on a
    # known cloud or when the rows already exist.
    try:
        from app.database import async_session_factory as _bootstrap_session_factory
        from app.services.bootstrap_metadata import (
            persist_app_sa_from_metadata,
            persist_bootstrap_sa_from_metadata,
            persist_cloud_provider,
        )

        async with _bootstrap_session_factory() as _bootstrap_session:
            await persist_cloud_provider(_bootstrap_session)
            await persist_bootstrap_sa_from_metadata(_bootstrap_session)
            await persist_app_sa_from_metadata(_bootstrap_session)
    except Exception as e:
        logger.info("Bootstrap metadata read skipped: %s", e)

    # Attach Cloud Logging using the app's configured GCP credentials
    try:
        from app.database import async_session_factory as cl_session_factory
        from app.adapters.credentials import credential_injector
        from app.platform.platform_config_service import PlatformConfigService

        async with cl_session_factory() as cl_session:
            project_value = await PlatformConfigService.get(cl_session, "gcp_project_id")
            gcp_project_id = project_value if project_value and project_value != "null" else ""

            if gcp_project_id:
                cred_config = await PlatformConfigService.get_many(
                    cl_session,
                    [
                        "gcp_credential_source",
                        "gcp_service_account_key",
                        "gcp_service_account_email",
                        "gcp_bootstrap_sa_email",
                    ],
                )
                try:
                    credentials = credential_injector.load_gcp_credentials(cred_config)
                except Exception:
                    credentials = None
                attach_cloud_logging(gcp_project_id, credentials, debug=settings.debug)
    except Exception as e:
        logger.info("Cloud Logging not configured: %s", e)

    # Load persisted SMTP settings from database. Read through the ORM so the
    # encrypted smtp_password column is decrypted (a raw SQL read would leave it
    # as Fernet ciphertext). Gracefully skip if the columns don't exist yet,
    # e.g. before migration 040 has run.
    try:
        from app.database import async_session_factory as smtp_session_factory
        from app.services.email_service import load_persisted_smtp_settings

        async with smtp_session_factory() as smtp_session:
            if await load_persisted_smtp_settings(smtp_session):
                logger.info("SMTP settings loaded from database")
    except Exception as e:
        logger.warning("Could not load SMTP settings from database: %s", e)

    # Initialize notification system
    from app.database import async_session_factory as notif_session_factory
    from app.services.notification_router import NotificationRouter

    notification_router = NotificationRouter(notif_session_factory)
    notification_router.register()
    logger.info("Notification system initialized")

    # Subscribe custom pipeline cascade handler to environment build completions (ADR-046)
    from app.services.custom_pipeline_service import CustomPipelineService
    from app.services.event_bus import event_bus
    from app.services.event_types import ENVIRONMENT_BUILD_COMPLETED

    event_bus.subscribe(
        ENVIRONMENT_BUILD_COMPLETED,
        CustomPipelineService.handle_environment_build_completed,
    )
    logger.info("Custom pipeline cascade handler subscribed")

    # Initialize BioAF Adapter Layer (BAL)
    from app.adapters.registry import initialize_adapters

    async with notif_session_factory() as adapter_session:
        await initialize_adapters(adapter_session, session_factory=notif_session_factory)
    logger.info("BAL adapters initialized")

    # Sync built-in role permissions (backfill any new permissions added to bootstrap_roles)
    from app.services.bootstrap_roles import BUILTIN_ROLES
    from app.models.role import Role, RolePermission
    from sqlalchemy import select as sa_select

    async with notif_session_factory() as role_sync_session:
        try:
            for role_name, (_desc, perm_map) in BUILTIN_ROLES.items():
                roles_result = await role_sync_session.execute(
                    sa_select(Role).where(Role.name == role_name, Role.is_system == True)  # noqa: E712
                )
                for role in roles_result.scalars().all():
                    existing_result = await role_sync_session.execute(
                        sa_select(RolePermission.resource, RolePermission.action).where(
                            RolePermission.role_id == role.id
                        )
                    )
                    existing = {(r, a) for r, a in existing_result.fetchall()}
                    expected = {(r, a) for r, actions in perm_map.items() for a in actions}
                    missing = expected - existing
                    for resource, action in missing:
                        role_sync_session.add(RolePermission(role_id=role.id, resource=resource, action=action))
                    if missing:
                        logger.info(
                            "Synced %d permissions to built-in role '%s' (org %d)",
                            len(missing),
                            role_name,
                            role.organization_id,
                        )
            await role_sync_session.commit()
        except Exception as e:
            logger.warning("Built-in role permission sync failed: %s", e)

    # Seed default work node environment if none exists (ADR-043)
    from app.services.environment_service import ensure_default_work_node_environment

    try:
        async with notif_session_factory() as env_seed_session:
            await ensure_default_work_node_environment(env_seed_session)
            await env_seed_session.commit()
    except Exception as e:
        logger.warning("Default work node environment seed failed: %s", e)

    # Seed built-in `bioaf-base` work-node env (to-resolve.md issue #1).
    # No-op if BIOAF_BASE_WORK_NODE_IMAGE_URI is unset.
    from app.services.bootstrap_environments import seed_builtin_environments

    try:
        async with notif_session_factory() as builtin_env_session:
            await seed_builtin_environments(builtin_env_session)
            await builtin_env_session.commit()
    except Exception as e:
        logger.warning("Built-in environment seed failed: %s", e)

    # Seed default pipeline environment if none exists (ADR-045)
    from app.services.environment_service import ensure_default_pipeline_environment

    try:
        async with notif_session_factory() as pipe_seed_session:
            await ensure_default_pipeline_environment(pipe_seed_session)
            await pipe_seed_session.commit()
    except Exception as e:
        logger.warning("Default pipeline environment seed failed: %s", e)

    # Seed the 10x bamtofastq converter (an archival 10x BAM carries its barcodes in tags, so it is
    # unreachable without this tool). Draft environment: it needs a build before it can launch.
    from app.services.bootstrap_bamtofastq import ensure_bamtofastq_pipeline

    try:
        async with notif_session_factory() as b2f_seed_session:
            await ensure_bamtofastq_pipeline(b2f_seed_session)
            await b2f_seed_session.commit()
    except Exception as e:
        logger.warning("10x bamtofastq seed failed: %s", e)

    # Seed default notebook environment if none exists
    from app.services.environment_service import ensure_default_notebook_environment

    try:
        async with notif_session_factory() as nb_seed_session:
            await ensure_default_notebook_environment(nb_seed_session)
            await nb_seed_session.commit()
    except Exception as e:
        logger.warning("Default notebook environment seed failed: %s", e)

    # Resolve any pending upgrades from before the restart
    from app.services.upgrade_service import UpgradeService

    try:
        async with notif_session_factory() as upgrade_session:
            await UpgradeService.resolve_pending_upgrades(upgrade_session)
            await upgrade_session.commit()
    except Exception as e:
        logger.warning("Could not resolve pending upgrades: %s", e)

    # Mark any in-flight hosted LLM review jobs as failed with reason
    # process_restart (ADR-055). Gemma jobs are owned by the orchestrator
    # and are left in place.
    from app.services import agent_review_job_service

    try:
        async with notif_session_factory() as orphan_session:
            count = await agent_review_job_service.mark_orphaned_on_startup(orphan_session)
            await orphan_session.commit()
            if count:
                logger.info("Marked %d orphaned LLM review jobs as failed on startup", count)
    except Exception as e:
        logger.warning("Could not mark orphaned LLM review jobs: %s", e)

    # Fail any glossary scan jobs left in-flight by a restart (ADR-062).
    from app.services import lab_glossary_scan_service

    try:
        async with notif_session_factory() as orphan_session:
            count = await lab_glossary_scan_service.mark_orphaned_on_startup(orphan_session)
            await orphan_session.commit()
            if count:
                logger.info("Marked %d orphaned glossary scan jobs as failed on startup", count)
    except Exception as e:
        logger.warning("Could not mark orphaned glossary scan jobs: %s", e)

    logger.info("bioAF backend started successfully")

    # Start background tasks
    background_tasks = []
    background_tasks.append(asyncio.create_task(_job_status_sync_loop()))
    background_tasks.append(asyncio.create_task(_idle_session_check_loop()))
    background_tasks.append(asyncio.create_task(_quota_reset_loop()))
    background_tasks.append(asyncio.create_task(_pipeline_monitor_loop()))
    background_tasks.append(asyncio.create_task(_plot_archive_watcher_loop()))
    background_tasks.append(asyncio.create_task(_storage_stats_refresh_loop()))
    background_tasks.append(asyncio.create_task(_notification_cleanup_loop()))
    background_tasks.append(asyncio.create_task(_work_dir_reaper_loop()))
    background_tasks.append(asyncio.create_task(_backup_health_check_loop()))
    background_tasks.append(asyncio.create_task(_postgres_backup_loop()))
    background_tasks.append(asyncio.create_task(_config_backup_loop()))
    background_tasks.append(asyncio.create_task(_lit_review_auto_loop()))
    background_tasks.append(asyncio.create_task(_cost_billing_sync_loop()))
    background_tasks.append(asyncio.create_task(_version_check_loop()))
    background_tasks.append(asyncio.create_task(_review_reminder_loop()))
    background_tasks.append(asyncio.create_task(_auto_run_launch_loop()))
    background_tasks.append(asyncio.create_task(_validation_driver_loop()))
    background_tasks.append(asyncio.create_task(_pubsub_listener_loop()))
    background_tasks.append(asyncio.create_task(_session_monitor_loop()))
    background_tasks.append(asyncio.create_task(_notebook_image_build_loop()))
    background_tasks.append(asyncio.create_task(_cellxgene_image_build_loop()))
    background_tasks.append(asyncio.create_task(_environment_build_poll_loop()))
    background_tasks.append(asyncio.create_task(_work_node_heartbeat_loop()))
    background_tasks.append(asyncio.create_task(_export_cleanup_loop()))
    background_tasks.append(asyncio.create_task(_nf_core_registry_refresh_loop()))
    background_tasks.append(asyncio.create_task(_sdr_trigger_loop()))

    # LIMS integration (ADR-051): subscribe webhook dispatcher to internal
    # events, start the delivery worker, and add an idempotency-key cleanup
    # tick alongside the existing background loops.
    from app.services import webhook_dispatcher
    from app.services.webhook_worker import run_worker_loop as _webhook_run_worker_loop

    webhook_dispatcher.subscribe_all()
    background_tasks.append(asyncio.create_task(_webhook_run_worker_loop()))
    background_tasks.append(asyncio.create_task(_idempotency_cleanup_loop()))
    logger.info("Background tasks started")

    yield

    # Cancel background tasks
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)

    # Shutdown
    await engine.dispose()
    logger.info("bioAF backend shut down")


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
    from app.services.cost_service import CostService
    from app.models.organization import Organization
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(select(Organization))
        orgs = list(result.scalars().all())
        for org in orgs:
            try:
                await CostService.sync_billing_data(session, org.id)
                await CostService.check_budget_thresholds(session, org.id)
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


async def _pubsub_listener_loop():
    """Run the Pub/Sub listener for auto-ingest if enabled."""
    from app.database import async_session_factory
    from app.services.pubsub_listener import start_pubsub_listener_task

    try:
        async with async_session_factory() as session:
            await start_pubsub_listener_task(session)
    except asyncio.CancelledError:
        from app.services.pubsub_listener import get_listener

        listener = get_listener()
        if listener:
            listener.stop()
    except Exception as e:
        logger.error("Pub/Sub listener error: %s", e)


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

    # Middleware (applied in reverse order -- last added is outermost)
    application.add_middleware(AuthMiddleware)
    application.add_middleware(LoggingMiddleware)
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)

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
