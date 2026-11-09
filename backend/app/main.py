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
from app.tasks.jobs import cost_billing_sync_once as _cost_billing_sync_once  # noqa: F401 -- tests import it here

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


# Started, in this order, before the Pub/Sub listener; the remaining periodic jobs start after it.
_BEFORE_LISTENER = 18


async def _start_workers(owner: TaskOwner, ctx: startup.StartupContext) -> None:
    """Start the periodic jobs, the Pub/Sub listener, and the webhook worker, all owned.

    Periodic jobs watch the owner's stop signal, so closing admission ends them between
    iterations; an iteration in progress gets the drain to finish.
    """
    from app.services.pubsub_listener import listener_manager
    from app.services.webhook_worker import run_worker_loop as _webhook_run_worker_loop
    from app.tasks.jobs import PERIODIC_JOBS
    from app.tasks.periodic import run_periodic

    *periodic, idempotency = PERIODIC_JOBS
    for job in periodic[:_BEFORE_LISTENER]:
        owner.spawn(run_periodic(job, stopping=owner.stopping), role=job.role)
    await listener_manager().start()
    for job in periodic[_BEFORE_LISTENER:]:
        owner.spawn(run_periodic(job, stopping=owner.stopping), role=job.role)

    # LIMS integration (ADR-051): subscribe webhook dispatcher to internal
    # events, start the delivery worker, and add an idempotency-key cleanup
    # tick alongside the existing background loops.
    from app.services import webhook_dispatcher

    async def subscribe_webhooks(_ctx: startup.StartupContext) -> None:
        webhook_dispatcher.subscribe_all()

    await startup.run_steps([startup.Step("webhook_dispatcher", subscribe_webhooks)], ctx)
    owner.spawn(_webhook_run_worker_loop(stop_event=owner.stopping), role="webhook-worker")
    owner.spawn(run_periodic(idempotency, stopping=owner.stopping), role=idempotency.role)


async def _stop_owned_work(owner: TaskOwner) -> None:
    """Stop producers at once, let active work settle, then cancel what remains (bounded)."""
    from app.services.pubsub_listener import stop_listener

    # Closing admission also signals the periodic jobs and the webhook worker to stop.
    owner.close_admission()
    await startup.bounded(stop_listener(), startup.CANCEL_SECONDS, "pubsub_listener")
    await owner.shutdown(drain_seconds=startup.DRAIN_SECONDS, cancel_seconds=startup.CANCEL_SECONDS)


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
