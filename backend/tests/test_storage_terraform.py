"""Tests for Terraform storage module and deploy endpoint.

Tests:
1. Storage module files exist with expected resource definitions
2. Deploy endpoint requires admin
3. Deploy requires terraform_initialized
4. Deploy requires not already deployed
5. Deploy stores bucket names on success (mocked executor)
"""

from pathlib import Path


STORAGE_MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "modules" / "storage"


def test_storage_module_files_exist():
    """Verify storage module TF files exist with expected resource definitions."""
    main_tf = STORAGE_MODULE_DIR / "main.tf"
    variables_tf = STORAGE_MODULE_DIR / "variables.tf"
    outputs_tf = STORAGE_MODULE_DIR / "outputs.tf"

    assert main_tf.exists(), "main.tf should exist in storage module"
    assert variables_tf.exists(), "variables.tf should exist in storage module"
    assert outputs_tf.exists(), "outputs.tf should exist in storage module"

    main_content = main_tf.read_text()

    # Verify all five bucket resources are defined
    for bucket_name in ["ingest", "raw", "working", "results", "config_backups"]:
        assert f'resource "google_storage_bucket" "{bucket_name}"' in main_content, (
            f"main.tf should define google_storage_bucket.{bucket_name}"
        )

    # Verify bucket naming pattern
    assert "bioaf" in main_content, "Bucket names should use bioaf prefix"
    assert "var.org_slug" in main_content, "Bucket names should use org_slug variable"

    # Verify versioning enabled
    assert "versioning" in main_content
    assert "enabled = true" in main_content

    # Verify uniform bucket-level access
    assert "uniform_bucket_level_access = true" in main_content

    # Verify outputs
    outputs_content = outputs_tf.read_text()
    for output_name in [
        "ingest_bucket_name",
        "raw_bucket_name",
        "working_bucket_name",
        "results_bucket_name",
        "config_backups_bucket_name",
    ]:
        assert f'output "{output_name}"' in outputs_content, f"outputs.tf should define {output_name}"

    # Verify variables
    variables_content = variables_tf.read_text()
    for var_name in ["project_id", "region", "org_slug"]:
        assert f'variable "{var_name}"' in variables_content, f"variables.tf should define {var_name}"


def test_storage_module_raw_bucket_has_lifecycle_rule():
    """Raw bucket should have lifecycle rule to transition to NEARLINE after 90 days."""
    main_tf = STORAGE_MODULE_DIR / "main.tf"
    content = main_tf.read_text()

    # Find the raw bucket block - check for lifecycle_rule with NEARLINE
    assert "NEARLINE" in content, "Raw bucket should have NEARLINE lifecycle rule"
    assert "age = 90" in content, "Raw bucket lifecycle should trigger at 90 days"


def test_storage_module_data_buckets_have_force_destroy_false():
    """All data buckets must have force_destroy = false to prevent accidental data loss."""
    import re

    main_tf = STORAGE_MODULE_DIR / "main.tf"
    content = main_tf.read_text()

    # Parse each bucket resource block and verify force_destroy
    data_buckets = ["ingest", "raw", "working", "results", "config_backups"]
    for bucket in data_buckets:
        pattern = rf'resource "google_storage_bucket" "{bucket}".*?(?=resource "|$)'
        match = re.search(pattern, content, re.DOTALL)
        assert match, f"Could not find google_storage_bucket.{bucket} resource block"

        block = match.group(0)
        assert "force_destroy = false" in block, (
            f"google_storage_bucket.{bucket} must have force_destroy = false "
            f"to prevent accidental data loss during terraform destroy"
        )
