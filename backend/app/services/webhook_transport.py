"""The only outbound path for webhook deliveries (01A, ADR-051).

Every attempt resolves the subscription host, authorizes every answer against the destination
policy, and dials only an authorized numeric address. HTTPX never resolves the hostname itself:
the connection pool's network backend is replaced by one that refuses any host but the approved
one and connects to the approved addresses. TLS still uses the original hostname for SNI and
certificate verification, because the request URL keeps it.

Policy:

- An absolute ``https`` URL with a host and valid port, without userinfo or fragment.
- Every resolver answer (or the literal) must be a public address (``is_public_address``), unless
  the operator configured an internal receiver exception for this organization and exact origin, in
  which case a non-public answer must also be one of the exception's exact private host addresses.
- Exceptions come only from ``BIOAF_WEBHOOK_INTERNAL_DESTINATIONS`` (``Settings``); they cannot be
  set through the API. They never allow plain HTTP, metadata, loopback, link-local, multicast,
  unspecified, reserved, shared, or translated addresses.

Known limit: classification cannot see installation-specific NAT64 or routing translation; such
deployments need network egress controls as well.

Each attempt uses its own connection pool, closed afterwards, so no socket authorized under an
earlier resolution is reused. No redirects, no proxies from the environment, no transport retries,
no HTTP/2 coalescing, verified TLS only, and the receiver's body is never read.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import ssl
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from app.services.outbound_destination import (
    IPAddress,
    canonical_hostname,
    is_public_address,
    parse_ip_literal,
)

logger = logging.getLogger("bioaf.webhook_transport")

REQUEST_TIMEOUT_SECONDS = 10.0
_OPERATION_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=1.0)

# Safe codes a delivery records instead of raw upstream text.
INVALID_DESTINATION = "invalid_destination"
DESTINATION_DENIED = "destination_denied"
DNS_FAILED = "dns_failed"

# What a person setting up a webhook reads for each refusal (the code stays available to clients).
REFUSAL_MESSAGES = {
    DESTINATION_DENIED: (
        "bioAF isn’t allowed to connect to this address. Check the address with the administrator of the "
        "system you want to connect."
    ),
    INVALID_DESTINATION: (
        "This isn’t a valid webhook address. Enter the full https:// address the other system gave you, for "
        "example https://lims.example.com/hooks/bioaf."
    ),
    DNS_FAILED: (
        "bioAF couldn’t find this address. Check that the server name is spelled correctly and that the "
        "server can be reached from bioAF."
    ),
}
TLS_FAILED = "tls_failed"
REQUEST_TIMEOUT = "request_timeout"
TRANSPORT_FAILED = "transport_failed"
HTTP_NON_2XX = "http_non_2xx"

# Refusals that no retry can change: the subscription must be corrected first.
TERMINAL_CODES = frozenset({INVALID_DESTINATION, DESTINATION_DENIED})

_PRIVATE_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_ULA = ipaddress.ip_network("fc00::/7")
# Cloud instance metadata endpoints that sit inside otherwise private ranges.
_METADATA = frozenset(
    {
        ipaddress.ip_address("fd00:ec2::254"),
        ipaddress.ip_address("fd00:ec2::23"),
    }
)


class DestinationRefused(ValueError):
    """The destination may not be contacted. ``code`` is a safe diagnostic code."""

    def __init__(self, code: str) -> None:
        super().__init__(f"webhook destination refused: {code}")
        self.code = code


@dataclass(frozen=True)
class WebhookTarget:
    host: str  # canonical: lowercase IDNA hostname, or compressed IP literal without brackets
    port: int
    literal: IPAddress | None
    path_and_query: str

    @property
    def host_for_origin(self) -> str:
        return f"[{self.host}]" if isinstance(self.literal, ipaddress.IPv6Address) else self.host

    @property
    def origin(self) -> str:
        return f"https://{self.host_for_origin}:{self.port}"

    @property
    def send_url(self) -> str:
        port = "" if self.port == 443 else f":{self.port}"
        return f"https://{self.host_for_origin}{port}{self.path_and_query}"


@dataclass(frozen=True)
class InternalDestination:
    organization_id: int
    origin: str
    hosts: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


@dataclass(frozen=True)
class AuthorizedDestination:
    target: WebhookTarget
    addresses: tuple[IPAddress, ...]


def parse_webhook_url(url: str) -> WebhookTarget:
    """Validate and canonicalize a subscription URL, or raise ``invalid_destination``."""
    if not isinstance(url, str) or not url or "#" in url or any(ch.isspace() for ch in url):
        raise DestinationRefused(INVALID_DESTINATION)
    try:
        parts = urlsplit(url)
        port = parts.port
        raw_host = parts.hostname
    except ValueError:
        raise DestinationRefused(INVALID_DESTINATION) from None
    if parts.scheme.lower() != "https" or not raw_host or "@" in parts.netloc:
        raise DestinationRefused(INVALID_DESTINATION)
    if port is None:
        port = 443
    if not 1 <= port <= 65535:
        raise DestinationRefused(INVALID_DESTINATION)
    literal = parse_ip_literal(raw_host)
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.scope_id:
            raise DestinationRefused(INVALID_DESTINATION)
        host = str(literal)
    else:
        canonical = canonical_hostname(raw_host)
        if canonical is None:
            raise DestinationRefused(INVALID_DESTINATION)
        host = canonical
    path_and_query = parts.path or "/"
    if parts.query:
        path_and_query = f"{path_and_query}?{parts.query}"
    return WebhookTarget(host=host, port=port, literal=literal, path_and_query=path_and_query)


def _exception_host(entry_index: int, cidr: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    problem = f"entry {entry_index}: cidrs must list exact private receiver addresses (/32 RFC1918 or /128 ULA)"
    if not isinstance(cidr, str):
        raise ValueError(problem)
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        raise ValueError(problem) from None
    if network.num_addresses != 1:
        raise ValueError(problem)
    if not _is_exception_address(network.network_address):
        raise ValueError(problem)
    return network


def _is_exception_address(ip: IPAddress) -> bool:
    if ip in _METADATA:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _PRIVATE_V4)
    return ip in _ULA and ip.ipv4_mapped is None


def parse_internal_destinations(raw: Any) -> tuple[InternalDestination, ...]:
    """Parse the operator's internal receiver exceptions. Messages never repeat the input."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError("must be a JSON list of internal destination entries") from None
    if isinstance(raw, tuple) and all(isinstance(item, InternalDestination) for item in raw):
        return raw
    if not isinstance(raw, list):
        raise ValueError("must be a JSON list of internal destination entries")
    parsed: list[InternalDestination] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict) or set(entry) != {"organization_id", "origin", "cidrs"}:
            raise ValueError(f"entry {index}: needs exactly organization_id, origin, and cidrs")
        org = entry["organization_id"]
        if not isinstance(org, int) or isinstance(org, bool) or org <= 0:
            raise ValueError(f"entry {index}: organization_id must be a positive integer")
        origin = entry["origin"]
        try:
            target = parse_webhook_url(origin) if isinstance(origin, str) else None
        except DestinationRefused:
            target = None
        if target is None or target.path_and_query != "/" or origin.rstrip("/").count("/") != 2:
            raise ValueError(f"entry {index}: origin must be an exact https://host[:port] origin")
        cidrs = entry["cidrs"]
        if not isinstance(cidrs, list) or not cidrs:
            raise ValueError(f"entry {index}: cidrs must list at least one receiver address")
        hosts = tuple(_exception_host(index, cidr) for cidr in cidrs)
        parsed.append(InternalDestination(organization_id=org, origin=target.origin, hosts=hosts))
    return tuple(parsed)


