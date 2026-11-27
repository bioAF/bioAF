"""An organization-CA certificate uploaded for a private installation (plan 03.5A).

The pair is checked here before anything is staged; the host checks it again before installing
(installer/tls.sh), since the staging directory is writable from this container. Nothing here logs,
audits or returns key material.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.exceptions import UnprocessableError

MIN_REMAINING = timedelta(hours=1)
INSTALL_OPERATION = "tls-install-uploaded"


@dataclass(frozen=True)
class CertificateSummary:
    names: list[str]
    not_after: datetime
    issuer: str


def _public_der(key) -> bytes:
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _host_only(host: str) -> str:
    host = (host or "").strip()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _covers(cert: x509.Certificate, host: str) -> bool:
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return address in sans.get_values_for_type(x509.IPAddress)
    name = host.lower().rstrip(".")
    for pattern in sans.get_values_for_type(x509.DNSName):
        pattern = pattern.lower().rstrip(".")
        if pattern == name or (
            pattern.startswith("*.") and name.count(".") == pattern.count(".") and name.endswith(pattern[1:])
        ):
            return True
    return False


def check_pair(certificate_pem: str, private_key_pem: str, *, host: str) -> CertificateSummary:
    """The checked certificate's summary, or UnprocessableError with a plain reason."""
    try:
        chain = x509.load_pem_x509_certificates(certificate_pem.encode())
    except ValueError:
        raise UnprocessableError(
            "The certificate could not be read. Paste the PEM text, starting with the server certificate."
        ) from None
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    except (ValueError, TypeError):
        raise UnprocessableError("The private key could not be read. Upload an unencrypted PEM key.") from None
    leaf = chain[0]
    if _public_der(leaf.public_key()) != _public_der(key.public_key()):
        raise UnprocessableError("The private key does not match the certificate.")
    try:
        if leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise UnprocessableError("This is a CA certificate. Upload the server certificate the CA issued.")
    except x509.ExtensionNotFound:
        pass
    now = datetime.now(timezone.utc)
    if leaf.not_valid_before_utc > now:
        raise UnprocessableError("The certificate is not valid yet.")
    if leaf.not_valid_after_utc < now + MIN_REMAINING:
        raise UnprocessableError("The certificate has expired or expires within the hour.")
    name = _host_only(host)
    if not _covers(leaf, name):
        raise UnprocessableError(f"The certificate does not name {name}, the address you are using for bioAF.")
    try:
        sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v) for v in sans.get_values_for_type(x509.DNSName)] + [
            str(v) for v in sans.get_values_for_type(x509.IPAddress)
        ]
    except x509.ExtensionNotFound:
        names = []
    return CertificateSummary(names=names, not_after=leaf.not_valid_after_utc, issuer=leaf.issuer.rfc4514_string())


def _write_private(directory: str, name: str, content: str, owner: tuple[int, int]) -> None:
    path = os.path.join(directory, name)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, content.encode())
        if os.geteuid() == 0:
            # The host user who owns the staging directory installs (and removes) the pair.
            os.fchown(fd, *owner)
    finally:
        os.close(fd)


def stage_and_request_install(certificate_pem: str, private_key_pem: str) -> None:
    from app.config import settings

    directory = settings.tls_upload_dir
    info = os.stat(directory, follow_symlinks=False)
    owner = (info.st_uid, info.st_gid)
    _write_private(directory, "tls.key", private_key_pem, owner)
    _write_private(directory, "tls.crt", certificate_pem, owner)
    name = f"{INSTALL_OPERATION}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}.json"
    with open(os.path.join(settings.update_requests_dir, name), "x", encoding="utf-8") as f:
        json.dump({"operation": INSTALL_OPERATION}, f)
