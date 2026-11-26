"""Plan 03.5A: the notebook gateway, served for real (uvicorn) in front of a real upstream server.

The upstream stands in for the IDE and echoes what it receives, so these prove what the gateway lets
through, what it strips and what it refuses, over real HTTP and WebSocket connections. A real Jupyter
Server proof is in tests/integration/test_notebook_gateway_jupyter.py.
"""

import asyncio
import json
import socket
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
import uvicorn
import websockets
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute

from app.models.notebook_session import ComputeSession
from app.notebook_gateway.app import COOKIE_PREFIX, Gateway
from app.notebook_gateway.transport import DirectTransport, Upstream
from app.services import notebook_access_grant as grants

APISERVER_PREFIX = "/api/v1/namespaces/bioaf-notebooks/pods/bioaf-notebook-41:8888/proxy"


# -- A real upstream ----------------------------------------------------------------------------


async def _echo(request: Request):
    body = await request.body()
    return JSONResponse(
        {
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "headers": {k: v for k, v in request.headers.items()},
            "body": body.decode(),
        }
    )


async def _page(request: Request):
    return Response(
        f'<script src="{APISERVER_PREFIX}/s/41/static/app.js"></script>', media_type="text/html; charset=utf-8"
    )


async def _redirect(request: Request):
    return Response(status_code=302, headers={"location": f"{APISERVER_PREFIX}/s/41/lab"})


async def _cookie(request: Request):
    response = PlainTextResponse("ok")
    response.headers.append("set-cookie", "rs-csrf=abc; Domain=evil.example; Path=/; HttpOnly")
    return response


async def _ws(ws):
    await ws.accept(subprotocol=(ws.scope.get("subprotocols") or [None])[0])
    await ws.send_text(
        json.dumps({"path": ws.url.path, "query": dict(ws.query_params), "cookie": ws.headers.get("cookie")})
    )
    while True:
        try:
            message = await ws.receive_text()
        except Exception:
            return
        await ws.send_text("echo:" + message)


upstream_app = Starlette(
    routes=[
        Route("/s/41/page", _page),
        Route("/s/41/redirect", _redirect),
        Route("/s/41/cookie", _cookie),
        Route("/{path:path}", _echo, methods=["GET", "POST", "PUT", "DELETE"]),
        WebSocketRoute("/s/41/ws", _ws),
    ]
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve(app):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task, port


# -- Fixtures -----------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def nb(session, comp_bio_user):
    row = ComputeSession(
        id=41,
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="running",
        compute_job_ref="bioaf-notebook-41",
        provider_metadata={"pod_name": "bioaf-notebook-41", "namespace": "bioaf-notebooks"},
        notebook_access_secret="ide-secret-41",
    )
    session.add(row)
    await session.commit()
    return row


class _Adapter:
    def __init__(self):
        self.uid = "uid-1"

    def resolve_workload(self, ns):
        return MagicMock(session_id=ns.id, namespace="bioaf-notebooks", pod_name="bioaf-notebook-41"), None

    async def live_pod_uid(self, workload):
        return self.uid


class _RewritingTransport(DirectTransport):
    async def upstream(self, grant, session_type):
        base = await super().upstream(grant, session_type)
        return Upstream(http_base=base.http_base, ws_base=base.ws_base, rewrite_prefix=APISERVER_PREFIX)


@pytest_asyncio.fixture
async def stack(nb):
    from app.database import async_session_factory

    up_server, up_task, up_port = await _serve(upstream_app)
    adapter = _Adapter()
    gateway = Gateway(
        session_factory=async_session_factory,
        adapter_provider=lambda: adapter,
        transport=_RewritingTransport(f"http://127.0.0.1:{up_port}"),
        scheme="http",
        recheck_seconds=0.2,
        max_websockets=2,
    )
    gw_server, gw_task, gw_port = await _serve(gateway.app)
    yield {
        "port": gw_port,
        "adapter": adapter,
        "origin": f"http://127.0.0.1:{gw_port}",
        "app_origin": "http://127.0.0.1",
    }
    for server, task in ((gw_server, gw_task), (up_server, up_task)):
        server.should_exit = True
        await task


def _cookie_for(nb, **over):
    values = dict(
        user_id=nb.user_id,
        organization_id=nb.organization_id,
        session_id=41,
        pod_uid="uid-1",
        pod_name="bioaf-notebook-41",
        namespace="bioaf-notebooks",
    )
    values.update(over)
    return {f"{COOKIE_PREFIX}41": grants.access_credential(grants.AccessGrant(**values))}


def _client(stack, cookies=None):
    return httpx.AsyncClient(base_url=stack["origin"], cookies=cookies or {}, follow_redirects=False)


# -- Enter --------------------------------------------------------------------------------------


async def _ticket(session, nb):
    from unittest.mock import patch

    with patch("app.services.notebook_access_grant.get_notebook_adapter", return_value=_Adapter()):
        issued = await grants.issue_ticket(session, session_id=nb.id, user_id=nb.user_id, org_id=nb.organization_id)
    await session.commit()
    return issued.ticket


@pytest.mark.asyncio
async def test_a_ticket_from_the_app_origin_opens_the_session_once(session, nb, stack):
    ticket = await _ticket(session, nb)
    async with _client(stack) as c:
        first = await c.post("/_bioaf/enter", data={"ticket": ticket}, headers={"origin": stack["app_origin"]})
        replay = await c.post("/_bioaf/enter", data={"ticket": ticket}, headers={"origin": stack["app_origin"]})
    assert first.status_code == 303
    assert first.headers["location"] == "/s/41/lab"
    cookie = first.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_PREFIX}41=")
    for attribute in ("HttpOnly", "Secure", "Path=/", "SameSite=strict"):
        assert attribute.lower() in cookie.lower()
    assert "domain" not in cookie.lower()
    assert ticket not in first.text and ticket not in first.headers["location"]
    assert replay.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "http://evil.example", "http://127.0.0.1:9999"])
