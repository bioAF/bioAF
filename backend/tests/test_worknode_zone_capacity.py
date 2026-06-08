"""Phase 6: GCE zone-capacity probing is reachable through WorkNodeProvider.

The probe (formerly app/services/zone_capacity_probe.py, which imported compute_v1
outside adapters/) now lives in the adapter layer and is exposed as
WorkNodeProvider.probe_zone_capacity. The adapter resolves project + credentials
internally (fresh, via a forced config reload) and delegates to the relocated
probe_zones, so callers never name a backend or touch the SDK.
"""

import pytest

from app.adapters.work_nodes import gce_capacity
from app.adapters.work_nodes.gce import GCEWorkNodeProvider


@pytest.mark.asyncio
async def test_zone_capacity_via_worknode_adapter(monkeypatch):
    adapter = GCEWorkNodeProvider()
    adapter._gcp_config = {"gcp_project_id": "bioaf-test"}

    async def fake_load(force: bool = False):
        return adapter._gcp_config

    monkeypatch.setattr(adapter, "load_gcp_config", fake_load)
    monkeypatch.setattr(adapter, "_get_gcp_credentials", lambda: "fake-creds")

    captured: dict = {}

    def fake_probe(*, zones, project_id, credentials, machine_type="e2-medium"):
        captured.update(
            zones=zones,
            project_id=project_id,
            credentials=credentials,
            machine_type=machine_type,
        )
        return zones[0]

    monkeypatch.setattr(gce_capacity, "probe_zones", fake_probe)

    result = await adapter.probe_zone_capacity(["us-central1-a", "us-central1-b"])

    assert result == "us-central1-a"
    # The adapter resolved project + credentials itself from its config.
    assert captured["project_id"] == "bioaf-test"
    assert captured["credentials"] == "fake-creds"
    assert captured["zones"] == ["us-central1-a", "us-central1-b"]


@pytest.mark.asyncio
async def test_zone_capacity_propagates_exhaustion(monkeypatch):
    adapter = GCEWorkNodeProvider()
    adapter._gcp_config = {"gcp_project_id": "bioaf-test"}

    async def fake_load(force: bool = False):
        return adapter._gcp_config

    monkeypatch.setattr(adapter, "load_gcp_config", fake_load)
    monkeypatch.setattr(adapter, "_get_gcp_credentials", lambda: "fake-creds")

    def fake_probe(*, zones, project_id, credentials, machine_type="e2-medium"):
        raise gce_capacity.AllZonesExhaustedError("no capacity anywhere")

    monkeypatch.setattr(gce_capacity, "probe_zones", fake_probe)

    with pytest.raises(gce_capacity.AllZonesExhaustedError):
        await adapter.probe_zone_capacity(["us-central1-a"])
