import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_components(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/components",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["components"]) > 0
    keys = [c["key"] for c in data["components"]]
    assert "slurm" in keys
    assert "jupyter" in keys
    assert "cellxgene" in keys


@pytest.mark.asyncio
async def test_viewer_can_list_components(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/components",
        headers={
            "Authorization": f"Bearer {viewer_token}",
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_component_dependencies_in_catalog(client: AsyncClient, admin_token: str):
    """Catalog dependency data is exposed on the list response (filestore -> slurm)."""
    response = await client.get(
        "/api/components",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 200
    by_key = {c["key"]: c for c in response.json()["components"]}
    assert "slurm" in by_key["filestore"]["dependencies"]


# The per-component enable/disable/configure surface on /api/components was orphaned
# theater: it drove the now-removed TerraformService (a no-op in any shipped image).
# The live enable/disable path is the admin-gated stack toggle
# (/api/v1/infrastructure/stack/components/{key}/toggle). These endpoints must stay
# gone so the duplication does not creep back.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/components/cellxgene/enable"),
        ("post", "/api/components/cellxgene/disable"),
        ("patch", "/api/components/cellxgene/configure"),
        ("get", "/api/components/slurm"),
    ],
)
async def test_orphaned_component_endpoints_removed(client: AsyncClient, admin_token: str, method: str, path: str):
    response = await client.request(
        method.upper(),
        path,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
