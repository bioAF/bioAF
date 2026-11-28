"""Read-only EKS, CloudWatch Logs and IAM lookups for the AWS deploy guardrails (plans 04B, 04C).

The endpoint policy needs the live cluster (identity and endpoint settings) and whether the
cluster's control-plane log group already exists; the role-path step needs whether a role or
instance profile recorded in state still exists. boto3 stays behind this adapter boundary, the
same way the other AWS adapters authenticate through the ambient instance profile. Nothing here
changes AWS state.
"""

from __future__ import annotations


def _client(service: str, region: str | None):
    """The single seam tests patch to inject fake clients."""
    import boto3

    return boto3.client(service, region_name=region or None)


def describe_cluster(name: str, region: str | None) -> dict:
    """The live ``DescribeCluster`` record (arn, createdAt, resourcesVpcConfig, ...)."""
    return _client("eks", region).describe_cluster(name=name)["cluster"]


def log_group_exists(name: str, region: str | None) -> bool:
    """Whether a CloudWatch log group with exactly this name exists."""
    response = _client("logs", region).describe_log_groups(logGroupNamePrefix=name)
    return any(group.get("logGroupName") == name for group in response.get("logGroups") or [])


def _missing(exc: Exception) -> bool:
    return (getattr(exc, "response", None) or {}).get("Error", {}).get("Code") == "NoSuchEntity"


def role_exists(name: str) -> bool:
    """Whether the IAM role exists. Any error other than NoSuchEntity is raised, never guessed."""
    try:
        _client("iam", None).get_role(RoleName=name)
    except Exception as exc:
        if _missing(exc):
            return False
        raise
    return True


def instance_profile_exists(name: str) -> bool:
    """Whether the IAM instance profile exists. Errors other than NoSuchEntity are raised."""
    try:
        _client("iam", None).get_instance_profile(InstanceProfileName=name)
    except Exception as exc:
        if _missing(exc):
            return False
        raise
    return True
