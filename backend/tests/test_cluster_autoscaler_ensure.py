"""Tests for KubernetesComputeProvider.ensure_cluster_autoscaler (Stage 6e incr 3).

DB-free: drives the EKS Cluster Autoscaler install with mocked k8s clients,
asserting all kube-system objects are created, the SA carries the IRSA
annotation, the install is idempotent on 409, and non-conflict errors propagate.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client.rest import ApiException

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.pod_identity.aws import EksIrsaPodIdentityProvider

ROLE_ARN = "arn:aws:iam::043671579834:role/bioaf-bioaf-8ec3ba-cluster-autoscaler"


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    # Force the AWS pod-identity provider so the SA gets the IRSA annotation
    # (the factory would default to GKE on an unconfigured/test install).
    provider._pod_identity_provider = EksIrsaPodIdentityProvider()
    # The connection's DB read is irrelevant here; stub it.
    provider._gke.load_cluster_config = AsyncMock(return_value={})
    return provider


def _clients(adapter):
    core, rbac, apps = MagicMock(), MagicMock(), MagicMock()
    adapter._get_k8s_core_client = MagicMock(return_value=core)
    adapter._get_k8s_rbac_client = MagicMock(return_value=rbac)
    adapter._get_k8s_apps_client = MagicMock(return_value=apps)
    return core, rbac, apps


async def _ensure(adapter):
    await adapter.ensure_cluster_autoscaler(
        role_arn=ROLE_ARN,
        cluster_name="bioaf-bioaf-8ec3ba",
        region="us-west-1",
    )


@pytest.mark.asyncio
async def test_ensure_creates_all_objects_and_annotates_sa(adapter):
    core, rbac, apps = _clients(adapter)

    await _ensure(adapter)

    core.create_namespaced_service_account.assert_called_once()
    assert core.create_namespaced_service_account.call_args.kwargs["namespace"] == "kube-system"
    sa_body = core.create_namespaced_service_account.call_args.kwargs["body"]
    assert sa_body["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == ROLE_ARN

    rbac.create_cluster_role.assert_called_once()
    rbac.create_cluster_role_binding.assert_called_once()
    rbac.create_namespaced_role.assert_called_once()
    rbac.create_namespaced_role_binding.assert_called_once()

    apps.create_namespaced_deployment.assert_called_once()
    dep = apps.create_namespaced_deployment.call_args.kwargs["body"]
    cmd = dep["spec"]["template"]["spec"]["containers"][0]["command"]
    assert "--cloud-provider=aws" in cmd


@pytest.mark.asyncio
async def test_ensure_reloads_cluster_config(adapter):
    _clients(adapter)
    await _ensure(adapter)
    adapter._gke.load_cluster_config.assert_awaited_once_with(force=True)


@pytest.mark.asyncio
async def test_ensure_is_idempotent_on_conflict(adapter):
    core, rbac, apps = _clients(adapter)
    conflict = ApiException(status=409, reason="Conflict")
    for m in (
        core.create_namespaced_service_account,
        rbac.create_cluster_role,
        rbac.create_cluster_role_binding,
        rbac.create_namespaced_role,
        rbac.create_namespaced_role_binding,
        apps.create_namespaced_deployment,
    ):
        m.side_effect = conflict

    # Must not raise; mutable objects get patched instead.
    await _ensure(adapter)

    core.patch_namespaced_service_account.assert_called_once()
    apps.patch_namespaced_deployment.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_propagates_non_conflict_errors(adapter):
    core, _, _ = _clients(adapter)
    core.create_namespaced_service_account.side_effect = ApiException(status=403, reason="Forbidden")

    with pytest.raises(ApiException):
        await _ensure(adapter)
