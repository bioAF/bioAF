"""Ordered startup and shutdown of the backend.

Startup is a list of named steps run in order, each with a declared failure policy:

- ``fatal``: the exception propagates and startup stops (secrets, settings validation, the
  database connection, notification and cascade registration, adapters).
- ``optional``: the exception is logged with the step's own message and startup continues
  (bootstrap metadata, Cloud Logging, SMTP settings, seeds, restart reconciliation).

Cleanup is acquired as each resource is installed, on one ``AsyncExitStack``: whatever a step
installed is undone on a partial startup, on normal exit, on an exception through the lifespan,
and on cancellation. Event subscriptions a step adds are removed again; subscribers installed by
anyone else are kept.

The schema gate (plan 9) precedes every normal step that writes, subscribes, or starts work.
Steps are never run in parallel.

Shutdown budgets: active work gets ``DRAIN_SECONDS`` to settle, then
cancellation and event/child cleanup get ``CANCEL_SECONDS``, then owned resources get
``RESOURCE_SECONDS``. The container's 60 s stop grace (docker-compose) is the hard stop when work
does not cooperate; uvicorn's own request grace runs before the lifespan's shutdown and is separate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from app.services.event_bus import event_bus

logger = logging.getLogger("bioaf")

DRAIN_SECONDS = 10.0
CANCEL_SECONDS = 20.0
RESOURCE_SECONDS = 5.0


@dataclass(frozen=True)
class Step:
    """One startup step. ``failure`` is the optional step's log message, formatted with the error."""

    name: str
    run: Callable[[StartupContext], Awaitable[None]]
    policy: Literal["fatal", "optional"] = "fatal"
    failure: str = ""
    failure_level: int = logging.WARNING


@dataclass
class StartupContext:
    """``stack`` holds cleanup for what steps install; ``subscriptions`` removes their subscribers."""

    engine: Any
    stack: AsyncExitStack
    subscriptions: AsyncExitStack


@contextmanager
def _removing_new_subscriptions(stack: AsyncExitStack):
    """Remove, on unwind, every event subscription added inside the block (only those)."""
    before = {k: list(v) for k, v in event_bus._subscribers.items()}
    try:
        yield
    finally:
        added = []
        for event_type, callbacks in event_bus._subscribers.items():
            existing = list(before.get(event_type, []))
            for callback in callbacks:
                if callback in existing:
                    existing.remove(callback)
                else:
                    added.append((event_type, callback))
        if added:
            stack.callback(_unsubscribe, added)


def _unsubscribe(added: list[tuple[str, Any]]) -> None:
    for event_type, callback in added:
        event_bus.unsubscribe(event_type, callback)


async def run_steps(steps: list[Step], ctx: StartupContext) -> None:
    for step in steps:
        logger.debug("startup step=%s transition=started", step.name)
        try:
            with _removing_new_subscriptions(ctx.subscriptions):
                await step.run(ctx)
        except Exception as e:
            if step.policy == "fatal":
                logger.error("startup step=%s outcome=failed exception_type=%s", step.name, type(e).__name__)
                raise
            logger.log(step.failure_level, step.failure, e)
            continue
        logger.debug("startup step=%s transition=finished", step.name)


# --- steps before the schema gate ---------------------------------------------------------------


async def fetch_secrets(ctx: StartupContext) -> None:
    """Fetch secrets from Secret Manager in production."""
    from app.config import settings

    if not settings.use_secret_manager:
        return
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


async def validate_settings(ctx: StartupContext) -> None:
    from app.config import settings, validate_encryption_keys, validate_jwt_secret

    # Block startup if the JWT secret is a known insecure default
    validate_jwt_secret(settings.jwt_secret_key)
    # Block startup if at-rest encryption keys are missing or malformed.
    # The encryption_service module already validates at import time, but
    # call here as well so the failure surfaces in the same lifespan log
    # block as validate_jwt_secret.
    validate_encryption_keys(settings.encryption_keys)


async def verify_database(ctx: StartupContext) -> None:
    from sqlalchemy import text

    async with ctx.engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")


PRE_GATE_STEPS = [
    Step("secrets", fetch_secrets),
    Step("validate_settings", validate_settings),
    Step("database_connection", verify_database),
]


# --- steps after the schema gate ----------------------------------------------------------------


