"""Terraform plan parser for `terraform show -json` output.

Parses the JSON produced by `terraform show -json <planfile>` and extracts
resource change counts and human-readable summaries. This format is distinct
from the streaming NDJSON produced by `terraform plan -json`.

The key structure is:
  plan["resource_changes"] = [
    {
      "address": "google_storage_bucket.terraform_state",
      "type": "google_storage_bucket",
      "name": "terraform_state",
      "change": {
        "actions": ["create"],   # or ["update"], ["delete"], ["no-op"]
        ...
      }
    },
    ...
  ]
"""

from __future__ import annotations

# Human-readable description prefixes by GCP resource type
_RESOURCE_TYPE_LABELS: dict[str, str] = {
    "google_storage_bucket": "GCS bucket",
    "google_storage_bucket_versioning": "GCS bucket versioning",
    "google_storage_bucket_iam_binding": "GCS bucket IAM binding",
    "google_compute_instance": "Compute VM",
    "google_compute_network": "VPC network",
    "google_compute_subnetwork": "VPC subnetwork",
    "google_compute_firewall": "Firewall rule",
    "google_container_cluster": "GKE cluster",
    "google_container_node_pool": "GKE node pool",
    "google_sql_database_instance": "Cloud SQL instance",
    "google_sql_database": "Cloud SQL database",
    "google_sql_user": "Cloud SQL user",
    "google_project_iam_binding": "Project IAM binding",
    "google_project_iam_member": "Project IAM member",
    "google_service_account": "Service account",
    "google_service_account_key": "Service account key",
    "google_secret_manager_secret": "Secret Manager secret",
    "google_secret_manager_secret_version": "Secret Manager version",
    "google_filestore_instance": "Filestore NFS",
    "random_id": "Random ID",
}


def _human_description(resource_type: str, resource_name: str) -> str:
    """Produce a friendly description from resource type and name."""
    label = _RESOURCE_TYPE_LABELS.get(resource_type, resource_type)
    return f"{label}: {resource_name}"


class TerraformPlanParser:
    """Parse `terraform show -json` plan output into structured summaries."""

    @staticmethod
    def parse(plan: dict) -> dict:
        """Parse a plan dict and return counts and resource summaries.

        Args:
            plan: Dict from `json.loads(terraform show -json output)`.

        Returns:
            {
                "add_count": int,
                "change_count": int,
                "destroy_count": int,
                "total": int,
                "resources": [
                    {
                        "address": str,
                        "type": str,
                        "name": str,
                        "action": str,      # "create" | "update" | "delete"
                        "description": str,
                    },
                    ...
                ],
            }
        """
        resources = []
        add_count = 0
        change_count = 0
        destroy_count = 0

        for rc in plan.get("resource_changes", []):
            change = rc.get("change", {})
            actions = change.get("actions", [])

            if not actions or actions == ["no-op"]:
                continue

            # Determine canonical action from the actions list
            if "create" in actions and "delete" in actions:
                action = "replace"
                add_count += 1
                destroy_count += 1
            elif "create" in actions:
                action = "create"
                add_count += 1
            elif "update" in actions:
                action = "update"
                change_count += 1
            elif "delete" in actions:
                action = "delete"
                destroy_count += 1
            else:
                continue

            resource_type = rc.get("type", "unknown")
            resource_name = rc.get("name", "unknown")
            address = rc.get("address", f"{resource_type}.{resource_name}")

            resources.append(
                {
                    "address": address,
                    "type": resource_type,
                    "name": resource_name,
                    "action": action,
                    "description": _human_description(resource_type, resource_name),
                }
            )

        return {
            "add_count": add_count,
            "change_count": change_count,
            "destroy_count": destroy_count,
            # Total counts actual Terraform operations, not unique resource
            # entries.  A "replace" generates both a destroy and a create,
            # so add_count + change_count + destroy_count is the correct
            # number of apply_complete events Terraform will emit.
            "total": add_count + change_count + destroy_count,
            "resources": resources,
        }
