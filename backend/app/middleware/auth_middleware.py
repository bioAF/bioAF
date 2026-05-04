import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.auth_service import AuthService

# Endpoints that don't require authentication
PUBLIC_PATHS = {
    "/api/health/live",
    "/api/health/ready",
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
    # Docs paths pass through to FastAPI which returns 404 in production
    # (docs_url=None) or serves Swagger UI in development.
    "/docs",
    "/openapi.json",
}


_FILE_CONTENT_RE = re.compile(r"^/api/files/\d+/content$")
_PLOT_THUMBNAIL_CONTENT_RE = re.compile(r"^/api/plots/\d+/thumbnail/content$")

# Internal callback endpoints authenticate via X-Internal-Token, not user JWT.
# The handler validates the header itself; the middleware just lets it through.
_INTERNAL_CALLBACK_RE = re.compile(r"^/api/internal/")


def _is_file_content_path(path: str) -> bool:
    """Return True for paths that accept content-token query-param auth."""
    return _FILE_CONTENT_RE.match(path) is not None or _PLOT_THUMBNAIL_CONTENT_RE.match(path) is not None


_RESOURCE_ID_RE = re.compile(r"/(\d+)/(?:content|thumbnail/content)$")


def _extract_resource_id(path: str) -> int | None:
    """Pull the numeric resource ID from a content path."""
    m = _RESOURCE_ID_RE.search(path)
    return int(m.group(1)) if m else None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        is_public = (
            path in PUBLIC_PATHS
            or (path.startswith("/api/v1/work-nodes/sessions/") and path.endswith("/heartbeat"))
            or _INTERNAL_CALLBACK_RE.match(path) is not None
        )

        # For public endpoints, still attempt to populate current_user if a
        # valid token is present so handlers can adjust their response for
        # authenticated vs unauthenticated callers.
        if is_public:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                try:
                    payload = AuthService.validate_token(auth_header.split(" ", 1)[1])
                    request.state.current_user = payload
                except Exception:
                    pass
            return await call_next(request)

        # Content paths (file/plot inline display) accept short-lived content
        # tokens in query params instead of full session JWTs. This prevents
        # the 24-hour session token from leaking into logs, referrer headers,
        # and browser history (pentest finding #5).
        if _is_file_content_path(path) and request.query_params.get("token"):
            from app.api.content_tokens import validate_content_token

            try:
                payload = validate_content_token(request.query_params["token"])
                # Verify the token is scoped to the requested resource
                resource_id = _extract_resource_id(path)
                if payload.get("resource_id") != resource_id:
                    return JSONResponse(status_code=401, content={"detail": "Token not valid for this resource"})
                request.state.current_user = payload
                return await call_next(request)
            except (ValueError, Exception):
                return JSONResponse(status_code=401, content={"detail": "Invalid or expired content token"})

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization")
        token: str | None = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})
        try:
            payload = AuthService.validate_token(token)
            request.state.current_user = payload
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        return await call_next(request)
