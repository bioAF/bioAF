"""Bind the application's task owner around each request (plan 14).

Pure ASGI, outermost, so everything the request runs (routes of the mounted integration app,
streaming bodies, response background tasks) sees the owner the lifespan installed on
``app.state.runtime``. An application without one (tests with a no-op lifespan) serves unbound.
"""

import asyncio

from starlette.types import ASGIApp, Receive, Scope, Send

from app import runtime


class TaskOwnerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        owner = getattr(scope["app"].state, "runtime", None)
        with runtime.bound(owner):
            try:
                await self.app(scope, receive, send)
            finally:
                # An external operation this request admitted ends with the request.
                if owner is not None:
                    owner.end_operations(asyncio.current_task())
