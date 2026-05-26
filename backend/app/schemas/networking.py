"""Pydantic schemas for networking settings (hostname, domain, TLS)."""

import re
from datetime import datetime

from pydantic import BaseModel, field_validator

# RFC 1123 DNS label: lowercase alphanumerics and hyphens, no leading/trailing
# hyphen, 1-63 chars. We normalize to lowercase for storage and reject
# mixed-case so the persisted FQDN matches what GMC and DNS see.
_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$|^[a-z0-9]$")


def _is_dns_label(label: str) -> bool:
    if not label or len(label) > 63:
        return False
    return bool(_LABEL_RE.match(label))


def _is_dns_name(name: str) -> bool:
    """A DNS name: two or more labels joined by single dots, total length <= 253."""
    if not name or len(name) > 253:
        return False
    if name.endswith(".") or name.startswith("."):
        return False
    if ".." in name:
        return False
    labels = name.split(".")
    if len(labels) < 2:
        return False
    # Last label (TLD) must be at least 2 chars and alphabetic-first.
    if len(labels[-1]) < 2:
        return False
    return all(_is_dns_label(label) for label in labels)


class NetworkingConfigResponse(BaseModel):
    hostname: str
    domain: str
    fqdn: str
    reachability_status: str
    reachability_checked_at: datetime | None
    cert_status: str
    https_enforced: bool


class NetworkingConfigUpdate(BaseModel):
    hostname: str
    domain: str

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str) -> str:
        if not _is_dns_label(v):
            raise ValueError(
                "hostname must be a valid DNS label: lowercase letters, digits, "
                "and hyphens, no leading or trailing hyphen, 1-63 characters"
            )
        return v

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        if not _is_dns_name(v):
            raise ValueError(
                "domain must be a valid DNS name: two or more lowercase labels "
                "joined by dots, no leading/trailing hyphens, total length 1-253"
            )
        return v


class ReachabilityTestResult(BaseModel):
    fqdn: str
    status: str  # reachable | http_unreachable | wrong_instance
    detail: str = ""
    checked_at: datetime


class CertificateStatusResponse(BaseModel):
    fqdn: str
    status: str  # not_requested | provisioning | active | failed


class EnforceHttpsRequest(BaseModel):
    enabled: bool


class EnforceHttpsResponse(BaseModel):
    fqdn: str
    https_enforced: bool
