"""Schemas for the EKS Kubernetes API endpoint administrator API (plan 04C)."""

from typing import Literal

from pydantic import BaseModel, Field


class EksEndpointSnapshot(BaseModel):
    cluster_arn: str | None = None
    cluster_name: str | None = None
    created_at: str | None = None
    endpoint_public_access: bool
    endpoint_private_access: bool
    public_access_cidrs: list[str] = Field(default_factory=list)


class EksEndpointCutover(BaseModel):
    mode: Literal["private_only", "public_restricted"]
    public_access_cidrs: list[str] = Field(default_factory=list)
    status: Literal["pending", "applying", "needs_reconcile"]
    requested_by_user_id: int | None = None
    requested_at: str | None = None


class EksEndpointStatus(BaseModel):
    mode: Literal["private_only", "public_restricted", "preserve_existing"] | None = Field(
        description="Recorded endpoint mode; null before the cluster's first upgraded compute plan."
    )
    endpoint_public_access: bool
    public_access_cidrs: list[str]
    pending_hardening: bool = Field(
        description="True while an existing cluster keeps its preserved (possibly unrestricted) endpoint."
    )
    snapshot: EksEndpointSnapshot | None = None
    cutover: EksEndpointCutover | None = None


class EksEndpointCutoverRequest(BaseModel):
    mode: Literal["private_only", "public_restricted"]
    public_access_cidrs: list[str] = Field(default_factory=list)
    recovery_paths_verified: bool = Field(
        description=(
            "The administrator confirms the application, node and independent operator recovery paths were "
            "verified and the recovery procedure is ready."
        )
    )


class EksEndpointSettingsUpdate(BaseModel):
    mode: Literal["private_only", "public_restricted", "preserve_existing"] | None = Field(
        default=None, description="A new cluster's endpoint mode. Refused once a cluster exists (use a cutover)."
    )
    public_access_cidrs: list[str] | None = Field(
        default=None, description="Approved egress CIDRs for public_restricted; no unrestricted range."
    )
    log_retention_days: int | None = Field(default=None, description="A CloudWatch Logs retention value; default 30.")
    log_types: list[str] | None = Field(
        default=None, description="Control-plane log types; must keep audit and authenticator. Default all five."
    )
