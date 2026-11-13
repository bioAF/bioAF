import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

logger = logging.getLogger("bioaf")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs method, path, status and duration only.

    No headers, query string or body: they carry credentials (Authorization, cookies, OAuth
    ``code``/``state``). If a header is ever needed, add it as a named field with a use case.
    """

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        logger.info("Request: %s %s", request.method, request.url.path)

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