async def test_a_ticket_posted_from_anywhere_else_is_refused_and_not_spent(session, nb, stack, origin):
    ticket = await _ticket(session, nb)
    headers = {"origin": origin} if origin else {}
    async with _client(stack) as c:
        refused = await c.post("/_bioaf/enter", data={"ticket": ticket}, headers=headers)
        good = await c.post("/_bioaf/enter", data={"ticket": ticket}, headers={"origin": stack["app_origin"]})
    assert refused.status_code == 403
    assert good.status_code == 303


@pytest.mark.asyncio
async def test_a_ticket_cannot_be_sent_in_a_url(session, nb, stack):
    ticket = await _ticket(session, nb)
    async with _client(stack) as c:
        response = await c.get(f"/_bioaf/enter?ticket={ticket}", headers={"origin": stack["app_origin"]})
    assert response.status_code == 405


# -- HTTP ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_owner_reaches_the_ide_with_its_secret_added_and_browser_credentials_removed(nb, stack):
    cookies = {**_cookie_for(nb), "_xsrf": "x1"}
    async with _client(stack, cookies) as c:
        response = await c.get(
            "/s/41/api/contents?type=directory&token=attacker",
            headers={"authorization": "Bearer app-jwt", "x-forwarded-for": "1.2.3.4"},
        )
    assert response.status_code == 200
    seen = response.json()
    assert seen["path"] == "/s/41/api/contents"
    assert seen["query"] == {"type": "directory", "token": "ide-secret-41"}
    forwarded = seen["headers"]
    assert "authorization" not in forwarded and "x-forwarded-for" not in forwarded and "origin" not in forwarded
    assert forwarded.get("cookie") == "_xsrf=x1"
    assert COOKIE_PREFIX not in json.dumps(forwarded)


@pytest.mark.asyncio
async def test_unsafe_requests_need_the_gateway_origin(nb, stack):
    async with _client(stack, _cookie_for(nb)) as c:
        forged = await c.post("/s/41/api/kernels", content="{}", headers={"origin": "http://127.0.0.1"})
        missing = await c.post("/s/41/api/kernels", content="{}")
        ok = await c.post("/s/41/api/kernels", content='{"a":1}', headers={"origin": stack["origin"]})
    assert (forged.status_code, missing.status_code) == (403, 403)
    assert ok.status_code == 200 and ok.json()["body"] == '{"a":1}'


@pytest.mark.asyncio
async def test_without_a_credential_for_this_session_nothing_is_proxied(nb, stack):
    async with _client(stack) as anonymous:
        assert (await anonymous.get("/s/41/lab")).status_code == 401
    other_session = {
        f"{COOKIE_PREFIX}41": grants.access_credential(
            grants.AccessGrant(nb.user_id, nb.organization_id, 42, "uid-1", "p", "n")
        )
    }
    async with _client(stack, other_session) as c:
        assert (await c.get("/s/41/lab")).status_code == 401
    async with _client(stack, {f"{COOKIE_PREFIX}41": "not-a-token"}) as c:
        assert (await c.get("/s/41/lab")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "UPDATE compute_sessions SET status = 'stopped' WHERE id = 41",
        "UPDATE users SET status = 'inactive' WHERE id = (SELECT user_id FROM compute_sessions WHERE id = 41)",
        "UPDATE compute_sessions SET user_id = (SELECT id FROM users WHERE email = 'admin@test.com') WHERE id = 41",
    ],
)
async def test_access_ends_when_the_session_user_or_owner_changes(session, nb, stack, admin_user, change):
    await session.execute(text(change))
    await session.commit()
    async with _client(stack, _cookie_for(nb)) as c:
        assert (await c.get("/s/41/lab")).status_code == 403


