"""Unit tests for the per-cloud Terraform deploy seam (terraform_cloud).

DB-free: the seam only assembles tfvars + CLI flags from a config dict, so it is
unit-testable exactly like the existing TerraformExecutor._write_tfvars tests.
Covers the AWS column (new) and that the GCP column stays backend-config/prefix
shaped (behavior-preserving) and routes write_tfvars through the executor.
"""

from __future__ import annotations

import json

import pytest

from app.services.terraform_cloud import (
    AwsTerraformCloud,
    GcpTerraformCloud,
    get_terraform_cloud,
)


# --- resolver ----------------------------------------------------------------


def test_resolver_picks_cloud():
    assert isinstance(get_terraform_cloud("aws"), AwsTerraformCloud)
    assert isinstance(get_terraform_cloud("gcp"), GcpTerraformCloud)
    assert isinstance(get_terraform_cloud(None), GcpTerraformCloud)  # legacy GCP installs


# --- AWS column --------------------------------------------------------------


class TestAwsTerraformCloud:
    def setup_method(self):
        self.cloud = AwsTerraformCloud()

    def test_modules_dir_is_aws_path(self):
        assert self.cloud.modules_dir().as_posix().endswith("/terraform/aws/modules")

    def test_config_keys_are_aws_keys(self):
        keys = self.cloud.config_keys()
        assert "aws_region" in keys
        assert "terraform_state_bucket" in keys
        assert "cloud_provider" in keys
        assert "gcp_project_id" not in keys

    def test_write_tfvars_foundation(self, tmp_path):
        tf = self.cloud.write_tfvars(
            tmp_path, "foundation", {"aws_region": "us-west-1", "aws_account_id": "123456789012"}
        )
        assert tf["region"] == "us-west-1"
        # state bucket is computed when not explicitly configured
        assert tf["state_bucket_name"] == "bioaf-tfstate-123456789012-us-west-1"
        assert tf["account_id"] == "123456789012"
        written = json.loads((tmp_path / "terraform.tfvars.json").read_text())
        assert written == tf

    def test_write_tfvars_foundation_honors_explicit_state_bucket(self, tmp_path):
        tf = self.cloud.write_tfvars(
            tmp_path,
            "foundation",
            {"aws_region": "us-west-1", "terraform_state_bucket": "my-state"},
        )
        assert tf["state_bucket_name"] == "my-state"

    def test_write_tfvars_storage(self, tmp_path):
        tf = self.cloud.write_tfvars(
            tmp_path, "storage", {"aws_region": "us-west-1", "org_slug": "acme", "storage_stack_uid": "abc123"}
        )
        assert tf == {"region": "us-west-1", "org_slug": "acme", "stack_uid": "abc123"}

    def test_write_tfvars_storage_omits_stack_uid_on_destroy(self, tmp_path):
        tf = self.cloud.write_tfvars(tmp_path, "storage", {"aws_region": "us-west-1", "org_slug": "acme"})
        assert "stack_uid" not in tf  # omitted so destroy uses the state value

    def test_write_tfvars_image_build(self, tmp_path):
        # The CodeBuild project + IAM role module: region + account_id (ECR ARN
        # scope) + org_slug; install-level/stable names, so no stack_uid.
        tf = self.cloud.write_tfvars(
            tmp_path,
            "image_build",
            {"aws_region": "us-west-1", "org_slug": "acme", "aws_account_id": "123456789012"},
        )
        assert tf == {"region": "us-west-1", "account_id": "123456789012", "org_slug": "acme"}
        assert "stack_uid" not in tf

    def test_write_tfvars_compute(self, tmp_path):
        tf = self.cloud.write_tfvars(
            tmp_path,
            "compute",
            {
                "aws_region": "us-west-1",
                "org_slug": "acme",
                "compute_stack_uid": "c0ffee",
                "aws_account_id": "123456789012",
                "aws_app_role_arn": "arn:aws:iam::123456789012:role/bioaf-app",
            },
        )
        assert tf == {
            "region": "us-west-1",
            "org_slug": "acme",
            "stack_uid": "c0ffee",
            "account_id": "123456789012",
            "app_role_arn": "arn:aws:iam::123456789012:role/bioaf-app",
        }

    def test_write_tfvars_compute_omits_optional_when_absent(self, tmp_path):
        # No app role / account / suffix -> the module's own defaults apply, and
        # stack_uid is omitted so a destroy uses the value already in state.
        tf = self.cloud.write_tfvars(tmp_path, "compute", {"aws_region": "us-west-1", "org_slug": "acme"})
        assert tf == {"region": "us-west-1", "org_slug": "acme", "account_id": ""}
        assert "app_role_arn" not in tf
        assert "stack_uid" not in tf

    def test_backend_args_local(self):
        assert self.cloud.backend_init_args({}, "storage", local_backend=True) == ["-backend=false"]

    def test_backend_args_remote_s3(self):
        args = self.cloud.backend_init_args(
            {"terraform_state_bucket": "bioaf-state", "aws_region": "us-west-1"},
            "storage",
            local_backend=False,
        )
        assert "-backend-config=bucket=bioaf-state" in args
        assert "-backend-config=key=storage/terraform.tfstate" in args
        assert "-backend-config=region=us-west-1" in args
        assert "-backend-config=use_lockfile=true" in args

    @pytest.mark.asyncio
    async def test_build_provider_env_is_ambient(self):
        env, cleanup = await self.cloud.build_provider_env({"aws_region": "us-west-1"})
        assert env["AWS_REGION"] == "us-west-1"
        assert env["AWS_DEFAULT_REGION"] == "us-west-1"
        # no credential temp files to clean up (instance profile / IMDS); cleanup
        # is a no-op that returns None and must not raise.
        await cleanup()

    def test_is_configured_requires_account_id(self):
        # AWS is "configured" when the account identity is set (the bootstrap
        # gate's AWS analog of GCP's gcp_credentials_configured flag).
        assert self.cloud.is_configured({"aws_account_id": "123456789012"}) is True
        assert self.cloud.is_configured({}) is False
        assert self.cloud.is_configured({"aws_account_id": ""}) is False

    def test_not_configured_message_mentions_aws(self):
        assert "AWS" in self.cloud.not_configured_message()

    def test_lock_object_path_is_deferred_none(self):
        # S3 native (use_lockfile) lock deletion is a deferred follow-up, so there
        # is no executor-deletable lock object yet and lock cleanup no-ops on AWS.
        assert self.cloud.lock_object_path("storage") is None


