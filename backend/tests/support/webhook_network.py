"""A controlled network for the webhook transport (app/services/webhook_transport.py).

Installed for every test by the autouse ``webhook_network`` fixture in conftest.py, so no webhook
attempt resolves or dials the real network. Hostnames under ``.example`` resolve to one public
address by default (the subscription URLs throughout the suite use them); anything else must be
configured. Every dial is recorded with the numeric address the transport asked for, and refused
unless a test routes that address to a local server:

    webhook_network.answers["hooks.example"] = ["93.184.216.34"]
    webhook_network.routes[("93.184.216.34", 8443)] = ("127.0.0.1", server_port)

``resolved`` and ``dials`` record what was attempted, which is how a test proves a refused
destination was never dialed.
"""

from __future__ import annotations

import socket

import httpcore

DEFAULT_PUBLIC_ANSWER = "93.184.216.34"


class WebhookNetwork:
    def __init__(self) -> None:
        self.answers: dict[str, list[str] | BaseException | list] = {}
        self.routes: dict[tuple[str, int], tuple[str, int]] = {}
        self.resolved: list[str] = []
        self.dials: list[tuple[str, int]] = []

    async def resolve(self, host: str, port: int) -> list[str]:
        self.resolved.append(host)
        answer = self.answers.get(host)
        if answer is None and host.endswith(".example"):
            answer = [DEFAULT_PUBLIC_ANSWER]
        if answer is None:
            raise socket.gaierror(f"tests do not resolve {host}")
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return list(answer())
        return list(answer)

    def backend(self) -> httpcore.AsyncNetworkBackend:
        return _RecordingBackend(self)


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, network: WebhookNetwork) -> None:
        self._network = network
        self._real = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self._network.dials.append((host, port))
        route = self._network.routes.get((host, port))
        if route is None:
            raise httpcore.ConnectError(f"tests do not dial {host}:{port}")
        return await self._real.connect_tcp(route[0], route[1], timeout=timeout)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("tests do not dial unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._real.sleep(seconds)
