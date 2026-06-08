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
