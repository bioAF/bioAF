from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.auth_service import AuthService

# Endpoints that don't require authentication
PUBLIC_PATHS = {
    "/api/health/live",
    "/api/health/ready",
    "/api/health/status",
    "/api/auth/login",
    "/api/auth/verify-email",
    "/api/auth/request-reset",
    "/api/auth/reset-password",
    "/api/bootstrap/status",
    "/api/bootstrap/create-admin",
    "/api/users/accept-invite",
    "/api/notifications/slack/callback",
    "/docs",
    "/openapi.json",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for public endpoints
        if (
            path in PUBLIC_PATHS
            or path.startswith("/api/health")
            or (path.startswith("/api/v1/work-nodes/sessions/") and path.endswith("/heartbeat"))
        ):
            return await call_next(request)

        # Extract token from Authorization header or query parameter
        auth_header = request.headers.get("Authorization")
        token: str | None = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
        elif request.query_params.get("token"):
            token = request.query_params["token"]

        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})
        try:
            payload = AuthService.validate_token(token)
            request.state.current_user = payload
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        return await call_next(request)
