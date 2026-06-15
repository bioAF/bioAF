"""Per-cloud Terraform deploy seam (the BAL split applied to infrastructure).

`TerraformExecutor` orchestrates the run lifecycle (run records, plan/apply,
progress, recovery) the SAME way on every cloud. The bits that genuinely differ
per cloud - where the modules live, which tfvars a module needs, how the remote
state backend is configured, and how the provider authenticates - are this seam.

Same approach, different mechanics: GCP modules + GCS state + injected SA creds;
AWS modules + S3 state + the VM's ambient instance-profile creds. The app stays
cloud-blind; only the module/provider details change (see the project's BAL
decoupling principle).

This module is deploy-time orchestration, not a runtime BAL adapter, so it holds
no cloud SDKs and no object-URI scheme literals (Terraform itself talks to the
clouds); it only assembles CLI flags + tfvars, so it lives beside the executor.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path

# The deployed layout: GCP modules at /app/terraform/modules (the executor's
# MODULES_DIR), AWS modules at /app/terraform/aws/modules. Kept here (not imported
# from the executor) so the executor can import this module without a cycle.
TERRAFORM_BASE = Path("/app/terraform")


async def _noop_cleanup() -> None:
    """No credential temp files to remove (the AWS provider uses ambient creds)."""
    return None


class TerraformCloud(ABC):
    """The cloud-divergent operations the TerraformExecutor delegates to."""

    @abstractmethod
    def modules_dir(self) -> Path:
        """Directory holding this cloud's Terraform modules."""

    @abstractmethod
    def config_keys(self) -> list[str]:
        """platform_config keys to read for a deploy on this cloud."""

    def postprocess_config(self, config: dict) -> dict:
        """Cloud-specific normalization of the read config. Default: unchanged."""
        return config

    @abstractmethod
    def write_tfvars(self, work_dir: Path, module_name: str, config: dict) -> dict:
        """Write terraform.tfvars.json for ``module_name`` and return the dict."""

    @abstractmethod
    def backend_init_args(self, config: dict, module_name: str | None, local_backend: bool) -> list[str]:
        """The ``terraform init`` backend-config flags for this cloud's remote state."""

    @abstractmethod
    async def build_provider_env(self, config: dict) -> tuple[dict, Callable[[], Awaitable[None]]]:
        """Return (env, cleanup) giving the provider its credentials.

        GCP injects service-account creds (with a temp-file cleanup); AWS returns
        only the region and relies on the EC2 instance profile via IMDS, so its
        cleanup is a no-op.
        """


class AwsTerraformCloud(TerraformCloud):
    """AWS column: AWS modules, S3 remote state, ambient instance-profile creds."""

    def modules_dir(self) -> Path:
        return TERRAFORM_BASE / "aws" / "modules"

    def config_keys(self) -> list[str]:
        return [
            "cloud_provider",
            "aws_region",
            "aws_account_id",
            "aws_app_role_arn",
            "org_slug",
            "deploy_suffix",
            "storage_stack_uid",
            "compute_stack_uid",
            "terraform_initialized",
            "terraform_state_bucket",
        ]

    def _state_bucket(self, config: dict) -> str:
        explicit = config.get("terraform_state_bucket")
        if explicit:
            return explicit
        account = config.get("aws_account_id") or "unknown"
        region = config.get("aws_region") or "us-east-1"
        return f"bioaf-tfstate-{account}-{region}"

    def write_tfvars(self, work_dir: Path, module_name: str, config: dict) -> dict:
        region = config.get("aws_region") or "us-east-1"
        org_slug = config.get("org_slug") or "bioaf"
        deploy_suffix = config.get("deploy_suffix") or ""
        storage_suffix = config.get("storage_stack_uid") or deploy_suffix

        tfvars: dict = {"region": region}
        if module_name == "foundation":
            tfvars["state_bucket_name"] = self._state_bucket(config)
            tfvars["account_id"] = config.get("aws_account_id") or ""
        elif module_name == "storage":
            tfvars["org_slug"] = org_slug
            if storage_suffix:
                tfvars["stack_uid"] = storage_suffix

        tfvars_path = work_dir / "terraform.tfvars.json"
        tfvars_path.write_text(json.dumps(tfvars, indent=2))
        return tfvars

    def backend_init_args(self, config: dict, module_name: str | None, local_backend: bool) -> list[str]:
        if local_backend:
            return ["-backend=false"]
        args = [f"-backend-config=bucket={self._state_bucket(config)}"]
        if module_name:
            # One state object per module (the S3 analog of the GCS prefix).
            args.append(f"-backend-config=key={module_name}/terraform.tfstate")
        region = config.get("aws_region") or "us-east-1"
        args.append(f"-backend-config=region={region}")
        # Terraform 1.10+ native S3 state locking (no DynamoDB table needed).
        args.append("-backend-config=use_lockfile=true")
        return args

    async def build_provider_env(self, config: dict) -> tuple[dict, Callable[[], Awaitable[None]]]:
        region = config.get("aws_region") or "us-east-1"
        # No credential injection: on the EC2 VM the bioaf-app instance profile is
        # the ambient credential (IMDS), the same role S3StorageProvider uses.
        env = {"AWS_REGION": region, "AWS_DEFAULT_REGION": region}
        return env, _noop_cleanup