async def bootstrap_metadata(ctx: StartupContext) -> None:
    # Bootstrap identity from VM metadata. cloud_provider first (the foundational
    # identity every BAL seam resolves from): explicit installer-stamped value, else
    # auto-detect (GCE metadata vs EC2 IMDSv2), immutable once set. Then the
    # bioaf-bootstrap and bioaf-app SA emails: the installer attaches the bootstrap
    # email to instance metadata (--metadata=bioaf_bootstrap_sa_email=...) and
    # bioaf-app as the VM's attached SA, so the backend persists both to
    # platform_config on first startup. The app SA email is what Terraform grants
    # dataset read on the BQ billing export (ADR-028). No-op when not running on a
    # known cloud or when the rows already exist.
    from app.database import async_session_factory
    from app.services.bootstrap_metadata import (
        persist_app_sa_from_metadata,
        persist_bootstrap_sa_from_metadata,
        persist_cloud_provider,
    )

    async with async_session_factory() as session:
        await persist_cloud_provider(session)
        await persist_bootstrap_sa_from_metadata(session)
        await persist_app_sa_from_metadata(session)


async def cloud_logging(ctx: StartupContext) -> None:
    """Attach Cloud Logging using the app's configured GCP credentials."""
    from app.adapters.credentials import credential_injector
    from app.config import settings
    from app.database import async_session_factory
    from app.logging_config import attach_cloud_logging
    from app.platform.platform_config_service import PlatformConfigService

    async with async_session_factory() as session:
        project_value = await PlatformConfigService.get(session, "gcp_project_id")
        gcp_project_id = project_value if project_value and project_value != "null" else ""

        if gcp_project_id:
            cred_config = await PlatformConfigService.get_many(
                session,
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


async def smtp_settings(ctx: StartupContext) -> None:
    # Load persisted SMTP settings from database. Read through the ORM so the
    # encrypted smtp_password column is decrypted (a raw SQL read would leave it
    # as Fernet ciphertext). Gracefully skip if the columns don't exist yet,
    # e.g. before migration 040 has run.
    from app.database import async_session_factory
    from app.services.email_service import load_persisted_smtp_settings

    async with async_session_factory() as session:
        if await load_persisted_smtp_settings(session):
            logger.info("SMTP settings loaded from database")


async def notifications(ctx: StartupContext) -> None:
    from app.database import async_session_factory
    from app.services.notification_router import NotificationRouter

    NotificationRouter(async_session_factory).register()
    logger.info("Notification system initialized")


async def pipeline_cascade(ctx: StartupContext) -> None:
    """Subscribe custom pipeline cascade handler to environment build completions (ADR-046)."""
    from app.services.custom_pipeline_service import CustomPipelineService
    from app.services.event_types import ENVIRONMENT_BUILD_COMPLETED

    event_bus.subscribe(ENVIRONMENT_BUILD_COMPLETED, CustomPipelineService.handle_environment_build_completed)
    logger.info("Custom pipeline cascade handler subscribed")


async def adapters(ctx: StartupContext) -> None:
    """Initialize BioAF Adapter Layer (BAL)."""
    from app.adapters.registry import initialize_adapters
    from app.database import async_session_factory

    async with async_session_factory() as session:
        await initialize_adapters(session, session_factory=async_session_factory)
    logger.info("BAL adapters initialized")


async def role_sync(ctx: StartupContext) -> None:
    """Sync built-in role permissions (backfill any new permissions added to bootstrap_roles).

    Fatal only if the session cannot be opened, as before; a sync failure is logged.
    """
    from sqlalchemy import select as sa_select

    from app.database import async_session_factory
    from app.models.role import Role, RolePermission
    from app.services.bootstrap_roles import BUILTIN_ROLES

    async with async_session_factory() as session:
        try:
            for role_name, (_desc, perm_map) in BUILTIN_ROLES.items():
                roles_result = await session.execute(
                    sa_select(Role).where(Role.name == role_name, Role.is_system == True)  # noqa: E712
                )
                for role in roles_result.scalars().all():
                    existing_result = await session.execute(
                        sa_select(RolePermission.resource, RolePermission.action).where(
                            RolePermission.role_id == role.id
                        )
                    )
                    existing = {(r, a) for r, a in existing_result.fetchall()}
                    expected = {(r, a) for r, actions in perm_map.items() for a in actions}
                    missing = expected - existing
                    for resource, action in missing:
                        session.add(RolePermission(role_id=role.id, resource=resource, action=action))
                    if missing:
                        logger.info(
                            "Synced %d permissions to built-in role '%s' (org %d)",
                            len(missing),
                            role_name,
                            role.organization_id,
                        )
            await session.commit()
        except Exception as e:
            logger.warning("Built-in role permission sync failed: %s", e)


def _committed(module: str, name: str) -> Callable[[StartupContext], Awaitable[None]]:
    """A step that runs ``module.name(session)`` in its own session and commits."""

    async def run(ctx: StartupContext) -> None:
        import importlib

        from app.database import async_session_factory

        fn = getattr(importlib.import_module(module), name)
        async with async_session_factory() as session:
            await fn(session)
            await session.commit()

    return run


async def pending_upgrades(ctx: StartupContext) -> None:
    """Resolve any pending upgrades from before the restart."""
    from app.database import async_session_factory
    from app.services.upgrade_service import UpgradeService

    async with async_session_factory() as session:
        await UpgradeService.resolve_pending_upgrades(session)
        await session.commit()


async def hosted_review_orphans(ctx: StartupContext) -> None:
    # Mark any in-flight hosted LLM review jobs as failed with reason
    # process_restart (ADR-055). Gemma jobs are owned by the orchestrator
    # and are left in place.
    from app.database import async_session_factory
    from app.services import agent_review_job_service

    async with async_session_factory() as session:
        count = await agent_review_job_service.mark_orphaned_on_startup(session)
        await session.commit()
        if count:
            logger.info("Marked %d orphaned LLM review jobs as failed on startup", count)


async def glossary_orphans(ctx: StartupContext) -> None:
    """Fail any glossary scan jobs left in-flight by a restart (ADR-062)."""
    from app.database import async_session_factory
    from app.services import lab_glossary_scan_service

    async with async_session_factory() as session:
        count = await lab_glossary_scan_service.mark_orphaned_on_startup(session)
        await session.commit()
        if count:
            logger.info("Marked %d orphaned glossary scan jobs as failed on startup", count)


async def interrupted_local_jobs(ctx: StartupContext) -> None:
    """Fail local jobs (searches, URL imports, PDF extractions) a restart interrupted."""
    from app.database import async_session_factory
    from app.services.restart_recovery import fail_interrupted_local_jobs

    async with async_session_factory() as session:
        counts = await fail_interrupted_local_jobs(session)
        await session.commit()
    if any(counts.values()):
        logger.info("Marked jobs interrupted by the restart as failed: %s", counts)


NORMAL_STEPS = [
    Step("bootstrap_metadata", bootstrap_metadata, "optional", "Bootstrap metadata read skipped: %s", logging.INFO),
    Step("cloud_logging", cloud_logging, "optional", "Cloud Logging not configured: %s", logging.INFO),
    Step("smtp_settings", smtp_settings, "optional", "Could not load SMTP settings from database: %s"),
    Step("notifications", notifications),
    Step("pipeline_cascade", pipeline_cascade),
    Step("adapters", adapters),
    Step("role_sync", role_sync),
    # Seed default work node environment if none exists (ADR-043)
    Step(
        "work_node_environment",
        _committed("app.services.environment_service", "ensure_default_work_node_environment"),
        "optional",
        "Default work node environment seed failed: %s",
    ),
    # Seed built-in `bioaf-base` work-node env. No-op if BIOAF_BASE_WORK_NODE_IMAGE_URI is unset.
    Step(
        "builtin_environments",
        _committed("app.services.bootstrap_environments", "seed_builtin_environments"),
        "optional",
        "Built-in environment seed failed: %s",
    ),
    # Seed default pipeline environment if none exists (ADR-045)
    Step(
        "pipeline_environment",
        _committed("app.services.environment_service", "ensure_default_pipeline_environment"),
        "optional",
        "Default pipeline environment seed failed: %s",
    ),
    # Seed the 10x bamtofastq converter (an archival 10x BAM carries its barcodes in tags, so it is
    # unreachable without this tool). Draft environment: it needs a build before it can launch.
    Step(
        "bamtofastq",
        _committed("app.services.bootstrap_bamtofastq", "ensure_bamtofastq_pipeline"),
        "optional",
        "10x bamtofastq seed failed: %s",
    ),
    # Seed default notebook environment if none exists
    Step(
        "notebook_environment",
        _committed("app.services.environment_service", "ensure_default_notebook_environment"),
        "optional",
        "Default notebook environment seed failed: %s",
    ),
    Step("pending_upgrades", pending_upgrades, "optional", "Could not resolve pending upgrades: %s"),
    Step("hosted_review_orphans", hosted_review_orphans, "optional", "Could not mark orphaned LLM review jobs: %s"),
    Step("glossary_orphans", glossary_orphans, "optional", "Could not mark orphaned glossary scan jobs: %s"),
    Step(
        "interrupted_local_jobs",
        interrupted_local_jobs,
        "optional",
        "Could not mark jobs interrupted by the restart as failed: %s",
    ),
]


async def bounded(awaitable: Awaitable[Any], seconds: float, what: str) -> bool:
    """Run ``awaitable`` for at most ``seconds``; past that, cancel it once and abandon it.

    Unlike ``wait_for``, this returns at the deadline even if the step suppresses cancellation, so
    a shutdown phase cannot overrun its share of the budget. Returns whether the step finished.
    """
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if done:
        task.result()
        return True
    task.cancel()
    logger.error("shutdown step=%s outcome=abandoned budget=%.1fs", what, seconds)
    return False
