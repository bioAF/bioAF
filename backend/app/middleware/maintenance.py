"""Answer every API request with 503 while the upgrade gate is closed (plan 9, section 3).

The lifespan sets ``app.state.maintenance`` to app.schema_upgrade.compatibility()'s verdict when the
database is not at this code's completed head. Health probes still pass through: liveness answers,
readiness reports the same verdict. The body carries the reason and the upgrade record's public
fields so the UI can say what is happening instead of "unreachable".
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

RETRY_AFTER_SECONDS = "30"

IN_PROGRESS_TEXT = "bioAF is finishing an upgrade and is not accepting requests yet."
FAILED_TEXT = "bioAF\u2019s last upgrade failed. An administrator needs to review the failure and retry."


def maintenance_body(gate: dict) -> dict:
    return {
        "status": "maintenance",
        "detail": FAILED_TEXT if gate.get("reason") == "upgrade_failed" else IN_PROGRESS_TEXT,
        "reason": gate.get("reason"),
        "revision": gate.get("revision"),
        "target": gate.get("target"),
        "upgrade": gate.get("upgrade") or {},
    }


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        gate = getattr(request.app.state, "maintenance", None)
        path = request.url.path
        if gate and path.startswith("/api") and not path.startswith("/api/health"):
            return JSONResponse(
                status_code=503, content=maintenance_body(gate), headers={"Retry-After": RETRY_AFTER_SECONDS}
            )
        return await call_next(request)
