"""Shared classification of outbound network destinations.

One address predicate and one URL host canonicalization, used by the URL-import guard
(lab_document_upload_service) and by the webhook transport (webhook_transport). Each caller keeps
its own policy on top: URL imports accept public addresses only; webhooks additionally allow an
operator-configured internal receiver (webhook_transport.InternalDestination).
"""

from __future__ import annotations

import ipaddress
import re

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def is_public_address(ip: IPAddress) -> bool:
    """One predicate for IP literals and resolver answers alike.

    ``is_global`` alone is not the boundary: on Python 3.12 it is true for multicast and for the
    well-known NAT64 prefix 64:ff9b::/96, which the explicit exclusions refuse. The explicit
    exclusions alone missed shared address space (100.64.0.0/10), which ``is_global`` refuses. An
    address must pass both.
    """
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def parse_ip_literal(host: str) -> IPAddress | None:
    """The address a URL host names directly, or None for a hostname. Never consults DNS."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def canonical_hostname(host: str) -> str | None:
    """Lowercase ASCII (IDNA) form of a DNS hostname without a trailing dot, or None when malformed."""
    if not host:
        return None
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        return None
    labels = host.split(".")
    ascii_labels: list[str] = []
    for label in labels:
        if not label:
            return None
        try:
            encoded = label.encode("idna").decode("ascii") if not label.isascii() else label
        except UnicodeError:
            return None
        if not _LABEL.match(encoded):
            return None
        ascii_labels.append(encoded)
    result = ".".join(ascii_labels)
    return result if len(result) <= 253 else None
