"""Plan 03.5A executable proof: the gateway in front of a real Jupyter Server.

A scientist's browser path end to end, over real HTTP and WebSockets: Open (ticket exchange), start a
kernel, execute a Python cell, save a notebook and reopen it. Jupyter runs with its own token (native
authentication as defense in depth) and ``base_url=/s/{id}/``, as the protected launch configures it.

Runs only when ``BIOAF_TEST_JUPYTER_BIN`` names a ``jupyter`` executable (jupyter_server and ipykernel
are not backend dependencies), for example a scratch virtualenv's ``bin/jupyter``.
"""

import asyncio
import json
import os
import subprocess
import uuid
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
import websockets

from app.models.notebook_session import ComputeSession
from app.notebook_gateway.app import Gateway
from app.notebook_gateway.transport import DirectTransport
from app.services import notebook_access_grant as grants
from app.platform.notebook_jupyter_auth import GATEWAY_TOKEN_HEADER, JUPYTER_GATEWAY_CONFIG
from tests.test_notebook_gateway import _Adapter, _free_port, _serve

JUPYTER = os.environ.get("BIOAF_TEST_JUPYTER_BIN")
pytestmark = pytest.mark.skipif(not JUPYTER, reason="BIOAF_TEST_JUPYTER_BIN not set")

SECRET = "ide-secret-" + uuid.uuid4().hex


@pytest_asyncio.fixture
async def jupyter(tmp_path):
    port = _free_port()
    # As the protected launch configures it: the token from JUPYTER_TOKEN and the gateway config from
    # the session Secret on JUPYTER_CONFIG_PATH.
    config_dir = tmp_path / "bioaf-jupyter"
    config_dir.mkdir()
    (config_dir / "jupyter_server_config.py").write_text(JUPYTER_GATEWAY_CONFIG)
    root = tmp_path / "root"
    root.mkdir()
    assert JUPYTER is not None
    proc = subprocess.Popen(
        [
            JUPYTER,
            "server",
            "--no-browser",
            "--ServerApp.ip=127.0.0.1",
            f"--ServerApp.port={port}",
            "--ServerApp.base_url=/s/41/",
            f"--ServerApp.root_dir={root}",
        ],
        env={**os.environ, "JUPYTER_TOKEN": SECRET, "JUPYTER_CONFIG_PATH": str(config_dir)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    async with httpx.AsyncClient() as c:
        for _ in range(200):
            try:
                if (await c.get(f"http://127.0.0.1:{port}/s/41/api/status?token={SECRET}")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    yield port
    proc.terminate()
    proc.wait(10)


@pytest_asyncio.fixture
async def gateway(session, comp_bio_user, jupyter):
    from app.database import async_session_factory

    session.add(
        ComputeSession(
            id=41,
            user_id=comp_bio_user.id,
            organization_id=comp_bio_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=8,
            status="running",
            compute_job_ref="bioaf-notebook-41",
            notebook_access_secret=SECRET,
        )
    )
    await session.commit()
    gw = Gateway(
        session_factory=async_session_factory,
        adapter_provider=_Adapter,
        transport=DirectTransport(f"http://127.0.0.1:{jupyter}"),
        scheme="http",
    )
    server, task, port = await _serve(gw.app)
    yield port
    server.should_exit = True
    await task


@pytest.mark.asyncio
async def test_a_scientist_opens_jupyter_runs_python_saves_and_reopens(session, comp_bio_user, jupyter, gateway):
    with patch("app.services.notebook_access_grant.get_notebook_adapter", return_value=_Adapter()):
        ticket = (
            await grants.issue_ticket(
                session, session_id=41, user_id=comp_bio_user.id, org_id=comp_bio_user.organization_id
            )
        ).ticket
    await session.commit()

    origin = f"http://127.0.0.1:{gateway}"
    async with httpx.AsyncClient(base_url=origin, follow_redirects=False) as browser:
        entered = await browser.post("/_bioaf/enter", data={"ticket": ticket}, headers={"origin": "http://127.0.0.1"})
        assert entered.status_code == 303
        cookie_name, cookie_value = entered.headers["set-cookie"].split(";")[0].split("=", 1)
        browser.cookies.set(cookie_name, cookie_value)

        lab = await browser.get("/s/41/api/status")
        assert lab.status_code == 200

        kernel = await browser.post("/s/41/api/kernels", json={"name": "python3"}, headers={"origin": origin})
        assert kernel.status_code == 201, kernel.text
        kernel_id = kernel.json()["id"]

        ws_url = f"ws://127.0.0.1:{gateway}/s/41/api/kernels/{kernel_id}/channels"
        async with websockets.connect(
            ws_url, additional_headers={"Origin": origin, "Cookie": f"{cookie_name}={cookie_value}"}
        ) as ws:
            msg_id = uuid.uuid4().hex
            await ws.send(
                json.dumps(
                    {
                        "header": {
                            "msg_id": msg_id,
                            "msg_type": "execute_request",
                            "username": "",
                            "session": uuid.uuid4().hex,
                            "version": "5.3",
                        },
                        "parent_header": {},
                        "metadata": {},
                        "content": {"code": "import os\nprint(6 * 7, os.getuid() != 0)", "silent": False},
                        "channel": "shell",
                    }
                )
            )
            output = None
            async with asyncio.timeout(60):
                while output is None:
                    reply = json.loads(await ws.recv())
                    if reply.get("msg_type") == "stream" and reply["parent_header"].get("msg_id") == msg_id:
                        output = reply["content"]["text"]
        assert output.strip() == "42 True"

        notebook = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        saved = await browser.put(
            "/s/41/api/contents/analysis.ipynb",
            json={"type": "notebook", "content": notebook},
            headers={"origin": origin},
        )
        assert saved.status_code in (200, 201), saved.text
        reopened = await browser.get("/s/41/api/contents/analysis.ipynb")
        assert reopened.json()["content"]["nbformat"] == 4

    # Defense in depth: the IDE refuses anyone without its own secret, even on its own address.
    async with httpx.AsyncClient() as direct:
        assert (await direct.get(f"http://127.0.0.1:{jupyter}/s/41/api/contents")).status_code == 403


@pytest.mark.asyncio
async def test_jupyter_accepts_its_token_from_the_gateway_header_and_nothing_less(jupyter):
    base = f"http://127.0.0.1:{jupyter}/s/41/api/contents"

    async def get(headers):
        # A fresh client each time: Jupyter sets a login cookie after a good token.
        async with httpx.AsyncClient() as c:
            return await c.get(base, headers=headers)

    with_header = await get({GATEWAY_TOKEN_HEADER: SECRET})
    wrong = await get({GATEWAY_TOKEN_HEADER: "not-the-token"})
    none = await get({})
    assert with_header.status_code == 200
    assert wrong.status_code == 403 and none.status_code == 403
