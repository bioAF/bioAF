"""This AWS installation's ownership identity (security plan 04B).

The narrowed app policy lets the app change EC2 resources only when they carry the ownership tag,
and create IAM roles only under the installer's permissions boundary. The installer, the Terraform
modules and the app derive both from the account and region, so no extra configuration is needed.
One bioAF installation per account (the fixed ``bioaf-app`` role) means the pair is unique.

Keep in step with ``installer/aws_iam.sh`` and the modules' ``installation_id`` / ``role_boundary``
locals.
"""

from __future__ import annotations

INSTALLATION_TAG = "bioaf:installation"
ROLE_BOUNDARY_NAME = "bioaf-app-created-role-boundary"


def installation_id(account_id: str | None, region: str | None) -> str | None:
    """The ownership tag value, or None before the account and region are known."""
    if not account_id or not region or "null" in (account_id, region):
        return None
    return f"{account_id}-{region}"


def role_boundary_arn(account_id: str | None) -> str | None:
    """The permissions boundary every app-created role must carry."""
    if not account_id or account_id == "null":
        return None
    return f"arn:aws:iam::{account_id}:policy/{ROLE_BOUNDARY_NAME}"


def ownership_tags(account_id: str | None, region: str | None) -> list[dict]:
    """The ownership tag as an EC2 ``Tags`` list (empty when the identity is unknown)."""
    value = installation_id(account_id, region)
    return [{"Key": INSTALLATION_TAG, "Value": value}] if value else []


# New roles and instance profiles the app creates live under this IAM path (plan 04B). Only
# CreateRole sets a path and nothing changes it, so the app's ownership of these roles cannot be
# claimed by tagging; roles created before keep the path already in state.
NEW_ROLE_PATH = "/bioaf/"


def role_path_from_state(state: dict) -> str:
    """The path of the module's roles already in state, else the path for new roles."""
    for resource in state.get("resources") or []:
        if resource.get("mode") == "managed" and resource.get("type") == "aws_iam_role":
            for instance in resource.get("instances") or []:
                path = (instance.get("attributes") or {}).get("path")
                if path:
                    return path
    return NEW_ROLE_PATH