class GcpTerraformCloud(TerraformCloud):
    """GCP column: delegates to the executor's existing GCP logic (behavior-

    preserving). Holds no GCP logic of its own yet; the executor still owns the
    GCS-state/SA-cred mechanics and this wraps them so both clouds route through
    one seam. Lazy imports avoid an import cycle with the executor.
    """

    def modules_dir(self) -> Path:
        return TERRAFORM_BASE / "modules"

    def config_keys(self) -> list[str]:
        return [
            "gcp_credentials_configured",
            "gcp_credential_source",
            "gcp_project_id",
            "gcp_region",
            "gcp_zone",
            "gcp_service_account_key",
            "gcp_service_account_email",
            "gcp_bootstrap_sa_email",
            "bioaf_app_sa_email",
            "org_slug",
            "deploy_suffix",
            "storage_stack_uid",
            "compute_stack_uid",
            "terraform_initialized",
            "terraform_state_bucket",
            "backend_service_account_email",
            "k8s_pipeline_machine_type",
            "k8s_pipeline_max_nodes",
            "k8s_pipeline_use_spot",
            "k8s_interactive_machine_type",
            "k8s_interactive_max_nodes",
        ]

    def postprocess_config(self, config: dict) -> dict:
        if config.get("gcp_credential_source", "vm_default") == "vm_default":
            target = config.get("gcp_bootstrap_sa_email") or config.get("gcp_service_account_email")
            if target:
                config["gcp_bootstrap_sa_email"] = target
        return config

    def write_tfvars(self, work_dir: Path, module_name: str, config: dict) -> dict:
        from app.services.terraform_executor import TerraformExecutor

        return TerraformExecutor._write_tfvars(work_dir, module_name, config)

    def backend_init_args(self, config: dict, module_name: str | None, local_backend: bool) -> list[str]:
        if local_backend:
            return ["-backend=false"]
        args: list[str] = []
        bucket = config.get("terraform_state_bucket")
        if bucket:
            args.append(f"-backend-config=bucket={bucket}")
        if module_name:
            args.append(f"-backend-config=prefix={module_name}")
        return args

    async def build_provider_env(self, config: dict) -> tuple[dict, Callable[[], Awaitable[None]]]:
        from app.adapters.credentials.credential_injector import GCPCredentialInjector

        env, cleanup = await GCPCredentialInjector.build_env(config)
        return env, cleanup


def get_terraform_cloud(cloud_provider: str | None) -> TerraformCloud:
    """Resolve the TerraformCloud for ``cloud_provider`` (None / "gcp" -> GCP)."""
    if cloud_provider == "aws":
        return AwsTerraformCloud()
    return GcpTerraformCloud()