def internal_destination_policy(source: Any = None) -> tuple[InternalDestination, ...]:
    """The current operator policy (immutable for the life of the process)."""
    if source is None:
        from app.config import settings as source
    return source.webhook_internal_destinations


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def _system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


# The resolver every webhook attempt uses. Replaced by a controlled resolver in tests.
_resolve: Resolver = _system_resolve


async def authorize_webhook_destination(
    url: str,
    organization_id: int,
    *,
    policy: Iterable[InternalDestination] | None = None,
    resolver: Resolver | None = None,
) -> AuthorizedDestination:
    """Resolve and authorize ``url`` for ``organization_id``, or raise ``DestinationRefused``."""
    target = parse_webhook_url(url)
    rules = tuple(internal_destination_policy() if policy is None else policy)
    rule = next((r for r in rules if r.organization_id == organization_id and r.origin == target.origin), None)
    if target.literal is not None:
        addresses: list[IPAddress] = [target.literal]
    else:
        try:
            answers = await (resolver or _resolve)(target.host, target.port)
        except (OSError, UnicodeError):
            raise DestinationRefused(DNS_FAILED) from None
        if not answers:
            raise DestinationRefused(DNS_FAILED)
        addresses = []
        for answer in answers:
            ip = parse_ip_literal(answer)
            if ip is None:
                raise DestinationRefused(DESTINATION_DENIED)
            if ip not in addresses:
                addresses.append(ip)
    for ip in addresses:
        if is_public_address(ip):
            continue
        if rule is not None and _is_exception_address(ip) and any(ip in host for host in rule.hosts):
            continue
        raise DestinationRefused(DESTINATION_DENIED)
    return AuthorizedDestination(target=target, addresses=tuple(addresses))


