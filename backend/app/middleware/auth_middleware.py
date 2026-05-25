import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.auth_service import AuthService
from app.services.api_key_service import KEY_PREFIX as API_KEY_PREFIX

# Endpoints that don't require authentication
PUBLIC_PATHS = {
    "/api/health/live",
    "/api/health/ready",
    "/api/auth/login",
    "/api/auth/verify-email",
    "/api/auth/request-reset",
    "/api/auth/reset-password",
    "/api/auth/reset-password/validate",
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
    # Public Integration API OpenAPI document and docs UI (ADR-048).
    # The schema is fetchable without a key; the operations themselves still
    # require authentication.
    "/api/v1/integrations/openapi.json",
    "/api/v1/integrations/docs",
    "/api/v1/integrations/docs/oauth2-redirect",
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

        # API key path: biokey_<prefix>.<secret>. Looks up the row, sets
        # current_user with the SA identity plus the key's scopes and id, and
        # debounces a last_used_at write. JWT path is unchanged.
        if token.startswith(API_KEY_PREFIX):
            from app import database as database_module
            from app.services import api_key_service, role_service
            from sqlalchemy import select
            from app.models.user import User
            from app.models.role import Role

            async with database_module.async_session_factory() as ak_session:
                key = await api_key_service.verify(ak_session, token)
                if key is None:
                    return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
                user_result = await ak_session.execute(select(User).where(User.id == key.service_account_user_id))
                sa_user = user_result.scalar_one_or_none()
                if sa_user is None or sa_user.status != "active" or not sa_user.is_service_account:
                    return JSONResponse(status_code=401, content={"detail": "Service account inactive"})
                role_result = await ak_session.execute(select(Role).where(Role.id == sa_user.role_id))
                role = role_result.scalar_one_or_none()
                role_name = role.name if role is not None else ""
                # Warm the permission cache for this role; cheap and avoids a
                # later miss inside require_permission.
                await role_service.get_permissions_for_role(ak_session, sa_user.role_id)
                await api_key_service.touch_last_used(ak_session, key.id)
                await ak_session.commit()

            request.state.current_user = {
                "sub": sa_user.id,
                "email": sa_user.email,
                "role_id": sa_user.role_id,
                "role_name": role_name,
                "org_id": sa_user.organization_id,
                "scopes": list(key.scopes or []),
                "api_key_id": key.id,
            }
            return await call_next(request)

        try:
            payload = AuthService.validate_token(token)
            # JWT callers have no per-key scope envelope; mark explicitly.
            payload.setdefault("scopes", None)
            payload.setdefault("api_key_id", None)
            request.state.current_user = payload
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        return await call_next(request)
