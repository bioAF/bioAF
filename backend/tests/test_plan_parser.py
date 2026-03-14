"""Tests for the Terraform plan parser module (Step 2 - Phase 17).

Tests cover parsing of `terraform show -json` output format, which uses
resource_changes[] with change.actions arrays, distinct from the streaming
JSON produced by `terraform plan -json`.
"""

from app.services.plan_parser import TerraformPlanParser


# ---------------------------------------------------------------------------
# Sample Terraform show -json fixtures
# ---------------------------------------------------------------------------

EMPTY_PLAN_JSON: dict = {
    "format_version": "1.2",
    "terraform_version": "1.7.5",
    "resource_changes": [],
    "planned_values": {},
}

CREATES_ONLY_PLAN_JSON: dict = {
    "format_version": "1.2",
    "terraform_version": "1.7.5",
    "resource_changes": [
        {
            "address": "google_storage_bucket.terraform_state",
            "type": "google_storage_bucket",
            "name": "terraform_state",
            "change": {
                "actions": ["create"],
                "before": None,
                "after": {"name": "bioaf-tfstate-demo-lab", "location": "US"},
            },
        },
        {
            "address": "google_storage_bucket_versioning.terraform_state",
            "type": "google_storage_bucket_versioning",
            "name": "terraform_state",
            "change": {
                "actions": ["create"],
                "before": None,
                "after": {"bucket": "bioaf-tfstate-demo-lab"},
            },
        },
    ],
}

MIXED_ACTIONS_PLAN_JSON: dict = {
    "format_version": "1.2",
    "terraform_version": "1.7.5",
    "resource_changes": [
        {
            "address": "google_storage_bucket.terraform_state",
            "type": "google_storage_bucket",
            "name": "terraform_state",
            "change": {
                "actions": ["create"],
                "before": None,
                "after": {"name": "bioaf-tfstate-demo-lab"},
            },
        },
        {
            "address": "google_compute_instance.slurm_controller",
            "type": "google_compute_instance",
            "name": "slurm_controller",
            "change": {
                "actions": ["update"],
                "before": {"machine_type": "n2-standard-4"},
                "after": {"machine_type": "n2-standard-8"},
            },
        },
        {
            "address": "google_compute_instance.old_node",
            "type": "google_compute_instance",
            "name": "old_node",
            "change": {
                "actions": ["delete"],
                "before": {"machine_type": "n1-standard-2"},
                "after": None,
            },
        },
    ],
}

NO_OP_PLAN_JSON: dict = {
    "format_version": "1.2",
    "terraform_version": "1.7.5",
    "resource_changes": [
        {
            "address": "google_storage_bucket.terraform_state",
            "type": "google_storage_bucket",
            "name": "terraform_state",
            "change": {
                "actions": ["no-op"],
                "before": {"name": "bioaf-tfstate-demo-lab"},
                "after": {"name": "bioaf-tfstate-demo-lab"},
            },
        },
    ],
}


# ---------------------------------------------------------------------------
# Test 13: Empty plan
# ---------------------------------------------------------------------------


def test_parse_empty_plan_returns_zero_counts():
    """Empty resource_changes produces zero counts and empty resource list."""
    result = TerraformPlanParser.parse(EMPTY_PLAN_JSON)

    assert result["add_count"] == 0
    assert result["change_count"] == 0
    assert result["destroy_count"] == 0
    assert result["total"] == 0
    assert result["resources"] == []


# ---------------------------------------------------------------------------
# Test 14: Creates only
# ---------------------------------------------------------------------------


def test_parse_creates_only():
    """Two create actions are counted correctly."""
    result = TerraformPlanParser.parse(CREATES_ONLY_PLAN_JSON)

    assert result["add_count"] == 2
    assert result["change_count"] == 0
    assert result["destroy_count"] == 0
    assert result["total"] == 2
    assert len(result["resources"]) == 2
    # All resources should have action "create"
    assert all(r["action"] == "create" for r in result["resources"])


# ---------------------------------------------------------------------------
# Test 15: Mixed actions
# ---------------------------------------------------------------------------


def test_parse_mixed_actions():
    """Mixed create/update/delete are counted and listed correctly."""
    result = TerraformPlanParser.parse(MIXED_ACTIONS_PLAN_JSON)

    assert result["add_count"] == 1
    assert result["change_count"] == 1
    assert result["destroy_count"] == 1
    assert result["total"] == 3

    actions = {r["address"]: r["action"] for r in result["resources"]}
    assert actions["google_storage_bucket.terraform_state"] == "create"
    assert actions["google_compute_instance.slurm_controller"] == "update"
    assert actions["google_compute_instance.old_node"] == "delete"


# ---------------------------------------------------------------------------
# Test 16: Human-readable summaries
# ---------------------------------------------------------------------------


def test_parse_human_readable_descriptions():
    """Each resource summary has a human-readable description."""
    result = TerraformPlanParser.parse(CREATES_ONLY_PLAN_JSON)

    for r in result["resources"]:
        assert "description" in r
        assert isinstance(r["description"], str)
        assert len(r["description"]) > 0


def test_parse_gcs_bucket_description():
    """GCS bucket resources get a user-friendly description."""
    result = TerraformPlanParser.parse(CREATES_ONLY_PLAN_JSON)

    bucket_resource = next(r for r in result["resources"] if r["type"] == "google_storage_bucket")
    assert "GCS bucket" in bucket_resource["description"] or "bucket" in bucket_resource["description"].lower()


def test_parse_no_op_resources_excluded():
    """Resources with no-op action are not included in the result list."""
    result = TerraformPlanParser.parse(NO_OP_PLAN_JSON)

    assert result["total"] == 0
    assert result["resources"] == []


def test_parse_handles_missing_resource_changes_key():
    """Plan dict without resource_changes key returns zero counts safely."""
    result = TerraformPlanParser.parse({})

    assert result["add_count"] == 0
    assert result["total"] == 0


def test_parse_replace_counts_two_operations():
    """A replace action (create + delete) counts as two operations in total,
    matching the two apply_complete events Terraform emits."""
    plan = {
        "resource_changes": [
            {
                "address": "google_storage_bucket.ingest",
                "type": "google_storage_bucket",
                "name": "ingest",
                "change": {"actions": ["delete", "create"]},
            },
            {
                "address": "google_storage_bucket.results",
                "type": "google_storage_bucket",
                "name": "results",
                "change": {"actions": ["create"]},
            },
        ],
    }
    result = TerraformPlanParser.parse(plan)

    assert result["add_count"] == 2
    assert result["destroy_count"] == 1
    assert result["change_count"] == 0
    # total = add_count + change_count + destroy_count = 3 operations
    assert result["total"] == 3
    assert len(result["resources"]) == 2


def test_parse_data_source_reads_excluded():
    """Data source reads (action=['read']) are excluded from counts."""
    plan = {
        "resource_changes": [
            {
                "address": "data.google_storage_project_service_account.gcs_account",
                "type": "google_storage_project_service_account",
                "name": "gcs_account",
                "change": {"actions": ["read"]},
            },
            {
                "address": "google_storage_bucket.ingest",
                "type": "google_storage_bucket",
                "name": "ingest",
                "change": {"actions": ["create"]},
            },
        ],
    }
    result = TerraformPlanParser.parse(plan)

    assert result["add_count"] == 1
    assert result["total"] == 1
    assert len(result["resources"]) == 1
