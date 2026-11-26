"""How the notebook gateway reaches a session's IDE (plan 03.5A).

The target is always derived from the grant (recorded namespace, Pod and session type), never from the
request. ``KubernetesPodProxyTransport`` goes through the Kubernetes API ``pods/{name}:{port}/proxy``
subresource with the backend's existing authenticated client (cluster CA verified, credentials
refreshed by the client); no Pod or Service address is reached directly.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from typing import Protocol

from app.services.notebook_access_grant import AccessGrant

IDE_PORTS = {"jupyter": 8888, "rstudio": 8787}


@dataclass(frozen=True)
class Upstream:
    # Origin + path prefix the IDE path is appended to, for HTTP and WebSocket.
    http_base: str
    ws_base: str
    # Credentials for the transport itself (the Kubernetes API), never shown to the browser.
    headers: dict = field(default_factory=dict)
    verify: ssl.SSLContext | bool = True
    # A prefix the transport may write into HTML and redirects (the API server's proxy path); the
    # gateway removes it so the browser stays on gateway paths.
    rewrite_prefix: str = ""


class NotebookTransport(Protocol):
    async def upstream(self, grant: AccessGrant, session_type: str) -> Upstream: ...


def ide_path(session_type: str, session_id: int, rest: str) -> str:
    """The path the IDE serves for gateway path ``/s/{id}/{rest}``.

    Jupyter runs with ``base_url=/s/{id}/`` so it sees the full path; RStudio runs with
    ``www-root-path=/s/{id}/`` and expects the prefix removed.
    """
    if session_type == "jupyter":
        return f"/s/{session_id}/{rest}"
    return f"/{rest}"


class DirectTransport:
    """A fixed upstream origin (tests and local proofs). Never used for a cloud installation."""

    def __init__(self, base: str):
        self._base = base.rstrip("/")

    async def upstream(self, grant: AccessGrant, session_type: str) -> Upstream:
        ws = self._base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return Upstream(http_base=self._base, ws_base=ws)


class KubernetesPodProxyTransport:
    """Through the Kubernetes API server's Pod proxy, with the notebook adapter's client."""

    def __init__(self, adapter_provider):
        self._adapter_provider = adapter_provider

    async def upstream(self, grant: AccessGrant, session_type: str) -> Upstream:
        api_client = await self._adapter_provider()._get_api_client_async()
        config = api_client.configuration
        host = config.host.rstrip("/")
        port = IDE_PORTS[session_type]
        prefix = f"/api/v1/namespaces/{grant.namespace}/pods/{grant.pod_name}:{port}/proxy"
        context = ssl.create_default_context(cafile=config.ssl_ca_cert) if config.ssl_ca_cert else True
        token = (config.api_key or {}).get("authorization") or (config.api_key or {}).get("BearerToken")
        headers = {}
        if token:
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        ws_host = host.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return Upstream(
            http_base=host + prefix,
            ws_base=ws_host + prefix,
            headers=headers,
            verify=context,
            rewrite_prefix=prefix,
        )
