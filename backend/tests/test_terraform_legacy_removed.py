"""Guard: the legacy /api/terraform router (TerraformService-backed) is gone.

It served the orphaned per-component terraform theater (confirm/cancel of no-op
runs) plus duplicate run reads. The single terraform engine is TerraformExecutor,
exposed at /api/v1/infrastructure/terraform. These legacy routes must stay unmounted.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/terraform/runs"),
        ("get", "/api/terraform/runs/active"),
        ("get", "/api/terraform/runs/1"),
        ("post", "/api/terraform/runs/1/confirm"),
        ("post", "/api/terraform/runs/1/cancel"),
    ],
)
async def test_legacy_terraform_router_removed(client: AsyncClient, admin_token: str, method: str, path: str):
    response = await client.request(
        method.upper(),
        path,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
