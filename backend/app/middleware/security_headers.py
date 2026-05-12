from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.config import settings

_CSP = "; ".join(
    [
        "default-src 'self'",
        # 'unsafe-eval' is required because the Nextflow HTML report embeds
        # Plotly, which JITs vector math via `new Function(...)`. The report
        # renders in a srcdoc iframe whose CSP is inherited from this page
        # per the HTML spec (sandbox doesn't change that), so without
        # unsafe-eval the report's plots and task table stay blank.
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _CSP

        if settings.ssl_enabled:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response
