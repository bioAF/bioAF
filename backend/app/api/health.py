from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def liveness():
    return {"status": "ok"}


@router.get("/ready")
async def readiness(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}


@router.get("/status")
async def system_status(session: AsyncSession = Depends(get_session)):
    checks = {}

    # Database check
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "healthy"}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}

    # Overall status
    all_healthy = all(c["status"] == "healthy" for c in checks.values())
    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": checks,
    }


@router.get("/services")
async def service_health():
    """Return per-service health based on request success rates over the last 5 minutes.

    Services with >75% success are healthy, 50-74% are degraded,
    <50% are unhealthy. Services with no recent traffic are unknown.
    """
    from app.services.request_health import get_service_health

    health = get_service_health()
    services = [{"name": name, "status": status} for name, status in sorted(health.items())]
    return {"services": services}
