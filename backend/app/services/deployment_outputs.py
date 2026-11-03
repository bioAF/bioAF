"""Terraform outputs and the platform_config keys they feed.

Three contracts live here and they are not interchangeable, even where their keys
overlap: what an apply persists, what a teardown clears, and which buckets get
emptied. Each is declared on its own so that changing one cannot change another.

Each caller picks its own blank-output policy: storage deploy and every sync skip
blanks, so a missing output cannot erase a known value. Compute and image-build
deploy write "null", so a deploy leaves no stale identity behind.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

# Terraform output name -> platform_config key
OutputMap = Mapping[str, str]


class BlankOutputs(Enum):
    SKIP = "skip"
    WRITE_NULL = "write_null"


_STORAGE_BUCKET_OUTPUTS: OutputMap = {
    "ingest_bucket_name": "ingest_bucket_name",
    "raw_bucket_name": "raw_bucket_name",
    "working_bucket_name": "working_bucket_name",
    "results_bucket_name": "results_bucket_name",
    "references_bucket_name": "references_bucket_name",
    "literature_bucket_name": "literature_bucket_name",
    "config_backups_bucket_name": "config_backups_bucket_name",
}

# Used by the deploy hook and the storage update route.
STORAGE_DEPLOY_OUTPUTS: OutputMap = {
    **_STORAGE_BUCKET_OUTPUTS,
    "pubsub_topic_name": "pubsub_topic_name",
    "pubsub_subscription_name": "pubsub_subscription_name",
}

# Sync recovers bucket names only. It has never touched Pub/Sub.
STORAGE_SYNC_OUTPUTS: OutputMap = _STORAGE_BUCKET_OUTPUTS

IMAGE_BUILD_OUTPUTS: OutputMap = {"codebuild_project_name": "aws_codebuild_project"}

COMPUTE_OUTPUTS: OutputMap = {
    "cluster_name": "gke_cluster_name",
    "cluster_endpoint": "gke_cluster_endpoint",
    "cluster_ca_cert": "gke_cluster_ca_cert",
    # Per-workload Workload Identity runner SA emails. Persisted here (not only
    # in the full-deploy hook) so the "Check for Infrastructure Updates" path
    # records a newly added runner SA -- e.g. cellxgene -- in platform_config;
    # the adapters read these to annotate their runner KSA.
    "notebook_runner_sa_email": "notebook_runner_sa_email",
    "cellxgene_runner_sa_email": "cellxgene_runner_sa_email",
}

# AWS (EKS) outputs the same cluster_* keys, plus the IRSA role ARNs + OIDC issuer
# the AWS pod-identity / cluster-auth seams need, the Cluster Autoscaler's IRSA
# role, and the networking + instance profile EC2 work nodes launch into. The EKS
# module reuses the GCP runner output names, with ARNs as their values. These keys
# are additive and never written on GCP.
COMPUTE_AWS_OUTPUTS: OutputMap = {
    "pipeline_runner_role_arn": "pipeline_runner_role_arn",
    "notebook_runner_sa_email": "notebook_runner_role_arn",
    "cellxgene_runner_sa_email": "cellxgene_runner_role_arn",
    "oidc_provider_arn": "eks_oidc_provider_arn",
    "oidc_provider_url": "eks_oidc_provider_url",
    "cluster_autoscaler_role_arn": "cluster_autoscaler_role_arn",
    "work_node_subnet_id": "aws_work_node_subnet_id",
    "work_node_security_group_id": "aws_work_node_security_group_id",
    "work_node_instance_profile": "aws_work_node_instance_profile",
}

# Set to "null" by compute teardown. Everything else survives it.
COMPUTE_TEARDOWN_CLEARED_KEYS: tuple[str, ...] = (
    "gke_cluster_name",
    "gke_cluster_endpoint",
    "gke_cluster_ca_cert",
)

# Set to "null" by storage destroy, so the next deploy generates fresh names.
STORAGE_DESTROY_CLEARED_KEYS: tuple[str, ...] = (
    "storage_deployed",
    "ingest_bucket_name",
    "raw_bucket_name",
    "working_bucket_name",
    "results_bucket_name",
    "references_bucket_name",
    "literature_bucket_name",
    "config_backups_bucket_name",
    "pubsub_topic_name",
    "pubsub_subscription_name",
)

# Emptied before storage destroy, because the buckets have force_destroy=false.
# Only bucket names belong here: each value goes straight to the storage adapter.
STORAGE_BUCKETS_TO_EMPTY: tuple[str, ...] = (
    "ingest_bucket_name",
    "raw_bucket_name",
    "working_bucket_name",
    "results_bucket_name",
    "references_bucket_name",
    "literature_bucket_name",
    "config_backups_bucket_name",
)


async def persist_outputs(
    session: AsyncSession,
    outputs: dict,
    mapping: OutputMap,
    blanks: BlankOutputs,
) -> dict[str, str]:
    """Write mapped Terraform outputs to platform_config; the caller commits.

    Returns {config_key: value} for the outputs that had a value.
    """
    from app.platform.platform_config_service import PlatformConfigService

    populated: dict[str, str] = {}
    for output_name, config_key in mapping.items():
        value = outputs.get(output_name, {}).get("value", "")
        if value:
            await PlatformConfigService.set(session, config_key, value)
            populated[config_key] = value
        elif blanks is BlankOutputs.WRITE_NULL:
            await PlatformConfigService.set(session, config_key, "null")
    return populated
