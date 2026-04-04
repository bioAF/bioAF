import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

logger = logging.getLogger("bioaf")

REDACT_FIELDS = {"password", "token", "secret", "key", "authorization", "cookie"}


def _redact_value(key: str, value: str) -> str:
    if any(field in key.lower() for field in REDACT_FIELDS):
        return "***REDACTED***"
    return value


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Log request (redact sensitive headers)
        safe_headers = {k: _redact_value(k, v) for k, v in request.headers.items()}
        logger.info(
            "Request: %s %s",
            request.method,
            request.url.path,
            extra={"headers": safe_headers},
        )

        response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            "Response: %s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        # Feed request health counters
        from app.services.request_health import record

        record(request.url.path, response.status_code)

        return response
