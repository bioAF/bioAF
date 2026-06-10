"""Guard: the pre-ADR-033 package-management API surface is gone.

The ``/api/packages`` router (conda/pip package search, dependency tree, and
install/remove/pin/unpin) was superseded by versioned environment builds
(ADR-033): package changes are made by editing a Dockerfile/conda YAML and
creating a new environment version. The write endpoints had become
``NotImplementedError`` stubs and nothing (frontend, SDK, CLI) called the
router. These tests fail if the routes are ever re-mounted.

The requests carry an admin token so the auth middleware passes them through to
routing; a removed route then resolves to 404 rather than the middleware's 401.
"""

import pytest


@pytest.mark.asyncio
async def test_packages_search_route_removed(client, admin_token):
    resp = await client.get(
        "/api/packages/search?query=scanpy",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_packages_dependencies_route_removed(client, admin_token):
    resp = await client.get(
        "/api/packages/dependencies?package_name=scanpy&source=conda&environment=bioaf-scrna",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_packages_install_route_removed(client, admin_token):
    resp = await client.post(
        "/api/packages/install",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_packages_remove_route_removed(client, admin_token):
    resp = await client.post(
        "/api/packages/remove",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
