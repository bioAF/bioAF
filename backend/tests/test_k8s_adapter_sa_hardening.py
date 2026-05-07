"""Tests that K8s adapters route through credential_injector under SA hardening.

Pre-fix bug: each K8s adapter (notebooks, compute, cellxgene) had a private
`_get_gcp_credentials` that called json.loads on the stored
`gcp_service_account_key`. Under vm_default mode that row is empty, so the
whole adapter raised JSONDecodeError on first use. The adapters now delegate
to credential_injector.load_gcp_credentials so impersonated bootstrap creds
work transparently.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.cellxgene import kubernetes as cellxgene_k8s
from app.adapters.compute import kubernetes as compute_k8s
from app.adapters.notebooks import kubernetes as notebook_k8s


def _vm_default_cfg() -> dict:
    return {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    }


def test_notebook_adapter_get_gcp_token_uses_credential_injector():
    cfg = _vm_default_cfg()
    fake_creds = MagicMock()
    fake_creds.token = "ya29.fake"
    with (
        patch(
            "app.services.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ) as load,
        patch("google.auth.transport.requests.Request"),
    ):
        token = notebook_k8s._get_gcp_token(cfg)
    load.assert_called_once_with(cfg)
    fake_creds.refresh.assert_called_once()
    assert token == "ya29.fake"


def test_compute_adapter_get_gcp_token_uses_credential_injector():
    cfg = _vm_default_cfg()
    fake_creds = MagicMock()
    fake_creds.token = "ya29.fake"
    with (
        patch(
            "app.services.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ) as load,
        patch("google.auth.transport.requests.Request"),
    ):
        token = compute_k8s._get_gcp_token(cfg)
    load.assert_called_once_with(cfg)
    assert token == "ya29.fake"


def test_cellxgene_adapter_get_gcp_token_uses_credential_injector():
    cfg = _vm_default_cfg()
    fake_creds = MagicMock()
    fake_creds.token = "ya29.fake"
    with (
        patch(
            "app.services.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ) as load,
        patch("google.auth.transport.requests.Request"),
    ):
        token = cellxgene_k8s._get_gcp_token(cfg)
    load.assert_called_once_with(cfg)
    assert token == "ya29.fake"


def test_compute_adapter_resolve_cfg_treats_null_string_as_missing():
    """`stack_deployment` writes 'null' as a sentinel; readers must normalize it."""
    cfg = {"gke_cluster_name": "null"}
    assert compute_k8s._resolve_cfg(cfg, "gke_cluster_name", "GKE_CLUSTER_NAME") == ""
    cfg = {"gke_cluster_name": "bioaf-prod"}
    assert compute_k8s._resolve_cfg(cfg, "gke_cluster_name", "GKE_CLUSTER_NAME") == "bioaf-prod"


def test_compute_get_cluster_metrics_returns_fallback_on_null_cluster_name(monkeypatch):
    """When gke_cluster_name is the literal 'null', skip the GKE call entirely.

    Pre-fix this caused 'projects/.../clusters/null' to be sent to the GKE API,
    spamming Cloud Logging with PERMISSION_DENIED on a non-existent cluster.
    """
    import asyncio

    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = compute_k8s.KubernetesComputeProvider()
    provider._cluster_config = {
        "gke_cluster_name": "null",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
    }

    async def _no_load():
        return provider._cluster_config

    monkeypatch.setattr(provider, "load_cluster_config", _no_load)
    # _get_gke_client should not be called when cluster_name is null
    provider._get_gke_client = MagicMock(side_effect=AssertionError("should not be called"))

    result = asyncio.get_event_loop().run_until_complete(provider._k8s_get_cluster_metrics())
    assert result == {
        "cpu_utilization_pct": 0.0,
        "memory_utilization_pct": 0.0,
        "cost_burn_rate_hourly": 0.0,
        "node_pools": [],
    }


@pytest.mark.asyncio
async def test_k8s_submit_job_reloads_cluster_config_before_sync_helpers():
    """Regression: pipeline launch failed with "No GKE cluster endpoint in
    platform_config" when the backend started before compute deploy. The
    sync _get_api_client used by ensure_pipeline_namespace etc. does NOT
    reload cluster_config; only _get_api_client_async does. _k8s_submit_job
    must call _ensure_cluster_config_fresh() first so subsequent sync
    helpers see the freshly-loaded config.
    """
    provider = compute_k8s.KubernetesComputeProvider()
    provider._mode = "k8s"
    provider._cluster_config = None
    provider._namespace_ready = True

    fresh = AsyncMock(return_value=None)

    async def _fake_read_creds():
        return ("vm_default", "")

    with (
        patch.object(provider, "_ensure_cluster_config_fresh", new=fresh),
        patch.object(provider, "_get_k8s_batch_client", return_value=MagicMock()),
        patch.object(provider, "_get_k8s_core_client", return_value=MagicMock()),
        patch.object(provider, "_read_gcp_credentials", new=_fake_read_creds),
        patch.object(provider, "_ensure_gcs_secret", return_value=False),
    ):
        await provider._k8s_submit_job(
            {
                "run_id": 99,
                "pipeline_name": "regression",
                "container_image": "alpine:3.19",
                "command": ["/bin/sh", "-c", "true"],
                "namespace": "bioaf-pipelines",
                "input_files": [],
                "parameters": {},
            }
        )

    fresh.assert_awaited()


def test_ensure_cluster_config_fresh_skips_when_no_session_factory():
    """Test contexts have no session_factory; helper must no-op so seeded
    _cluster_config (e.g., raw_bucket_name) survives the call.
    """
    import asyncio

    provider = compute_k8s.KubernetesComputeProvider()
    provider._session_factory = None
    provider._cluster_config = {"raw_bucket_name": "bioaf-test"}

    # load_cluster_config would clobber to {} without the no-op guard
    provider.load_cluster_config = AsyncMock(side_effect=AssertionError("must not be called"))

    asyncio.get_event_loop().run_until_complete(provider._ensure_cluster_config_fresh())
    assert provider._cluster_config == {"raw_bucket_name": "bioaf-test"}
