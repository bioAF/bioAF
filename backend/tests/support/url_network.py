"""A controlled network for the URL-import guard and downloader.

Installed for every test by the autouse ``url_network`` fixture in conftest.py,
so neither the guard's resolver nor the downloader's HTTP client can reach the
real network, even when the guard regresses. A test configures what it needs:

    url_network.answers["files.example"] = ["93.184.216.34"]
    url_network.routes["http://files.example/a.pdf"] = httpx.Response(200, content=b"%PDF")

An unconfigured host fails to resolve and an unconfigured URL fails to connect,
so anything a test did not plan for is a refusal, never an outbound request.
``resolved`` and ``requests`` record what was attempted, which is how a test
proves a denied destination was never requested.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import httpx

Route = httpx.Response | BaseException | Callable[[httpx.Request], httpx.Response]


class UrlNetwork:
    def __init__(self) -> None:
        self.answers: dict[str, list[str] | BaseException] = {}
        self.routes: dict[str, Route] = {}
        self.resolved: list[str] = []
        self.requests: list[str] = []

    def resolve(self, host: str, port: int) -> list:
        self.resolved.append(host)
        answer = self.answers.get(host)
        if answer is None:
            raise socket.gaierror(f"tests do not resolve {host}")
        if isinstance(answer, BaseException):
            raise answer
        return [_addrinfo(address, port) for address in answer]

    def open_client(self) -> httpx.AsyncClient:
        from app.services.lab_document_upload_service import URL_FETCH_TIMEOUT_SECONDS

        return httpx.AsyncClient(
            follow_redirects=False,
            timeout=URL_FETCH_TIMEOUT_SECONDS,
            transport=httpx.MockTransport(self._handle),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        route = self.routes.get(url)
        if route is None:
            raise httpx.ConnectError(f"tests do not fetch {url}", request=request)
        if isinstance(route, BaseException):
            raise route
        if callable(route):
            return route(request)
        return route


def _addrinfo(address: str, port: int) -> tuple:
    if ":" in address:
        return (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
