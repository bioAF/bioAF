"""bioAF public Integration API sub-app (ADR-048).

Mounted at /api/v1/integrations from app/main.py. Owns its own OpenAPI doc
and /docs endpoint, which is served in production. Auth happens through the
parent app's AuthMiddleware (API-key path: Bearer biokey_<prefix>.<secret>).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1.integrations import experiments, files, projects, samples


def build_integrations_app() -> FastAPI:
    app = FastAPI(
        title="bioAF Integration API",
        version="1.0",
        description=(
            "Public, key-authenticated REST API for LIMS integrations. "
            "Authentication: `Authorization: Bearer biokey_<prefix>.<secret>`. "
            "All endpoints are scoped to the calling service account's organization. "
            "See ADR-048 (public surface), ADR-049 (auth), ADR-050 (idempotency), ADR-051 (webhooks)."
        ),
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    app.include_router(projects.router)
    app.include_router(experiments.router)
    app.include_router(samples.router)
    app.include_router(files.router)
    return app
