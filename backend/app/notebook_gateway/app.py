"""The protected notebook gateway (plan 03.5A): its own ASGI app on its own origin (``:8443``).

Routes:

- ``POST /_bioaf/enter``: exchanges a single-use ticket (form body, never a URL) for a session-scoped
  ``HttpOnly; Secure; SameSite=Strict`` cookie, then redirects to the IDE. The request must come from
  the bioAF application origin (same host, default HTTPS port).
- ``/s/{id}/...`` (HTTP and WebSocket): proxied to that session's IDE after the per-request check
  (``access.current_access``). Unsafe methods and every WebSocket must carry this gateway's Origin.

Nothing else is served. The target comes only from the grant; browser credentials (cookies for the
gateway, Authorization, Origin) never reach the IDE, and transport credentials never reach the browser.
Refusals never stop a session or delete files.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode

import httpx
import websockets
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from app import runtime
from app.notebook_gateway.access import PodConfirmations, current_access
from app.notebook_gateway.transport import NotebookTransport, Upstream, ide_path
from app.services import notebook_access_grant as grants
from app.platform.notebook_jupyter_auth import GATEWAY_TOKEN_HEADER

logger = logging.getLogger("bioaf.notebook_gateway")

COOKIE_PREFIX = "__Host-bioaf_nb_"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_WEBSOCKETS_PER_SESSION = 32
RECHECK_SECONDS = 60.0
MAX_REWRITTEN_BODY = 10 * 1024 * 1024
MAX_TICKET_BODY = 4096
CONNECT_TIMEOUT = 30.0

_FORWARD_REQUEST_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "cache-control",
    "content-length",
    "content-type",
    "if-match",
    "if-modified-since",
    "if-none-match",
    "range",
    "user-agent",
    "x-requested-with",
    "x-xsrftoken",
    "x-rs-csrf-token",
}
_DROP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "set-cookie",
    "location",
    "content-length",
}

# Gateway path ``/s/{id}/{rest}``; ``rest`` may not climb out of the session (checked on the raw path).
_UNSAFE_PATH = re.compile(r"(^|/)\.\.?(/|$)|%2e|%2f|%5c|\\", re.IGNORECASE)

REFUSED_SIGN_IN = "Open this session from bioAF again."
REFUSED_ACCESS = "This session cannot be opened here. Open it from bioAF."


def _upstream_cookies(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    kept = [c.strip() for c in cookie_header.split(";") if c.strip() and not c.strip().startswith(COOKIE_PREFIX)]
    return "; ".join(kept) or None


def _scoped_set_cookie(value: str, session_id: int, secure: bool) -> str:
    """An IDE cookie confined to its session's path on the gateway host (no Domain)."""
    parts = [p.strip() for p in value.split(";")]
    kept = [parts[0]] + [p for p in parts[1:] if p and p.split("=", 1)[0].strip().lower() not in ("domain", "path")]
    kept.append(f"Path=/s/{session_id}/")
    if secure and not any(p.lower() == "secure" for p in kept):
        kept.append("Secure")
    return "; ".join(kept)


def _rewrite(text: str, prefix: str) -> str:
    return text.replace(prefix, "") if prefix else text


