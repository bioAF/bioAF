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

from app.services.deployment_outputs import identifier_value
from app.platform.aws_installation import NEW_ROLE_PATH
from app.services.eks_endpoint_policy import CONFIG_KEYS as EKS_CONFIG_KEYS

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

    @abstractmethod
    def is_configured(self, config: dict) -> bool:
        """Whether this cloud's deploy identity/credentials are configured.

        The bootstrap gate: GCP reads the gcp_credentials_configured flag; AWS
        checks the account identity is set (the VM's instance profile is ambient).
        """

    @abstractmethod
    def not_configured_message(self) -> str:
        """Operator-facing message raised when ``is_configured`` is False."""

    async def prepare_run(
        self,
        session,
        module_name: str,
        config: dict,
        *,
        phase: str,
        run_id: int | None,
        pull_state: Callable[[], Awaitable[dict | None]],
    ) -> dict:
        """Before a plan (``phase="plan"``) or apply, after init: check and resolve cloud-specific
        inputs, returning the config the tfvars are written from. Raise to refuse. Default: unchanged."""
        return config

    def review_plan(self, module_name: str, config: dict, raw_plan: dict) -> dict:
        """Refuse a plan (``terraform show -json``) this cloud's guardrails do not allow. Return
        extra keys to record in the run's plan_json for the apply to compare. Default: allow,
        record nothing."""
        return {}

    async def review_apply(
        self,
        module_name: str,
        config: dict,
        *,
        planned_tfvars: dict | None,
        applying_tfvars: dict,
        reviewed_plan: dict | None,
        plan_now: Callable[[], Awaitable[dict]],
    ) -> bool:
        """Before an apply, after its tfvars are written: refuse an apply that is not the reviewed
        plan. Return True to apply the plan file ``plan_now`` just wrote (and checked) instead of a
        fresh auto-approved apply. Default: no check, fresh apply."""
        return False

    async def finish_apply(self, session, module_name: str, config: dict, *, run_id: int, succeeded: bool) -> None:
        """After an apply exits. Default: nothing."""
        return None

    async def finish_destroy(self, session, module_name: str) -> None:
        """After a successful destroy. Default: nothing."""
        return None

    def lock_object_path(self, module_name: str | None) -> str | None:
        """State-store object key for ``module_name``'s lock, or None when this
        cloud has no executor-deletable lock object.

        The executor deletes this object via the storage adapter after a failed or
        abandoned run. Default None (no deletable lock); each cloud overrides for
        its remote-state backend's lock convention.
        """
        return None


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
            "storage_bucket_markers",
            "compute_stack_uid",
            "terraform_initialized",
            "terraform_state_bucket",
            # The application network, recorded by the backend for private backend sync (plan 03B).
            "aws_app_vpc_id",
            "aws_app_security_group_id",
            # EKS endpoint and control-plane logging (plan 04C).
            "compute_deployed",
            "gke_cluster_name",
            *EKS_CONFIG_KEYS,
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
        org_slug = identifier_value(config.get("org_slug")) or "bioaf"
        deploy_suffix = identifier_value(config.get("deploy_suffix"))
        storage_suffix = identifier_value(config.get("storage_stack_uid")) or deploy_suffix

        tfvars: dict = {"region": region}
        if module_name == "foundation":
            tfvars["state_bucket_name"] = self._state_bucket(config)
            tfvars["account_id"] = config.get("aws_account_id") or ""
        elif module_name == "image_build":
            # CodeBuild project + its IAM service role (the AWS analog of Cloud
            # Build, which is serverless and needs no module). Install-level/stable
            # names, so no stack_uid; account_id scopes the ECR repo ARNs.
            tfvars["account_id"] = config.get("aws_account_id") or ""
            tfvars["org_slug"] = org_slug
            if "_aws_role_paths" in config:
                tfvars["role_paths"] = config["_aws_role_paths"]
                tfvars["new_role_path"] = NEW_ROLE_PATH
        elif module_name == "storage":
            tfvars["org_slug"] = org_slug
            if storage_suffix:
                tfvars["stack_uid"] = storage_suffix
            from app.services.storage_generation import markers_for_tfvars

            markers = markers_for_tfvars(config, identifier_value(config.get("storage_stack_uid")))
            if markers:
                tfvars["bucket_markers"] = markers
        elif module_name == "compute":
            # EKS cluster + node groups + IRSA runner roles. The cluster name uses
            # the compute stack uid (its own suffix, like GKE), and the bioaf-app
            # role ARN is granted an EKS access entry so the backend can drive the
            # cluster out-of-cluster (the ClusterAuth seam).
            tfvars["org_slug"] = org_slug
            compute_suffix = identifier_value(config.get("compute_stack_uid")) or deploy_suffix
            if compute_suffix:
                tfvars["stack_uid"] = compute_suffix
            tfvars["account_id"] = config.get("aws_account_id") or ""
            app_role_arn = config.get("aws_app_role_arn")
            if app_role_arn:
                tfvars["app_role_arn"] = app_role_arn
            # Private backend sync (plan 03B): peer with the application VPC when it is recorded.
            app_vpc = identifier_value(config.get("aws_app_vpc_id"))
            app_sg = identifier_value(config.get("aws_app_security_group_id"))
            if app_vpc and app_sg:
                tfvars["app_vpc_id"] = app_vpc
                tfvars["app_security_group_id"] = app_sg
            # Explicit endpoint/logging inputs resolved by prepare_run for a plan or apply (plan 04C).
            tfvars.update(config.get("_eks_resolved") or {})
            if "_aws_role_paths" in config:
                tfvars["role_paths"] = config["_aws_role_paths"]
                tfvars["new_role_path"] = NEW_ROLE_PATH

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

    def is_configured(self, config: dict) -> bool:
        # The account identity gates bootstrap; the VM's instance profile supplies
        # the actual credentials ambiently (no stored key to validate).
        return bool(config.get("aws_account_id"))

    def not_configured_message(self) -> str:
        return "AWS is not configured. Set the AWS account before bootstrapping."

    async def prepare_run(self, session, module_name, config, *, phase, run_id, pull_state):
        if module_name not in ("compute", "image_build"):
            return config
        from app.exceptions import ValidationError
        from app.platform.aws_installation import role_paths_from_state

        pulled: list = []

        async def pull_once():
            if not pulled:
                pulled.append(await pull_state())
            return pulled[0]

        state = await pull_once()
        if state is None:
            raise ValidationError(
                f"The remote {module_name} state could not be read, so the existing roles cannot be identified. "
                "Nothing was changed; check the state bucket and retry."
            )
        config = {**config, "_aws_role_paths": role_paths_from_state(state)}
        if module_name == "image_build":
            return config
        from app.services import eks_endpoint

        return await eks_endpoint.prepare_run(session, config, phase=phase, run_id=run_id, pull_state=pull_once)

    def review_plan(self, module_name, config, raw_plan):
        if module_name != "compute":
            return {}
        from app.services import eks_endpoint, eks_endpoint_policy

        eks_endpoint_policy.review_plan(raw_plan, config)
        return {eks_endpoint.REVIEWED_CHANGES_KEY: eks_endpoint.change_digests(raw_plan)}

    async def review_apply(self, module_name, config, *, planned_tfvars, applying_tfvars, reviewed_plan, plan_now):
        if module_name != "compute":
            return False
        from app.services import eks_endpoint

        await eks_endpoint.review_apply(
            config,
            planned_tfvars=planned_tfvars,
            applying_tfvars=applying_tfvars,
            reviewed_plan=reviewed_plan,
            plan_now=plan_now,
        )
        return True

    async def finish_apply(self, session, module_name, config, *, run_id, succeeded):
        if module_name == "compute":
            from app.services import eks_endpoint

            await eks_endpoint.finish_apply(session, config, run_id=run_id, succeeded=succeeded)

    async def finish_destroy(self, session, module_name):
        if module_name == "compute":
            from app.services import eks_endpoint

            await eks_endpoint.finish_destroy(session)

    def lock_object_path(self, module_name: str | None) -> str | None:
        # Deferred: Terraform's S3 backend with use_lockfile writes a <key>.tflock
        # object whose deletion path differs from GCS; faithful S3 lock cleanup is
        # a separate follow-up (see cleanup-items). Until then, no executor-driven
        # lock deletion on AWS, so this returns None and lock cleanup no-ops.
        return None


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
            "storage_bucket_markers",
            "compute_stack_uid",
            "terraform_initialized",
            "terraform_state_bucket",
            "backend_service_account_email",
            "k8s_pipeline_machine_type",
            "k8s_pipeline_max_nodes",
            "k8s_pipeline_use_spot",
            "k8s_pipeline_disk_size_gb",
            "k8s_pipeline_disk_type",
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

    def is_configured(self, config: dict) -> bool:
        return config.get("gcp_credentials_configured", "false") == "true"

    def not_configured_message(self) -> str:
        return "GCP credentials are not configured. Configure GCP settings before bootstrapping."

    def lock_object_path(self, module_name: str | None) -> str | None:
        # Unchanged from the executor's previous hardcoded path: the GCS backend
        # writes the lock at "<prefix>/default.tflock" and prefix == module_name.
        if not module_name:
            return None
        return f"{module_name}/default.tflock"


def get_terraform_cloud(cloud_provider: str | None) -> TerraformCloud:
    """Resolve the TerraformCloud for ``cloud_provider`` (None / "gcp" -> GCP)."""
    if cloud_provider == "aws":
        return AwsTerraformCloud()
    return GcpTerraformCloud()
