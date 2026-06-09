"""Central FastAPI exception handlers.

Keeps the framework glue (FastAPI ``Request``/``JSONResponse``) out of the
framework-free :mod:`app.exceptions` module while giving both the main app and
the mounted integration sub-app one place to register identical handling.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import DomainError


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Map any :class:`DomainError` to its declared status code and envelope."""
    content: dict = {"detail": str(exc), "code": exc.code}
    details = getattr(exc, "details", None)
    if details:
        content["details"] = details
    return JSONResponse(status_code=exc.status_code, content=content)


def register_error_handlers(app: FastAPI) -> None:
    """Register the shared domain-error handler on ``app``.

    A mounted Starlette sub-app does not inherit the parent's exception
    handlers, so this is called once per app (main + integrations).
    """
    app.add_exception_handler(DomainError, domain_error_handler)
