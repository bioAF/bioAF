"""Phase 4b: the active backend's ProviderCapabilities are exposed at bootstrap.

The frontend useCapabilities() hook reads the active stack's capability flags from
/api/bootstrap/status (the surface it already fetches on load) to gate UI on what
the backend can actually do. Capabilities are deployment detail, so they are only
returned to authenticated callers (the same gating as smtp_configured, pentest #3).
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_capabilities_exposed_at_bootstrap_when_authenticated(client: AsyncClient, admin_token: str):
    resp = await client.get(
        "/api/bootstrap/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "capabilities" in data
    caps = data["capabilities"]
    # The test stack is kubernetes/GCS/GCE, so the K8s/GCS/GCE capabilities are
    # declared true and the reserved Phase-9 flags are false.
    assert caps["cost_estimation"] is True
    assert caps["ssh_exec"] is True
    assert caps["signed_url_upload"] is True
    assert caps["cellxgene"] is True
    assert caps["work_nodes"] is True
    assert caps["messaging"] is False
    assert caps["billing"] is False


@pytest.mark.asyncio
async def test_capabilities_omitted_when_unauthenticated(client: AsyncClient):
    """Capabilities are deployment detail; do not leak them to anonymous callers."""
    resp = await client.get("/api/bootstrap/status")
    assert resp.status_code == 200
    assert "capabilities" not in resp.json()