class Gateway:
    def __init__(
        self,
        *,
        session_factory,
        adapter_provider,
        transport: NotebookTransport,
        scheme: str = "https",
        recheck_seconds: float = RECHECK_SECONDS,
        max_websockets: int = MAX_WEBSOCKETS_PER_SESSION,
    ):
        self._factory = session_factory
        self._adapter_provider = adapter_provider
        self._transport = transport
        self._scheme = scheme
        self._recheck = recheck_seconds
        self._max_ws = max_websockets
        self._confirmations = PodConfirmations()
        self._open_ws: dict[int, int] = defaultdict(int)
        self.app = Starlette(
            routes=[
                Route("/_bioaf/enter", self.enter, methods=["POST"]),
                Route(
                    "/s/{sid:int}/{rest:path}",
                    self.http,
                    methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
                ),
                WebSocketRoute("/s/{sid:int}/{rest:path}", self.websocket),
            ]
        )

    # -- Origins -----------------------------------------------------------------------------

    def _gateway_origin(self, host: str) -> str:
        return f"{self._scheme}://{host}"

    def _app_origin(self, host: str) -> str:
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        return f"{self._scheme}://{hostname}"

    # -- Access ------------------------------------------------------------------------------

    async def _authorize(self, sid: int, cookies: dict, raw_path: bytes):
        if _UNSAFE_PATH.search(raw_path.decode("latin-1").split("?", 1)[0][len(f"/s/{sid}/") :]):
            return None, PlainTextResponse(REFUSED_ACCESS, status_code=400)
        grant = grants.read_access_credential(cookies.get(f"{COOKIE_PREFIX}{sid}"), session_id=sid)
        if grant is None:
            return None, PlainTextResponse(REFUSED_SIGN_IN, status_code=401)
        access = await current_access(self._factory, self._adapter_provider(), grant, self._confirmations)
        if access is None:
            return None, PlainTextResponse(REFUSED_ACCESS, status_code=403)
        return (grant, access), None

    @staticmethod
    def _query(query_string: str) -> str:
        # A browser-supplied token never reaches the IDE.
        params = [
            (k, v) for k, vs in parse_qs(query_string, keep_blank_values=True).items() for v in vs if k != "token"
        ]
        return urlencode(params)

    @staticmethod
    def _ide_headers(access) -> dict:
        """The IDE's own secret, as a header (never in the upstream URL; see notebook_jupyter_auth)."""
        if access.session_type == "jupyter" and access.ide_secret:
            return {GATEWAY_TOKEN_HEADER: access.ide_secret}
        return {}

    # -- Enter -------------------------------------------------------------------------------

    async def enter(self, request: Request) -> Response:
        host = request.headers.get("host", "")
        if request.headers.get("origin") != self._app_origin(host):
            return PlainTextResponse(REFUSED_SIGN_IN, status_code=403)
        # Read as it arrives and stop at the limit: this route is unauthenticated, so a large body must
        # never be buffered first.
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_TICKET_BODY:
                return PlainTextResponse(REFUSED_SIGN_IN, status_code=413)
        ticket = (parse_qs(body.decode("utf-8", "replace")).get("ticket") or [""])[0]
        async with self._factory() as session:
            grant = await grants.consume_ticket(session, ticket)
            await session.commit()
        if grant is None:
            return PlainTextResponse(REFUSED_SIGN_IN, status_code=403)
        access = await current_access(self._factory, self._adapter_provider(), grant, self._confirmations)
        if access is None:
            return PlainTextResponse(REFUSED_ACCESS, status_code=403)
        landing = f"/s/{grant.session_id}/lab" if access.session_type == "jupyter" else f"/s/{grant.session_id}/"
        response = RedirectResponse(landing, status_code=303)
        response.set_cookie(
            f"{COOKIE_PREFIX}{grant.session_id}",
            grants.access_credential(grant),
            max_age=int(grants.ACCESS_LIFETIME.total_seconds()),
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response

    # -- HTTP --------------------------------------------------------------------------------

    async def http(self, request: Request) -> Response:
        sid = request.path_params["sid"]
        host = request.headers.get("host", "")
        if request.method not in SAFE_METHODS and request.headers.get("origin") != self._gateway_origin(host):
            return PlainTextResponse(REFUSED_ACCESS, status_code=403)
        ok, refused = await self._authorize(sid, request.cookies, request.scope.get("raw_path") or b"")
        if refused:
            return refused
        grant, access = ok
        upstream: Upstream = await self._transport.upstream(grant, access.session_type)
        path = ide_path(access.session_type, sid, request.path_params["rest"])
        query = self._query(request.url.query)
        headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQUEST_HEADERS}
        cookie = _upstream_cookies(request.headers.get("cookie"))
        if cookie:
            headers["cookie"] = cookie
        headers.update(self._ide_headers(access))
        headers.update(upstream.headers)
        client = httpx.AsyncClient(verify=upstream.verify, timeout=httpx.Timeout(CONNECT_TIMEOUT, read=None))
        try:
            upstream_request = client.build_request(
                request.method,
                upstream.http_base + path + (f"?{query}" if query else ""),
                headers=headers,
                content=request.stream() if request.method not in ("GET", "HEAD") else None,
            )
            reply = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning("Notebook session %s: upstream unavailable (%s)", sid, type(exc).__name__)
            return PlainTextResponse("This session is not responding. Try again shortly.", status_code=502)

        out_headers = [(k, v) for k, v in reply.headers.multi_items() if k.lower() not in _DROP_RESPONSE_HEADERS]
        for value in reply.headers.get_list("set-cookie"):
            out_headers.append(("set-cookie", _scoped_set_cookie(value, sid, self._scheme == "https")))
        location = reply.headers.get("location")
        if location:
            out_headers.append(("location", _rewrite(location, upstream.rewrite_prefix)))
        out_headers.append(("referrer-policy", "same-origin"))
        out_headers.append(("x-content-type-options", "nosniff"))
        # The bioAF tab that opened this one keeps no handle on it, and it none on bioAF.
        out_headers = [(k, v) for k, v in out_headers if k.lower() != "cross-origin-opener-policy"]
        out_headers.append(("cross-origin-opener-policy", "same-origin"))

        if upstream.rewrite_prefix and reply.headers.get("content-type", "").startswith("text/html"):
            body = b""
            async for chunk in reply.aiter_bytes():
                body += chunk
                if len(body) > MAX_REWRITTEN_BODY:
                    break
            await reply.aclose()
            await client.aclose()
            text = _rewrite(body.decode("utf-8", "replace"), upstream.rewrite_prefix)
            out_headers = [(k, v) for k, v in out_headers if k.lower() != "content-encoding"]
            response = Response(text.encode(), status_code=reply.status_code)
        else:

            async def stream():
                try:
                    async for chunk in reply.aiter_raw():
                        yield chunk
                finally:
                    await reply.aclose()
                    await client.aclose()

            response = StreamingResponse(stream(), status_code=reply.status_code)
        for k, v in out_headers:
            response.headers.append(k, v)
        return response

    # -- WebSocket ---------------------------------------------------------------------------

    async def websocket(self, ws: WebSocket) -> None:
        sid = ws.path_params["sid"]
        host = ws.headers.get("host", "")
        if ws.headers.get("origin") != self._gateway_origin(host):
            await ws.close(code=1008)
            return
        ok, refused = await self._authorize(sid, ws.cookies, ws.scope.get("raw_path") or b"")
        if refused:
            await ws.close(code=1008)
            return
        grant, access = ok
        if self._open_ws[sid] >= self._max_ws:
            await ws.close(code=1013)
            return
        upstream = await self._transport.upstream(grant, access.session_type)
        path = ide_path(access.session_type, sid, ws.path_params["rest"])
        query = self._query(ws.url.query)
        url = upstream.ws_base + path + (f"?{query}" if query else "")
        headers = {**self._ide_headers(access), **upstream.headers}
        cookie = _upstream_cookies(ws.headers.get("cookie"))
        if cookie:
            headers["Cookie"] = cookie
        offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
        ssl_context = upstream.verify if url.startswith("wss://") and not isinstance(upstream.verify, bool) else None
        self._open_ws[sid] += 1
        try:
            try:
                remote = await websockets.connect(
                    url,
                    additional_headers=headers,
                    subprotocols=offered or None,
                    ssl=ssl_context,
                    open_timeout=CONNECT_TIMEOUT,
                    max_size=64 * 1024 * 1024,
                    compression=None,
                )
            except Exception as exc:
                logger.warning("Notebook session %s: upstream WebSocket failed (%s)", sid, type(exc).__name__)
                await ws.close(code=1011)
                return
            await ws.accept(subprotocol=remote.subprotocol)
            await self._pump(ws, remote, grant)
        finally:
            self._open_ws[sid] -= 1

    async def _pump(self, ws: WebSocket, remote, grant) -> None:
        async def browser_to_ide():
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await remote.send(message["bytes"])
                elif message.get("text") is not None:
                    await remote.send(message["text"])

        async def ide_to_browser():
            async for message in remote:
                if isinstance(message, bytes):
                    await ws.send_bytes(message)
                else:
                    await ws.send_text(message)

        async def still_allowed():
            # Rechecked periodically (sign-out, a stopped session, a replaced Pod) and always at the
            # credential's own expiry.
            while True:
                wait = self._recheck
                if grant.expires_at is not None:
                    remaining = (grant.expires_at - datetime.now(timezone.utc)).total_seconds()
                    wait = min(wait, max(remaining, 0.0))
                await asyncio.sleep(wait)
                access = await current_access(self._factory, self._adapter_provider(), grant, self._confirmations)
                if access is None:
                    return

        tasks = [
            runtime.spawn(t(), role="notebook-gateway-socket") for t in (browser_to_ide, ide_to_browser, still_allowed)
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await remote.close()
            try:
                await ws.close(code=1008)
            except RuntimeError:
                pass


def create_app():
    """The production gateway process: its own adapter registry, the Kubernetes Pod proxy as transport.

    Run with ``uvicorn app.notebook_gateway.app:create_app --factory`` (docker-compose.yml). It serves
    no bioAF API route and runs no database upgrade.
    """
    from contextlib import asynccontextmanager

    from app.adapters.registry import get_notebook_adapter, initialize_adapters
    from app.database import async_session_factory
    from app.notebook_gateway.transport import KubernetesPodProxyTransport

    gateway = Gateway(
        session_factory=async_session_factory,
        adapter_provider=get_notebook_adapter,
        transport=KubernetesPodProxyTransport(get_notebook_adapter),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with async_session_factory() as session:
            await initialize_adapters(session, session_factory=async_session_factory)
        logger.info("Notebook gateway ready")
        yield

    gateway.app.router.lifespan_context = lifespan
    return gateway.app
