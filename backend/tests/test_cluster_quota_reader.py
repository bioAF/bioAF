"""Tests for reading a region's live quota through the compute adapter.

The fit calculation in `cluster_quota` is pure; this is the seam that feeds it
real numbers. It is on the compute provider because that is what owns node pools
and autoscaling, and it is optional: a provider with no reader reports "unknown"
rather than raising, so the Components page stays usable on backends that cannot
answer (SLURM today, AWS until its reader lands).

Failing open is the rule everywhere here. A missing IAM role or a cloud API blip
must degrade to "not verified", never to a blocked operator.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.compute.slurm import SlurmComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._cluster_config = {
        "gcp_project_id": "bioaf-495400",
        "gcp_region": "us-central1",
        "gcp_service_account_key": '{"type": "service_account", "project_id": "bioaf-495400"}',
    }
    return provider


def _fake_region(pairs):
    """A compute_v1 Region carrying the quota shapes the real API returns."""
    region = MagicMock()
    region.quotas = []
    for metric, usage, limit in pairs:
        quota = MagicMock()
        quota.metric = metric
        quota.usage = usage
        quota.limit = limit
        region.quotas.append(quota)
    return region


# -- a provider that cannot answer says so ----------------------------------


@pytest.mark.asyncio
async def test_a_provider_without_a_reader_reports_unknown():
    """SLURM has no quota concept. It must return None, not raise.

    Every other method on this stub raises NotImplementedError; this one must
    not, because the preflight calls it on every render.
    """
    assert await SlurmComputeProvider().get_regional_quotas() is None


# -- the GCP reader ----------------------------------------------------------


@pytest.mark.asyncio
async def test_gcp_reader_maps_a_regions_quotas(adapter):
    """The exact metrics the incident turned on, in the shape the API returns."""
    fake_client = MagicMock()
    fake_client.get.return_value = _fake_region(
        [
            ("SSD_TOTAL_GB", 30.0, 500.0),
            ("DISKS_TOTAL_GB", 90.0, 4096.0),
            ("CPUS", 7.0, 200.0),
        ]
    )

    with (
        patch("google.cloud.compute_v1.RegionsClient", return_value=fake_client),
        patch.object(adapter, "_get_gcp_credentials", return_value=MagicMock(), create=True),
    ):
        quotas = await adapter.get_regional_quotas()

    assert quotas is not None
    assert quotas["SSD_TOTAL_GB"].limit == 500.0
    assert quotas["SSD_TOTAL_GB"].usage == 30.0
    assert quotas["DISKS_TOTAL_GB"].limit == 4096.0
    assert quotas["CPUS"].usage == 7.0


@pytest.mark.asyncio
async def test_gcp_reader_asks_for_the_configured_project_and_region(adapter):
    fake_client = MagicMock()
    fake_client.get.return_value = _fake_region([("CPUS", 7.0, 200.0)])

    with (
        patch("google.cloud.compute_v1.RegionsClient", return_value=fake_client),
        patch.object(adapter, "_get_gcp_credentials", return_value=MagicMock(), create=True),
    ):
        await adapter.get_regional_quotas()

    kwargs = fake_client.get.call_args.kwargs
    assert kwargs["project"] == "bioaf-495400"
    assert kwargs["region"] == "us-central1"


@pytest.mark.asyncio
async def test_gcp_reader_reports_unknown_when_the_cloud_call_fails(adapter):
    """A missing compute.regions.get permission is the expected case here."""
    fake_client = MagicMock()
    fake_client.get.side_effect = PermissionError("compute.regions.get denied")

    with (
        patch("google.cloud.compute_v1.RegionsClient", return_value=fake_client),
        patch.object(adapter, "_get_gcp_credentials", return_value=MagicMock(), create=True),
    ):
        assert await adapter.get_regional_quotas() is None


@pytest.mark.asyncio
async def test_gcp_reader_reports_unknown_without_a_project(adapter):
    """No project configured means no call to make, and still no exception."""
    adapter._cluster_config = {"gcp_region": "us-central1"}
    assert await adapter.get_regional_quotas() is None


@pytest.mark.asyncio
async def test_an_explicit_region_overrides_the_configured_one(adapter):
    fake_client = MagicMock()
    fake_client.get.return_value = _fake_region([("CPUS", 1.0, 10.0)])

    with (
        patch("google.cloud.compute_v1.RegionsClient", return_value=fake_client),
        patch.object(adapter, "_get_gcp_credentials", return_value=MagicMock(), create=True),
    ):
        await adapter.get_regional_quotas(region="europe-west1")

    assert fake_client.get.call_args.kwargs["region"] == "europe-west1"


# -- capability declaration --------------------------------------------------


def test_kubernetes_declares_quota_introspection(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    assert KubernetesComputeProvider().capabilities().quota_introspection is True


def test_a_stub_backend_does_not_declare_quota_introspection():
    assert SlurmComputeProvider().capabilities().quota_introspection is False


# -- counting what a pool currently holds ------------------------------------


@pytest.mark.asyncio
async def test_pool_node_count_reads_live_nodes_not_the_initial_count(adapter):
    """`NodePoolStatus.current_nodes` is GKE's initial_node_count, which on an
    autoscaling pool is the seed value and not what is running. Netting quota
    against that would systematically understate the pool's own usage and could
    block an operator re-applying a config on their own running nodes.
    """
    fake_core = MagicMock()
    fake_core.list_node.return_value = MagicMock(items=[MagicMock(), MagicMock(), MagicMock()])

    with patch.object(adapter, "_get_k8s_core_client", return_value=fake_core):
        assert await adapter.count_pool_nodes("bioaf-pipelines") == 3

    selector = fake_core.list_node.call_args.kwargs["label_selector"]
    assert "bioaf-pipelines" in selector


@pytest.mark.asyncio
async def test_pool_node_count_is_zero_for_a_scaled_to_zero_pool(adapter):
    fake_core = MagicMock()
    fake_core.list_node.return_value = MagicMock(items=[])

    with patch.object(adapter, "_get_k8s_core_client", return_value=fake_core):
        assert await adapter.count_pool_nodes("bioaf-pipelines") == 0


@pytest.mark.asyncio
async def test_pool_node_count_is_unknown_when_the_cluster_is_unreachable(adapter):
    """Unknown must be distinguishable from zero: zero grants headroom that
    netting-out would then hand back, unknown must not."""
    with patch.object(adapter, "_get_k8s_core_client", side_effect=RuntimeError("no cluster")):
        assert await adapter.count_pool_nodes("bioaf-pipelines") is None
