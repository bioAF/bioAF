"""Cloud-neutral resolution of the image build/registry context (Stage 6e).

The image services (``notebook_image_service`` / ``cellxgene_image_service``)
orchestrate the ImageRegistry + ImageBuild seams, which need a small bundle of
cloud-specific facts: the provider ``config`` dict (project/account + region,
plus the CodeBuild project on AWS), the credentials to authenticate with, and
the identity the build runs as. This module is the *single* place that branches
on ``cloud_provider`` to assemble that bundle, so the rest of both services stay
cloud-blind (they consume an ``ImagePlatform`` and never read ``gcp_*`` / ``aws_*``
keys directly).

GCP is byte-identical to the pre-Stage-6e behavior: the GCP branch reads exactly
``gcp_project_id`` / ``gcp_region`` / ``gcp_service_account_email`` and the same
``load_gcp_credentials`` path, so an existing GCP install resolves the same config
+ credentials + build SA it does today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.platform.cloud_provider import get_cloud_provider
from app.platform.platform_config_service import PlatformConfigService


def _clean(value: str | None) -> str:
    """Normalize a platform_config value, treating the ``"null"`` sentinel as unset."""
    if value is None or value == "null":
        return ""
    return value


@dataclass
class ImagePlatform:
    """The cloud-resolved inputs the ImageRegistry/ImageBuild providers consume.

    ``config`` is the provider config dict (GCP: ``project_id`` + ``region``; AWS:
    ``account_id`` + ``region`` + ``codebuild_project``). ``build_sa`` is the
    identity the build runs as (a GCP SA email, or ``None`` to fall back to the
    credentials' own SA at submit time; always ``None`` on AWS, where the
    CodeBuild project runs as its own Terraform-provisioned role).

    Credentials are resolved lazily via :func:`resolve_image_credentials` at the
    point of each provider call (not eagerly here): AWS never needs them (boto3
    authenticates ambiently through the instance profile), and on GCP this keeps
    the credential fetch where the pre-6e code did it (so a build/poll that only
    needs the config never triggers a credential lookup).
    """

    cloud_provider: str
    config: dict
    build_sa: str | None

    def require_target(self) -> None:
        """Raise if the registry target (project/account + region) is unconfigured.

        Preserves the GCP error messages the image services raised pre-6e.
        """
        if self.cloud_provider == "aws":
            if not self.config.get("account_id"):
                raise ValidationError("AWS account not configured")
            if not self.config.get("region"):
                raise ValidationError("AWS region not configured")
        else:
            if not self.config.get("project_id"):
                raise ValidationError("GCP project not configured")
            if not self.config.get("region"):
                raise ValidationError("GCP region not configured")

    def require_build_service(self) -> None:
        """Raise if the build service needs provisioning that has not run.

        Cloud Build is serverless (nothing to provision); CodeBuild needs a
        project from the ``image_build`` Terraform module.
        """
        if self.cloud_provider == "aws" and not self.config.get("codebuild_project"):
            raise ValidationError("Image build is not deployed yet. Deploy the image build infrastructure first.")

    @property
    def has_target(self) -> bool:
        """True when the registry target is configured (used by pollers to no-op)."""
        if self.cloud_provider == "aws":
            return bool(self.config.get("account_id") and self.config.get("region"))
        return bool(self.config.get("project_id") and self.config.get("region"))


async def resolve_image_platform(session: AsyncSession) -> ImagePlatform:
    """Resolve the cloud-neutral image platform for this install.

    The single ``cloud_provider`` branch for the image services. The GCP branch
    reproduces the pre-6e reads exactly (byte-identical on a GCP install).
    """
    cloud = await get_cloud_provider(session)

    if cloud == "aws":
        cfg = await PlatformConfigService.get_many(session, ["aws_account_id", "aws_region", "aws_codebuild_project"])
        config = {
            "account_id": _clean(cfg.get("aws_account_id")),
            "region": _clean(cfg.get("aws_region")),
            "codebuild_project": _clean(cfg.get("aws_codebuild_project")),
        }
        # AWS: the build runs as the CodeBuild project's own service role.
        return ImagePlatform("aws", config, build_sa=None)

    # GCP (default; byte-identical to pre-6e).
    cfg = await PlatformConfigService.get_many(session, ["gcp_project_id", "gcp_region", "gcp_service_account_email"])
    config = {"project_id": _clean(cfg.get("gcp_project_id")), "region": _clean(cfg.get("gcp_region"))}
    return ImagePlatform("gcp", config, build_sa=_clean(cfg.get("gcp_service_account_email")) or None)


async def resolve_image_credentials(session: AsyncSession, platform: ImagePlatform) -> Any:
    """Resolve the credentials object the image providers authenticate with.

    Lazy companion to :func:`resolve_image_platform`: AWS returns ``None`` (boto3
    uses the ambient instance-profile / IRSA chain); GCP loads the same
    credentials the pre-6e image services did (honoring bootstrap-SA
    impersonation via ``credential_injector.load_gcp_credentials``).
    """
    if platform.cloud_provider == "aws":
        return None
    from app.services.notebook_image_service import _get_credentials

    return await _get_credentials(session)