# --- transport -------------------------------------------------------------------------------


def _network_backend() -> httpcore.AsyncNetworkBackend:
    """The real dialer. Replaced by a recording backend in tests."""
    return httpcore.AnyIOBackend()


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS with hostname checking. Replaced by a context trusting a test CA in tests."""
    return httpx.create_ssl_context(verify=True)


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Dial only the approved host, and only at its authorized numeric addresses."""

    def __init__(self, destination: AuthorizedDestination, inner: httpcore.AsyncNetworkBackend) -> None:
        self._destination = destination
        self._inner = inner

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        target = self._destination.target
        if host.strip("[]").lower() != target.host or port != target.port:
            raise httpcore.ConnectError("connection to an unapproved host refused")
        last: Exception | None = None
        for address in self._destination.addresses:
            try:
                return await self._inner.connect_tcp(
                    str(address), port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not webhook destinations")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _client(destination: AuthorizedDestination) -> httpx.AsyncClient:
    context = _ssl_context()
    transport = httpx.AsyncHTTPTransport(verify=context, http1=True, http2=False, retries=0)
    # The pool HTTPX builds would resolve the hostname itself; this one can only dial the approved
    # addresses. One connection, never kept alive past this attempt.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=context,
        max_connections=1,
        max_keepalive_connections=0,
        http1=True,
        http2=False,
        retries=0,
        network_backend=_PinnedBackend(destination, _network_backend()),
    )
    return httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=_OPERATION_TIMEOUT)


@dataclass(frozen=True)
class DeliveryOutcome:
    delivered: bool
    status_code: int | None
    code: str | None

    @property
    def terminal(self) -> bool:
        return self.code in TERMINAL_CODES


def _is_tls_failure(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


async def post_webhook(url: str, organization_id: int, body: bytes, headers: dict[str, str]) -> DeliveryOutcome:
    """Make one authorized POST attempt. Never raises for delivery failures; returns a safe code."""
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            destination = await authorize_webhook_destination(url, organization_id)
            send_headers = {**headers, "Accept-Encoding": "identity"}
            async with _client(destination) as client:
                async with client.stream(
                    "POST", destination.target.send_url, content=body, headers=send_headers
                ) as response:
                    status = response.status_code
            if 200 <= status < 300:
                return DeliveryOutcome(True, status, None)
            return DeliveryOutcome(False, status, HTTP_NON_2XX)
    except DestinationRefused as refused:
        return DeliveryOutcome(False, None, refused.code)
    except (TimeoutError, httpx.TimeoutException):
        return DeliveryOutcome(False, None, REQUEST_TIMEOUT)
    except (httpx.TransportError, ssl.SSLError, OSError) as exc:
        return DeliveryOutcome(False, None, TLS_FAILED if _is_tls_failure(exc) else TRANSPORT_FAILED)
