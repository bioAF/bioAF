"""Phase 4b: server-side capability guards return a clean 4xx, not a 500.

require_capability() on capability-dependent endpoints raises CapabilityNotSupported
when the active backend cannot perform the action; one registered exception handler
maps it to a 422 envelope. These tests force an incapable backend by patching the
registry's active-capability surface to the minimal (all-false) set.
"""

import pytest
from httpx import AsyncClient

from app.adapters import registry
from app.adapters.capabilities import ProviderCapabilities


def _force_incapable(monkeypatch):
    monkeypatch.setattr(registry, "get_active_capabilities", lambda: ProviderCapabilities())


@pytest.mark.asyncio
async def test_ssh_connect_pipeline_4xx_when_ssh_exec_unsupported(client: AsyncClient, admin_token, monkeypatch):
    _force_incapable(monkeypatch)
    resp = await client.post(
        "/api/pipeline-runs/1/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "capability_not_supported"
    assert body["capability"] == "ssh_exec"


@pytest.mark.asyncio
async def test_ssh_connect_session_4xx_when_ssh_exec_unsupported(client: AsyncClient, admin_token, monkeypatch):
    _force_incapable(monkeypatch)
    resp = await client.post(
        "/api/sessions/1/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["capability"] == "ssh_exec"


@pytest.mark.asyncio
async def test_signed_url_upload_4xx_when_unsupported(client: AsyncClient, admin_token, monkeypatch):
    _force_incapable(monkeypatch)
    resp = await client.post(
        "/api/files/upload/initiate",
        json={"filename": "reads.fastq.gz"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["capability"] == "signed_url_upload"


@pytest.mark.asyncio
async def test_cellxgene_publish_4xx_when_unsupported(client: AsyncClient, admin_token, monkeypatch):
    _force_incapable(monkeypatch)
    resp = await client.post(
        "/api/cellxgene/publish",
        json={"file_id": 1, "dataset_name": "demo"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["capability"] == "cellxgene"


@pytest.mark.asyncio
async def test_work_node_launch_4xx_when_unsupported(client: AsyncClient, admin_token, monkeypatch):
    _force_incapable(monkeypatch)
    resp = await client.post(
        "/api/v1/work-nodes/sessions",
        json={"project_id": 1, "environment_version_id": 1, "machine_type": "n2-standard-4"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["capability"] == "work_nodes"


@pytest.mark.asyncio
async def test_cluster_autoscale_config_4xx_when_unsupported(client: AsyncClient, admin_token, monkeypatch):
    _force_incapable(monkeypatch)
    resp = await client.post(
        "/api/v1/infrastructure/cluster/config",
        json={"k8s_pipeline_max_nodes": 10},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["capability"] == "autoscaling"
