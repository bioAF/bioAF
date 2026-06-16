"""Pydantic schemas for AWS configuration settings (stage 8d).

The structural parallel of ``app.schemas.gcp_config``. AWS auth is the EC2
instance profile (ambient), so there is no service-account-key field to carry:
the account / region / role ARNs / org_slug the install runs on are stored, and
validation confirms the ambient credentials resolve and match the configured
account. The org_slug rule is cloud-neutral, so it is reused from the GCP schema.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from app.schemas.gcp_config import _validate_org_slug


class AwsConfigUpdate(BaseModel):
    aws_account_id: str | None = None
    aws_region: str | None = None
    aws_app_role_arn: str | None = None
    aws_bootstrap_role_arn: str | None = None
    org_slug: str | None = None

    @field_validator("org_slug")
    @classmethod
    def validate_org_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_org_slug(v)


class AwsConfigResponse(BaseModel):
    model_config = {"from_attributes": True}

    aws_account_id: str | None
    aws_region: str | None
    aws_app_role_arn: str | None
    aws_bootstrap_role_arn: str | None
    org_slug: str | None
    # Ambient credential source for the install (instance_profile today). Surfaced
    # read-only so the UI can show how the app authenticates.
    aws_credential_source: str
    aws_credentials_configured: bool
    aws_validation_status: str | None


class AwsValidationCheck(BaseModel):
    name: str
    passed: bool
    message: str
    status: str = ""

    @model_validator(mode="after")
    def set_status_from_passed(self) -> "AwsValidationCheck":
        if not self.status:
            self.status = "ok" if self.passed else "failed"
        return self


class AwsValidationResult(BaseModel):
    passed: bool
    checks: list[AwsValidationCheck]
    # The account STS reported for the resolved credentials (display / parity).
    account_id: str | None = None