@pytest.mark.asyncio
async def test_a_replaced_pod_is_refused(nb, stack):
    stack["adapter"].uid = "uid-2"
    gateway_cookie = _cookie_for(nb)
    async with _client(stack, gateway_cookie) as c:
        assert (await c.get("/s/41/lab")).status_code == 403


@pytest.mark.asyncio
async def test_a_credential_from_another_organization_is_refused(nb, stack):
    async with _client(stack, _cookie_for(nb, organization_id=nb.organization_id + 999)) as c:
        assert (await c.get("/s/41/lab")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/s/41/../42/lab", "/s/41/%2e%2e/42/lab", "/s/41/a%2f..%2f..%2fx", "/s/41/a/%2E%2E/%2E%2E/x"]
)
async def test_a_path_cannot_climb_out_of_the_session(nb, stack, path):
    async with _client(stack, _cookie_for(nb)) as c:
        response = await c.get(path)
    # Refused (or, when the client normalized the path first, a session this credential is not for).
    assert response.status_code in (400, 401, 404)
    assert '"method"' not in response.text


@pytest.mark.asyncio
async def test_nothing_outside_session_paths_is_served(nb, stack):
    async with _client(stack, _cookie_for(nb)) as c:
        for path in ("/", "/api/v1/notebooks/sessions", "/api/health/", "/s/notanumber/lab"):
            assert (await c.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_the_transports_own_paths_never_reach_the_browser(nb, stack):
    async with _client(stack, _cookie_for(nb)) as c:
        page = await c.get("/s/41/page")
        moved = await c.get("/s/41/redirect")
    assert APISERVER_PREFIX not in page.text and '"/s/41/static/app.js"' in page.text
    assert moved.headers["location"] == "/s/41/lab"


@pytest.mark.asyncio
async def test_ide_cookies_are_confined_to_their_session_path(nb, stack):
    async with _client(stack, _cookie_for(nb)) as c:
        response = await c.get("/s/41/cookie")
    cookie = response.headers["set-cookie"]
    assert "domain" not in cookie.lower()
    assert "Path=/s/41/" in cookie


# -- WebSocket ----------------------------------------------------------------------------------


def _ws_headers(stack, nb, origin=None, cookies=None):
    jar = cookies if cookies is not None else _cookie_for(nb)
    headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in {**jar, "_xsrf": "x1"}.items())}
    headers["Origin"] = origin or stack["origin"]
    return headers


@pytest.mark.asyncio
async def test_the_owner_gets_a_websocket_with_the_ide_secret_and_without_gateway_cookies(nb, stack):
    url = f"ws://127.0.0.1:{stack['port']}/s/41/ws?session_id=abc"
    async with websockets.connect(
        url, additional_headers=_ws_headers(stack, nb), subprotocols=["v1.kernel.websocket.jupyter.org"]
    ) as ws:
        hello = json.loads(await ws.recv())
        assert ws.subprotocol == "v1.kernel.websocket.jupyter.org"
        await ws.send("run")
        assert await ws.recv() == "echo:run"
    assert hello["path"] == "/s/41/ws"
    assert hello["query"] == {"session_id": "abc", "token": "ide-secret-41"}
    assert hello["cookie"] == "_xsrf=x1"


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["http://127.0.0.1", "http://evil.example"])
async def test_a_websocket_from_another_origin_is_refused(nb, stack, origin):
    url = f"ws://127.0.0.1:{stack['port']}/s/41/ws"
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(url, additional_headers=_ws_headers(stack, nb, origin=origin)):
            pass


@pytest.mark.asyncio
async def test_an_anonymous_websocket_is_refused(nb, stack):
    url = f"ws://127.0.0.1:{stack['port']}/s/41/ws"
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(url, additional_headers=_ws_headers(stack, nb, cookies={})):
            pass


@pytest.mark.asyncio
async def test_an_open_websocket_closes_when_access_ends(session, nb, stack):
    url = f"ws://127.0.0.1:{stack['port']}/s/41/ws"
    async with websockets.connect(url, additional_headers=_ws_headers(stack, nb)) as ws:
        await ws.recv()
        await session.execute(text("UPDATE compute_sessions SET status = 'stopped' WHERE id = 41"))
        await session.commit()
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            async with asyncio.timeout(5):
                while True:
                    await ws.recv()


@pytest.mark.asyncio
async def test_websockets_per_session_are_bounded(nb, stack):
    url = f"ws://127.0.0.1:{stack['port']}/s/41/ws"
    async with websockets.connect(url, additional_headers=_ws_headers(stack, nb)) as a:
        await a.recv()
        async with websockets.connect(url, additional_headers=_ws_headers(stack, nb)) as b:
            await b.recv()
            with pytest.raises((websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosed)):
                async with websockets.connect(url, additional_headers=_ws_headers(stack, nb)) as c:
                    await c.recv()
