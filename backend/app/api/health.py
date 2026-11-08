import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.middleware.maintenance import maintenance_body

logger = logging.getLogger("bioaf.health")

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def liveness():
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request, session: AsyncSession = Depends(get_session)):
    """200 only when this backend serves normally: the upgrade gate is open and the database answers.

    503 while the gate is closed (upgrade running, failed or not yet validated: see
    app.schema_upgrade) and when the database does not answer. Liveness stays separate.
    """
    gate = getattr(request.app.state, "maintenance", None)
    if gate:
        return JSONResponse(status_code=503, content=maintenance_body(gate), headers={"Retry-After": "30"})
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})


@router.get("/status")
async def system_status(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    checks = {}

    # Database check
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "healthy"}
    except Exception:
        logger.exception("Database health check failed")
        checks["database"] = {"status": "unhealthy", "error": "unavailable"}

    # Overall status
    all_healthy = all(c["status"] == "healthy" for c in checks.values())
    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": checks,
    }


@router.get("/services")
async def service_health(current_user: dict = require_permission("infrastructure", "view")):
    """Return per-service health based on request success rates over the last 5 minutes.

    Services with >75% success are healthy, 50-74% are degraded,
    <50% are unhealthy. Services with no recent traffic are unknown.
    """
    from app.services.request_health import get_service_health

    health = get_service_health()
    services = [{"name": name, "status": status} for name, status in sorted(health.items())]
    return {"services": services}
