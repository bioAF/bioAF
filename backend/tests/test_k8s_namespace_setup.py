"""Tests for K8s namespace setup (spec tests 10-11).

Tests that the compute adapter creates namespace, service account, and role binding,
and skips creation if they already exist.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    return KubernetesComputeProvider()


class TestNamespaceSetupCreatesResources:
    @pytest.mark.asyncio
    async def test_creates_namespace_sa_and_rolebinding(self, adapter):
        """Test 10: namespace setup creates namespace, service account, and role binding."""
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        # Simulate namespace not found (404)
        from kubernetes.client.rest import ApiException

        mock_core_v1.read_namespace.side_effect = ApiException(status=404)
        mock_core_v1.create_namespace.return_value = MagicMock()
        mock_core_v1.create_namespaced_service_account.return_value = MagicMock()
        mock_rbac_v1.create_namespaced_role_binding.return_value = MagicMock()

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await adapter.ensure_pipeline_namespace("bioaf-pipelines")

        mock_core_v1.create_namespace.assert_called_once()
        mock_core_v1.create_namespaced_service_account.assert_called_once()
        mock_rbac_v1.create_namespaced_role_binding.assert_called_once()

        # Verify namespace name
        ns_body = mock_core_v1.create_namespace.call_args[1]["body"]
        assert ns_body.metadata.name == "bioaf-pipelines"

        # Verify service account name and namespace
        sa_call = mock_core_v1.create_namespaced_service_account.call_args
        assert sa_call[1]["namespace"] == "bioaf-pipelines"
        assert sa_call[1]["body"].metadata.name == "bioaf-pipeline-runner"


class TestNamespaceSetupSkipsIfExists:
    @pytest.mark.asyncio
    async def test_skips_creation_if_namespace_exists(self, adapter):
        """Test 11: namespace setup skips if resources already exist."""
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        # Simulate namespace already exists
        mock_core_v1.read_namespace.return_value = MagicMock()

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await adapter.ensure_pipeline_namespace("bioaf-pipelines")

        mock_core_v1.create_namespace.assert_not_called()
        mock_core_v1.create_namespaced_service_account.assert_not_called()
        mock_rbac_v1.create_namespaced_role_binding.assert_not_called()


class TestNamespaceSetupAnnotatesWorkloadIdentity:
    """Without an iam.gke.io/gcp-service-account annotation on the KSA, pods
    in the bioaf-pipelines namespace cannot obtain a GCP identity under GKE
    Workload Identity (workload_metadata=GKE_METADATA), and Nextflow fails
    with 'storage.objects.get denied' on GCS reads. These tests pin the
    annotation behavior."""

    @pytest.mark.asyncio
    async def test_creates_sa_with_workload_identity_annotation(self, adapter):
        """When gcp_sa_email is provided, the new SA carries the WI annotation."""
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        from kubernetes.client.rest import ApiException

        mock_core_v1.read_namespace.side_effect = ApiException(status=404)

        sa_email = "bioaf-pipeline-runner@my-proj.iam.gserviceaccount.com"
        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await adapter.ensure_pipeline_namespace("bioaf-pipelines", gcp_sa_email=sa_email)

        sa_call = mock_core_v1.create_namespaced_service_account.call_args
        sa_body = sa_call[1]["body"]
        annotations = sa_body.metadata.annotations or {}
        assert annotations.get("iam.gke.io/gcp-service-account") == sa_email

    @pytest.mark.asyncio
    async def test_creates_sa_without_annotation_when_no_email(self, adapter):
        """When gcp_sa_email is empty, no WI annotation is set (avoids stamping a bogus value)."""
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        from kubernetes.client.rest import ApiException

        mock_core_v1.read_namespace.side_effect = ApiException(status=404)

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await adapter.ensure_pipeline_namespace("bioaf-pipelines")

        sa_call = mock_core_v1.create_namespaced_service_account.call_args
        sa_body = sa_call[1]["body"]
        annotations = sa_body.metadata.annotations or {}
        assert "iam.gke.io/gcp-service-account" not in annotations

    @pytest.mark.asyncio
    async def test_patches_annotation_when_namespace_already_exists(self, adapter):
        """Existing namespaces created before WI was wired must be patched on next call.
        Without this, deployments stay broken until manual intervention."""
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        # Namespace already exists.
        mock_core_v1.read_namespace.return_value = MagicMock()

        # Existing SA has no WI annotation.
        existing_sa = MagicMock()
        existing_sa.metadata.annotations = None
        mock_core_v1.read_namespaced_service_account.return_value = existing_sa

        sa_email = "bioaf-pipeline-runner@my-proj.iam.gserviceaccount.com"
        # Reset _namespace_ready so the function actually runs.
        adapter._namespace_ready = False
        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await adapter.ensure_pipeline_namespace("bioaf-pipelines", gcp_sa_email=sa_email)

        patch_call = mock_core_v1.patch_namespaced_service_account.call_args
        assert patch_call is not None, "patch_namespaced_service_account must be called"
        assert patch_call[1]["name"] == "bioaf-pipeline-runner"
        assert patch_call[1]["namespace"] == "bioaf-pipelines"
        body = patch_call[1]["body"]
        assert body["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] == sa_email

    @pytest.mark.asyncio
    async def test_does_not_patch_when_annotation_matches(self, adapter):
        """If the existing annotation already matches, skip the patch (idempotent)."""
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        mock_core_v1.read_namespace.return_value = MagicMock()

        sa_email = "bioaf-pipeline-runner@my-proj.iam.gserviceaccount.com"
        existing_sa = MagicMock()
        existing_sa.metadata.annotations = {"iam.gke.io/gcp-service-account": sa_email}
        mock_core_v1.read_namespaced_service_account.return_value = existing_sa

        adapter._namespace_ready = False
        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await adapter.ensure_pipeline_namespace("bioaf-pipelines", gcp_sa_email=sa_email)

        mock_core_v1.patch_namespaced_service_account.assert_not_called()
