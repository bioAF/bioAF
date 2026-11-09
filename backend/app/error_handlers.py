"""Central FastAPI exception handlers.

Keeps the framework glue (FastAPI ``Request``/``JSONResponse``) out of the
framework-free :mod:`app.exceptions` module while giving both the main app and
the mounted integration sub-app one place to register identical handling.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import DomainError
from app.runtime import OwnerClosed


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Map any :class:`DomainError` to its declared status code and envelope."""
    if exc.plain_body:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    content: dict = {"detail": str(exc), "code": exc.code}
    details = getattr(exc, "details", None)
    if details:
        content["details"] = details
    return JSONResponse(status_code=exc.status_code, content=content)


async def owner_closed_handler(request: Request, exc: OwnerClosed) -> JSONResponse:
    """A job refused because the backend is shutting down: nothing was written; retry shortly."""
    return JSONResponse(
        status_code=503,
        content={"detail": "bioAF is restarting and cannot start this now. Try again in a moment."},
        headers={"Retry-After": "30"},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register the shared domain-error handler on ``app``.

    A mounted Starlette sub-app does not inherit the parent's exception
    handlers, so this is called once per app (main + integrations).
    """
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(OwnerClosed, owner_closed_handler)
