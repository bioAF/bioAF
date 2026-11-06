import pytest


@pytest.mark.asyncio
async def test_gitops_status_not_initialized(client, admin_token):
    """Status returns not_configured when no repo exists."""
    response = await client.get(
        "/api/gitops/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["initialized"] is False
    assert data["status"] == "not_configured"


@pytest.mark.asyncio
async def test_gitops_status_viewer_can_access(client, viewer_token):
    """Viewer can check GitOps status."""
    response = await client.get(
        "/api/gitops/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_gitops_initialize_admin_only(client, viewer_token):
    """Viewer cannot initialize GitOps."""
    response = await client.post(
        "/api/gitops/initialize",
        json={},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_gitops_commits_requires_auth(client, viewer_token):
    """Viewer cannot list commits (comp_bio+ required)."""
    response = await client.get(
        "/api/gitops/commits",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_gitops_commits_comp_bio_can_access(client, comp_bio_token):
    """Comp Bio can list commits but gets error when repo not initialized."""
    response = await client.get(
        "/api/gitops/commits",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400  # repo not initialized


@pytest.mark.asyncio
async def test_gitops_file_requires_repo(client, admin_token):
    """File read requires initialized repo."""
    response = await client.get(
        "/api/gitops/file?path=test.txt",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