# --- GCP column (behavior-preserving) ---------------------------------------


class TestGcpTerraformCloud:
    def setup_method(self):
        self.cloud = GcpTerraformCloud()

    def test_modules_dir_is_gcp_path(self):
        d = self.cloud.modules_dir().as_posix()
        assert d.endswith("/terraform/modules")
        assert "/aws/" not in d

    def test_backend_args_use_gcs_prefix(self):
        args = self.cloud.backend_init_args({"terraform_state_bucket": "bioaf-state"}, "storage", local_backend=False)
        assert "-backend-config=bucket=bioaf-state" in args
        assert "-backend-config=prefix=storage" in args  # GCS prefix, not S3 key

    def test_write_tfvars_delegates_to_executor(self, tmp_path):
        # The GCP column routes through the existing executor logic, so a foundation
        # deploy still produces the project_id + state_bucket_name tfvars it always did.
        tf = self.cloud.write_tfvars(tmp_path, "foundation", {"gcp_project_id": "proj-x", "gcp_region": "us-central1"})
        assert tf["project_id"] == "proj-x"
        assert tf["state_bucket_name"] == "bioaf-tfstate-proj-x"

    def test_postprocess_config_fills_bootstrap_target(self):
        cfg = self.cloud.postprocess_config(
            {"gcp_credential_source": "vm_default", "gcp_service_account_email": "sa@x.iam"}
        )
        assert cfg["gcp_bootstrap_sa_email"] == "sa@x.iam"

    def test_is_configured_reads_gcp_flag(self):
        # Behavior-preserving: the same predicate the executor used inline.
        assert self.cloud.is_configured({"gcp_credentials_configured": "true"}) is True
        assert self.cloud.is_configured({"gcp_credentials_configured": "false"}) is False
        assert self.cloud.is_configured({}) is False

    def test_not_configured_message_mentions_gcp(self):
        assert "GCP" in self.cloud.not_configured_message()

    def test_lock_object_path_is_gcs_default_tflock(self):
        # The GCS lock object the executor deletes after a failed/abandoned run,
        # unchanged from the previous hardcoded "{module}/default.tflock".
        assert self.cloud.lock_object_path("compute") == "compute/default.tflock"
        assert self.cloud.lock_object_path(None) is None


def test_the_disk_settings_are_read_back_with_the_other_pool_settings():
    """`config_keys` is what the Components page reads. A key missing here is a control the page
    cannot render even once the API and terraform both support it."""
    from app.services.terraform_cloud import GcpTerraformCloud

    keys = GcpTerraformCloud().config_keys()
    assert "k8s_pipeline_disk_size_gb" in keys
    assert "k8s_pipeline_disk_type" in keys
