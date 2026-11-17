"""Cluster status, stack-view detail, and cost metrics read the deployed GKE cluster.

All three address the cluster at its deployed location (gke_cluster_location), never the
installation default gcp_region. A missing location is discovered by cluster name and recorded;
when it cannot be determined, status and detail fail clearly and metrics report unavailable
rather than zero activity. Provider failures keep each method's existing contract.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.kubernetes.cluster_location import ClusterLocationUnknown
from app.adapters.models import ClusterMetrics
from app.settings_contract import ComputeMode

DEPLOYED = "projects/lab-proj-1/locations/europe-west1/clusters/bioaf-lab-abc123"


def _gke_cluster():
    cluster = MagicMock()
    cluster.name = "bioaf-lab-abc123"
    cluster.status = 2
    cluster.current_node_count = 1
    pool = MagicMock()
    pool.name = "bioaf-platform"
    pool.config.machine_type = "e2-standard-2"
    pool.config.spot = False
    pool.autoscaling.min_node_count = 1
    pool.autoscaling.max_node_count = 1
    pool.initial_node_count = 1
    pool.status = 2
    cluster.node_pools = [pool]
    return cluster


def _adapter(monkeypatch, *, location="europe-west1", listed=()):
    adapter = KubernetesComputeProvider(mode=ComputeMode.KUBERNETES)
    adapter._cluster_config = {
        "gke_cluster_endpoint": "https://10.0.0.1",
        "gke_cluster_name": "bioaf-lab-abc123",
        "gcp_project_id": "lab-proj-1",
        # The installation default, restored after a deploy that overrode it.
        "gcp_region": "us-central1",
        "gke_cluster_location": location,
    }
    client = MagicMock()
    client.get_cluster.return_value = _gke_cluster()
    client.list_clusters.return_value = SimpleNamespace(clusters=list(listed))
    monkeypatch.setattr(adapter, "_get_gke_client", lambda: client)
    return adapter, client


async def _all_three(adapter):
    await adapter.get_cluster_detail()
    await adapter.get_cluster_status()
    return await adapter.get_cluster_metrics()


@pytest.mark.asyncio
async def test_all_three_read_the_cluster_at_its_deployed_location(monkeypatch):
    adapter, client = _adapter(monkeypatch)

    metrics = await _all_three(adapter)

    assert [c.kwargs["name"] for c in client.get_cluster.call_args_list] == [DEPLOYED] * 3
    client.list_clusters.assert_not_called()
    assert metrics.unavailable is None
    assert metrics.cost_burn_rate_hourly > 0


@pytest.mark.asyncio
async def test_an_install_without_a_recorded_location_discovers_it_once(monkeypatch):
    listed = [SimpleNamespace(name="bioaf-lab-abc123", location="europe-west1")]
    adapter, client = _adapter(monkeypatch, location=None, listed=listed)

    await _all_three(adapter)

    client.list_clusters.assert_called_once_with(parent="projects/lab-proj-1/locations/-")
    assert [c.kwargs["name"] for c in client.get_cluster.call_args_list] == [DEPLOYED] * 3
    assert adapter._cluster_config["gke_cluster_location"] == "europe-west1"


@pytest.mark.parametrize("location", [None, "null", "not a location"])
@pytest.mark.asyncio
async def test_an_undiscoverable_location_is_reported_not_guessed(monkeypatch, location):
    adapter, client = _adapter(monkeypatch, location=location, listed=[])

    with pytest.raises(ClusterLocationUnknown):
        await adapter.get_cluster_detail()
    with pytest.raises(ClusterLocationUnknown):
        await adapter.get_cluster_status()
    metrics = await adapter.get_cluster_metrics()

    client.get_cluster.assert_not_called()
    assert metrics.unavailable == "cluster_location_unknown"
    assert metrics.node_pools == []


@pytest.mark.asyncio
async def test_a_provider_failure_keeps_the_metrics_fallback_and_says_unavailable(monkeypatch):
    adapter, client = _adapter(monkeypatch)
    client.get_cluster.side_effect = RuntimeError("403 Forbidden")

    metrics = await adapter.get_cluster_metrics()

    assert metrics.cost_burn_rate_hourly == 0.0
    assert metrics.node_pools == []
    assert metrics.unavailable == "provider_unavailable"


@pytest.mark.asyncio
async def test_a_discovery_failure_is_a_provider_failure(monkeypatch):
    adapter, client = _adapter(monkeypatch, location=None)
    client.list_clusters.side_effect = RuntimeError("403 Forbidden")

    metrics = await adapter.get_cluster_metrics()
    assert metrics.unavailable == "provider_unavailable"
    with pytest.raises(RuntimeError):
        await adapter.get_cluster_detail()


@pytest.mark.asyncio
async def test_cost_sync_records_no_compute_cost_when_the_cluster_cannot_be_read(session, admin_user, monkeypatch):
    """Unknown is not zero: keep the day's existing compute/node estimates instead of writing $0."""
    from app.models.cost_record import CostRecord
    from app.services import cost_service
    from app.services.billing_period import billing_period

    org_id = admin_user.organization_id
    period = billing_period()
    session.add(
        CostRecord(
            organization_id=org_id,
            record_date=period.today,
            component="compute",
            cost_amount=Decimal("12.50"),
            metadata_json={"source": "estimated"},
        )
    )
    await session.flush()

    async def unavailable_metrics():
        return ClusterMetrics(cpu_utilization_pct=0.0, unavailable="cluster_location_unknown")

    async def storage_metrics():
        return SimpleNamespace(total_cost_monthly_usd=30.0)

    monkeypatch.setattr(
        cost_service, "get_compute_adapter", lambda: SimpleNamespace(get_cluster_metrics=unavailable_metrics)
    )
    monkeypatch.setattr(
        cost_service, "get_storage_adapter", lambda: SimpleNamespace(get_storage_metrics=storage_metrics)
    )

    await cost_service.CostService._sync_from_adapters(session, org_id, period)
    await session.flush()

    rows = (
        await session.execute(
            select(CostRecord.component, CostRecord.cost_amount).where(
                CostRecord.organization_id == org_id, CostRecord.record_date == period.today
            )
        )
    ).all()
    amounts = {component: amount for component, amount in rows}
    assert amounts["compute"] == Decimal("12.50")
    assert "node" not in amounts
    assert amounts["storage"] > 0
    assert isinstance(period.today, date)
