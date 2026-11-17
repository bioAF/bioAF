"""Webhook transport enforcement (01A): pinned dials, original-host TLS, bounded attempts.

A local TLS receiver stands in for the subscriber. The controlled network (webhook_network fixture)
records the numeric address the transport dials and routes only an approved public address to the
local receiver, so a dial to any other address fails and is visible in ``dials``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import ssl
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services import webhook_transport
from app.services.webhook_transport import parse_internal_destinations, post_webhook

pytestmark = pytest.mark.asyncio

PUBLIC = "93.184.216.34"


# --- a local TLS receiver ---------------------------------------------------------------------


def _ca_and_leaf(tmp_path, hostnames: list[str]):
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bioAF test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for name in hostnames:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            sans.append(x509.DNSName(name))
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path = tmp_path / "leaf.pem"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "leaf.key"
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return ca_path, cert_path, key_path


@dataclass
class Receiver:
    port: int = 0
    sni: list[str | None] = field(default_factory=list)
    requests: list[bytes] = field(default_factory=list)
    status: int = 200
    extra_headers: str = ""
    body: bytes = b"ok"
    header_delay: float = 0.0
    server: asyncio.AbstractServer | None = None

    @property
    def hosts(self) -> list[str]:
        out = []
        for raw in self.requests:
            for line in raw.split(b"\r\n"):
                if line.lower().startswith(b"host:"):
                    out.append(line.split(b":", 1)[1].strip().decode())
        return out


async def _start_receiver(tmp_path, hostnames: list[str], **kwargs) -> tuple[Receiver, ssl.SSLContext]:
    ca, cert, key = _ca_and_leaf(tmp_path, hostnames)
    receiver = Receiver(**kwargs)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)

    def _sni(sock, name, ctx):
        receiver.sni.append(name)

    server_ctx.sni_callback = _sni

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            body = await reader.readexactly(length) if length else b""
            receiver.requests.append(head + body)
            if receiver.header_delay:
                await asyncio.sleep(receiver.header_delay)
            reason = {200: "OK", 302: "Found", 503: "Unavailable"}.get(receiver.status, "X")
            writer.write(
                f"HTTP/1.1 {receiver.status} {reason}\r\nContent-Length: {len(receiver.body)}\r\n"
                f"{receiver.extra_headers}Connection: close\r\n\r\n".encode()
            )
            writer.write(receiver.body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    receiver.server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
    receiver.port = receiver.server.sockets[0].getsockname()[1]
    client_ctx = ssl.create_default_context(cafile=str(ca))
    return receiver, client_ctx


@pytest_asyncio.fixture
async def receiver(tmp_path, webhook_network, monkeypatch):
    rec, client_ctx = await _start_receiver(tmp_path, ["hooks.example"])
    monkeypatch.setattr(webhook_transport, "_ssl_context", lambda: client_ctx)
    webhook_network.answers["hooks.example"] = [PUBLIC]
    webhook_network.routes[(PUBLIC, rec.port)] = ("127.0.0.1", rec.port)
    yield rec
    rec.server.close()


def _url(rec: Receiver, host: str = "hooks.example") -> str:
    return f"https://{host}:{rec.port}/hook?k=v"


async def _post(url: str, org: int = 1):
    return await post_webhook(url, org, b'{"a":1}', {"Content-Type": "application/json"})


# --- pinning ----------------------------------------------------------------------------------


async def test_the_dial_goes_to_the_authorized_address_with_original_host_and_sni(receiver, webhook_network):
    outcome = await _post(_url(receiver))

    assert outcome.delivered and outcome.status_code == 200 and outcome.code is None
    assert webhook_network.dials == [(PUBLIC, receiver.port)]
    assert receiver.sni == ["hooks.example"]
    assert receiver.hosts == [f"hooks.example:{receiver.port}"]
    assert receiver.requests[0].endswith(b'{"a":1}')
    assert b"POST /hook?k=v HTTP/1.1" in receiver.requests[0]


async def test_a_rebinding_answer_is_never_dialed(receiver, webhook_network):
    answers = iter([[PUBLIC], ["10.0.0.5"]])
    webhook_network.answers["hooks.example"] = lambda: next(answers)

    first = await _post(_url(receiver))
    second = await _post(_url(receiver))

    assert first.delivered
    assert second.code == "destination_denied" and not second.delivered
    # Exactly one resolution per attempt, and the private answer was never dialed.
    assert webhook_network.resolved == ["hooks.example", "hooks.example"]
    assert webhook_network.dials == [(PUBLIC, receiver.port)]
    assert len(receiver.requests) == 1


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://127.0.0.1/x", "destination_denied"),
        ("https://[::1]/x", "destination_denied"),
        ("https://169.254.169.254/latest", "destination_denied"),
        ("http://hooks.example/x", "invalid_destination"),
        ("https://u:p@hooks.example/x", "invalid_destination"),
        ("https://nowhere.invalid/x", "dns_failed"),
    ],
)
async def test_refusals_dial_nothing(webhook_network, url, code):
    outcome = await _post(url)

    assert outcome.code == code and outcome.status_code is None and not outcome.delivered
    assert webhook_network.dials == []
    assert outcome.terminal is (code != "dns_failed")


async def test_literal_public_address_is_dialed_without_dns(tmp_path, webhook_network, monkeypatch):
    rec, ctx = await _start_receiver(tmp_path, [PUBLIC])
    monkeypatch.setattr(webhook_transport, "_ssl_context", lambda: ctx)
    webhook_network.routes[(PUBLIC, rec.port)] = ("127.0.0.1", rec.port)
    try:
        outcome = await _post(f"https://{PUBLIC}:{rec.port}/x")
    finally:
        rec.server.close()
    assert outcome.delivered
    assert webhook_network.resolved == []
    assert webhook_network.dials == [(PUBLIC, rec.port)]


async def test_an_internal_exception_reaches_only_its_receiver(tmp_path, webhook_network, monkeypatch):
    from app.config import settings

    rec, ctx = await _start_receiver(tmp_path, ["lims.lab.example"])
    monkeypatch.setattr(webhook_transport, "_ssl_context", lambda: ctx)
    monkeypatch.setattr(
        settings,
        "webhook_internal_destinations",
        parse_internal_destinations(
            [{"organization_id": 7, "origin": f"https://lims.lab.example:{rec.port}", "cidrs": ["10.24.8.19/32"]}]
        ),
    )
    webhook_network.answers["lims.lab.example"] = ["10.24.8.19"]
    webhook_network.routes[("10.24.8.19", rec.port)] = ("127.0.0.1", rec.port)
    try:
        allowed = await _post(f"https://lims.lab.example:{rec.port}/hook", org=7)
        other_org = await _post(f"https://lims.lab.example:{rec.port}/hook", org=8)
    finally:
        rec.server.close()
    assert allowed.delivered
    assert other_org.code == "destination_denied"
    assert webhook_network.dials == [("10.24.8.19", rec.port)]
    assert rec.sni == ["lims.lab.example"]


# --- TLS ---------------------------------------------------------------------------------------


async def test_a_certificate_for_another_host_fails_without_insecure_fallback(tmp_path, webhook_network, monkeypatch):
    rec, ctx = await _start_receiver(tmp_path, ["other.example"])
    monkeypatch.setattr(webhook_transport, "_ssl_context", lambda: ctx)
    webhook_network.answers["hooks.example"] = [PUBLIC]
    webhook_network.routes[(PUBLIC, rec.port)] = ("127.0.0.1", rec.port)
    try:
        outcome = await _post(_url(rec))
    finally:
        rec.server.close()
    assert outcome.code == "tls_failed" and not outcome.delivered
    assert rec.requests == []
    assert webhook_network.dials == [(PUBLIC, rec.port)]


async def test_an_untrusted_issuer_fails(receiver, monkeypatch):
    monkeypatch.setattr(webhook_transport, "_ssl_context", lambda: ssl.create_default_context())
    outcome = await _post(_url(receiver))
    assert outcome.code == "tls_failed"
    assert receiver.requests == []


# --- one bounded attempt -----------------------------------------------------------------------


async def test_a_redirect_is_one_non_2xx_attempt(receiver, webhook_network):
    receiver.status = 302
    receiver.extra_headers = "Location: https://hooks.example/elsewhere\r\n"
    outcome = await _post(_url(receiver))
    assert outcome.code == "http_non_2xx" and outcome.status_code == 302
    assert len(receiver.requests) == 1
    assert len(webhook_network.dials) == 1


async def test_a_5xx_is_not_retried_by_the_transport(receiver):
    receiver.status = 503
    outcome = await _post(_url(receiver))
    assert outcome.code == "http_non_2xx" and outcome.status_code == 503
    assert len(receiver.requests) == 1


async def test_an_oversized_compressed_2xx_body_is_delivered_once_without_reading_it(receiver):
    receiver.body = b"\x1f\x8b" + b"x" * (4 * 1024 * 1024)
    receiver.extra_headers = "Content-Encoding: gzip\r\n"
    outcome = await _post(_url(receiver))
    assert outcome.delivered and outcome.status_code == 200
    assert len(receiver.requests) == 1
    assert b"accept-encoding: identity" in receiver.requests[0].lower()


async def test_slow_headers_hit_the_total_deadline(receiver, monkeypatch):
    monkeypatch.setattr(webhook_transport, "REQUEST_TIMEOUT_SECONDS", 0.3)
    receiver.header_delay = 2.0
    loop = asyncio.get_running_loop()
    started = loop.time()
    outcome = await _post(_url(receiver))
    assert outcome.code == "request_timeout" and not outcome.delivered
    assert loop.time() - started < 1.5


async def test_a_slow_resolver_counts_against_the_deadline(webhook_network, monkeypatch):
    monkeypatch.setattr(webhook_transport, "REQUEST_TIMEOUT_SECONDS", 0.2)

    async def slow(host, port):
        await asyncio.sleep(5)
        return [PUBLIC]

    monkeypatch.setattr(webhook_transport, "_resolve", slow)
    outcome = await _post("https://hooks.example/x")
    assert outcome.code == "request_timeout"
    assert webhook_network.dials == []


async def test_proxy_environment_cannot_change_the_dial(receiver, webhook_network, monkeypatch):
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, "http://10.9.9.9:3128")
    outcome = await _post(_url(receiver))
    assert outcome.delivered
    assert webhook_network.dials == [(PUBLIC, receiver.port)]


async def test_a_refused_connection_is_a_transport_failure(webhook_network):
    outcome = await _post("https://hooks.example:9443/x")  # resolved, but no route: the dial fails
    assert outcome.code == "transport_failed" and not outcome.terminal
    assert webhook_network.dials == [(PUBLIC, 9443)]
