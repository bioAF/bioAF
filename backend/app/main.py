import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
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

    # Verify database connection
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")

    # Attach Cloud Logging using the app's configured GCP credentials
    try:
        from app.database import async_session_factory as cl_session_factory
        from app.services.gcs_storage import GcsStorageService

        async with cl_session_factory() as cl_session:
            result = await cl_session.execute(text("SELECT value FROM platform_config WHERE key = 'gcp_project_id'"))
            row = result.fetchone()
            gcp_project_id = row[0] if row and row[0] and row[0] != "null" else ""

            if gcp_project_id:
                credentials = await GcsStorageService.get_credentials(cl_session)
                attach_cloud_logging(gcp_project_id, credentials, debug=settings.debug)
    except Exception as e:
        logger.info("Cloud Logging not configured: %s", e)

    # Load persisted SMTP settings from database (gracefully skip if columns
    # don't exist yet, e.g. before migration 040 has run)
    try:
        async with engine.connect() as smtp_conn:
            smtp_result = await smtp_conn.execute(
                text(
                    "SELECT smtp_configured, smtp_host, smtp_port, smtp_username,"
                    " smtp_password, smtp_from_address, smtp_encryption"
                    " FROM organizations LIMIT 1"
                )
            )
            smtp_row = smtp_result.mappings().first()
            if smtp_row and smtp_row["smtp_configured"] and smtp_row["smtp_host"]:
                settings.smtp_host = smtp_row["smtp_host"]
                settings.smtp_port = smtp_row["smtp_port"]
                settings.smtp_username = smtp_row["smtp_username"]
                settings.smtp_password = smtp_row["smtp_password"]
                settings.smtp_from_address = smtp_row["smtp_from_address"]
                settings.smtp_encryption = smtp_row["smtp_encryption"]
                settings.smtp_configured = True
                logger.info("SMTP settings loaded from database")
    except Exception as e:
        logger.warning("Could not load SMTP settings from database: %s", e)

    # Initialize notification system
    from app.database import async_session_factory as notif_session_factory
    from app.services.notification_router import NotificationRouter

    notification_router = NotificationRouter(notif_session_factory)
    notification_router.register()
    logger.info("Notification system initialized")

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

    # Resolve any pending upgrades from before the restart
    from app.services.upgrade_service import UpgradeService

    try:
        async with notif_session_factory() as upgrade_session:
            await UpgradeService.resolve_pending_upgrades(upgrade_session)
            await upgrade_session.commit()
    except Exception as e:
        logger.warning("Could not resolve pending upgrades: %s", e)

    logger.info("bioAF backend started successfully")

    # Start background tasks
    background_tasks = []
    background_tasks.append(asyncio.create_task(_job_status_sync_loop()))
    background_tasks.append(asyncio.create_task(_idle_session_check_loop()))
    background_tasks.append(asyncio.create_task(_quota_reset_loop()))
    background_tasks.append(asyncio.create_task(_pipeline_monitor_loop()))
    background_tasks.append(asyncio.create_task(_plot_archive_watcher_loop()))
    background_tasks.append(asyncio.create_task(_storage_stats_refresh_loop()))
    background_tasks.append(asyncio.create_task(_reconciler_loop()))
    background_tasks.append(asyncio.create_task(_notification_cleanup_loop()))
    background_tasks.append(asyncio.create_task(_backup_health_check_loop()))
    background_tasks.append(asyncio.create_task(_postgres_backup_loop()))
    background_tasks.append(asyncio.create_task(_cost_billing_sync_loop()))
    background_tasks.append(asyncio.create_task(_version_check_loop()))
    background_tasks.append(asyncio.create_task(_review_reminder_loop()))
    background_tasks.append(asyncio.create_task(_auto_run_launch_loop()))
    background_tasks.append(asyncio.create_task(_pubsub_listener_loop()))
    background_tasks.append(asyncio.create_task(_session_monitor_loop()))
    background_tasks.append(asyncio.create_task(_notebook_image_build_loop()))
    background_tasks.append(asyncio.create_task(_cellxgene_image_build_loop()))
    background_tasks.append(asyncio.create_task(_environment_build_poll_loop()))
    background_tasks.append(asyncio.create_task(_work_node_heartbeat_loop()))
    background_tasks.append(asyncio.create_task(_export_cleanup_loop()))
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


async def _reconciler_loop():
    """Process pending environment reconciliation tasks every 5 seconds."""
    from app.database import async_session_factory
    from app.services.reconciler_service import ReconcilerService

    while True:
        try:
            await asyncio.sleep(5)
            async with async_session_factory() as session:
                await ReconcilerService.process_pending(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Reconciler error: %s", e)


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
    """Run pg_dump backups on the configured interval."""
    from app.config import settings
    from app.database import async_session_factory
    from app.services.backup_service import BackupService

    interval = settings.backup_postgres_interval_hours * 3600
    while True:
        try:
            await asyncio.sleep(interval)
            async with async_session_factory() as session:
                result = await BackupService.run_postgres_backup(session, org_id=1)
            if result["status"] == "completed":
                logger.info("Scheduled pg_dump completed: %s", result.get("filename"))
            else:
                logger.error("Scheduled pg_dump failed: %s", result.get("message"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Postgres backup loop error: %s", e)


async def _cost_billing_sync_loop():
    """Sync billing data daily and check budget thresholds."""
    from app.database import async_session_factory
    from app.services.cost_service import CostService
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
                        await CostService.sync_billing_data(session, org.id)
                        await CostService.check_budget_thresholds(session, org.id)
                    except Exception as e:
                        logger.warning("Billing sync failed for org %d: %s", org.id, e)
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

    from sqlalchemy import text as sa_text

    from app.database import async_session_factory
    from app.services.gcs_storage import GcsStorageService

    while True:
        try:
            await asyncio.sleep(3600)
            async with async_session_factory() as session:
                result = await session.execute(
                    sa_text("SELECT value FROM platform_config WHERE key = 'config_backups_bucket_name'")
                )
                row = result.fetchone()
                if not row or not row[0] or row[0] == "null":
                    continue

                bucket_name = row[0]
                credentials = await GcsStorageService.get_credentials(session)

                from google.cloud import storage as gcs

                client = gcs.Client(credentials=credentials)
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                deleted = 0
                for blob in client.list_blobs(bucket_name, prefix="exports/"):
                    if blob.time_created and blob.time_created < cutoff:
                        blob.delete()
                        deleted += 1
                if deleted:
                    logger.info("Export cleanup: deleted %d expired ZIP(s) from gs://%s/exports/", deleted, bucket_name)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Export cleanup error: %s", e)


app = FastAPI(
    title="bioAF API",
    version=settings.app_version,
    lifespan=lifespan,
)

# Middleware (applied in reverse order -- last added is outermost)
app.add_middleware(AuthMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Include routers
from app.api.router import api_router  # noqa: E402

app.include_router(api_router)
