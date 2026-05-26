"""Pydantic schemas for networking settings (hostname, domain, TLS)."""

from datetime import datetime

from pydantic import BaseModel


class NetworkingConfigResponse(BaseModel):
    hostname: str
    domain: str
    fqdn: str
    reachability_status: str
    reachability_checked_at: datetime | None
    cert_status: str
    https_enforced: bool
