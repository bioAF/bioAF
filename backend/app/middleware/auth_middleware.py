import re

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
    "/api/bootstrap/generate-setup-code",
    "/api/bootstrap/verify-setup-code",
    "/api/users/accept-invite",
    "/api/notifications/slack/callback",
    "/docs",
    "/openapi.json",
}


_FILE_CONTENT_RE = re.compile(r"^/api/files/\d+/content$")
_PLOT_THUMBNAIL_CONTENT_RE = re.compile(r"^/api/plots/\d+/thumbnail/content$")


def _is_file_content_path(path: str) -> bool:
    """Return True for paths that legitimately need query-param token auth."""
    return _FILE_CONTENT_RE.match(path) is not None or _PLOT_THUMBNAIL_CONTENT_RE.match(path) is not None


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

        # Extract token from Authorization header.
        # Query parameter tokens are only accepted on file content paths
        # (used by <img src> tags that cannot send Authorization headers).
        auth_header = request.headers.get("Authorization")
        token: str | None = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
        elif request.query_params.get("token") and _is_file_content_path(path):
            token = request.query_params["token"]

        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})
        try:
            payload = AuthService.validate_token(token)
            request.state.current_user = payload
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        return await call_next(request)
