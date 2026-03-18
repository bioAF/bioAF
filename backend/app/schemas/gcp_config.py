"""Pydantic schemas for GCP configuration settings."""

import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

_ORG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _validate_org_slug(slug: str) -> str:
    if len(slug) < 3:
        raise ValueError("org_slug must be at least 3 characters")
    if len(slug) > 30:
        raise ValueError("org_slug must be at most 30 characters")
    if not _ORG_SLUG_RE.match(slug):
        raise ValueError(
            "org_slug must contain only lowercase letters, digits, and hyphens, and must not start or end with a hyphen"
        )
    if "--" in slug:
        raise ValueError("org_slug must not contain consecutive hyphens")
    return slug


class GCPConfigUpdate(BaseModel):
    gcp_project_id: str | None = None
    gcp_region: str | None = None
    gcp_zone: str | None = None
    org_slug: str | None = None
    gcp_credential_source: Literal["vm_default", "service_account_key"] | None = None
    service_account_key: str | None = None  # write-only; never returned in GET
    gcp_service_account_email: str | None = None

    @field_validator("org_slug")
    @classmethod
    def validate_org_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_org_slug(v)


class GCPConfigResponse(BaseModel):
    model_config = {"from_attributes": True}

    gcp_project_id: str | None
    gcp_region: str | None
    gcp_zone: str | None
    org_slug: str | None
    gcp_credentials_configured: bool
    gcp_validation_status: str | None
    gcp_credential_source: str
    gcp_service_account_email: str | None
    # service_account_key is intentionally omitted - never returned to clients


class GCPValidationCheck(BaseModel):
    name: str
    passed: bool
    message: str
    status: str = ""

    @model_validator(mode="after")
    def set_status_from_passed(self) -> "GCPValidationCheck":
        if not self.status:
            self.status = "ok" if self.passed else "failed"
        return self


class PermissionDetail(BaseModel):
    permission: str
    granted: bool
    recommended_role: str


class GCPValidationResult(BaseModel):
    passed: bool
    checks: list[GCPValidationCheck]
    recommended_roles: list[str] = []
    permission_details: list[PermissionDetail] = []
