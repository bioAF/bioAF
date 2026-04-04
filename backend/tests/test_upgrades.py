from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_current_version(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/upgrades/current",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "current_version" in data
    assert data["app_name"] == "bioAF"


@pytest.mark.asyncio
async def test_get_current_version_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/upgrades/current",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_check_for_updates(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/upgrades/check",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "current_version" in data
    assert "latest_version" in data
    assert "update_available" in data


@pytest.mark.asyncio
async def test_check_for_updates_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/upgrades/check",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_upgrade_history_empty(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/upgrades/history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["upgrades"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_start_upgrade(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/upgrades/start",
        json={"target_version": "1.0.0"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert data["to_version"] == "1.0.0"
    assert "upgrade_id" in data


@pytest.mark.asyncio
async def test_confirm_upgrade(client: AsyncClient, admin_token: str):
    # Start first
    start_resp = await client.post(
        "/api/upgrades/start",
        json={"target_version": "1.0.0"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    upgrade_id = start_resp.json()["upgrade_id"]

    # Confirm
    response = await client.post(
        f"/api/upgrades/{upgrade_id}/confirm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_rollback_upgrade(client: AsyncClient, admin_token: str):
    # Start and confirm first
    start_resp = await client.post(
        "/api/upgrades/start",
        json={"target_version": "1.0.0"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    upgrade_id = start_resp.json()["upgrade_id"]

    await client.post(
        f"/api/upgrades/{upgrade_id}/confirm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Rollback
    response = await client.post(
        f"/api/upgrades/{upgrade_id}/rollback",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_confirm_nonexistent_upgrade(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/upgrades/99999/confirm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_upgrade_history_after_upgrade(client: AsyncClient, admin_token: str):
    # Start an upgrade
    await client.post(
        "/api/upgrades/start",
        json={"target_version": "2.0.0"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/upgrades/history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_version_check_service():
    """Test version check returns valid data."""
    from app.services.upgrade_service import UpgradeService, _clear_version_cache

    _clear_version_cache()
    result = await UpgradeService.check_for_updates(1)
    assert "current_version" in result
    assert "update_available" in result


@pytest.mark.asyncio
async def test_check_for_updates_detects_newer_version():
    """When GitHub reports a newer release, update_available should be True."""
    from app.services.upgrade_service import UpgradeService, _clear_version_cache

    _clear_version_cache()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tag_name": "v99.0.0",
        "body": "## What's new\n- Big improvements",
        "html_url": "https://github.com/not-that-guy-again/bioAF/releases/tag/v99.0.0",
    }

    with patch("app.services.upgrade_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await UpgradeService.check_for_updates(1)

    assert result["update_available"] is True
    assert result["latest_version"] == "99.0.0"
    assert result["changelog"] == "## What's new\n- Big improvements"
    assert "v99.0.0" in result["release_url"]


@pytest.mark.asyncio
async def test_check_for_updates_no_update_when_current():
    """When GitHub reports the same version, update_available should be False."""
    from app.config import settings
    from app.services.upgrade_service import UpgradeService, _clear_version_cache

    _clear_version_cache()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tag_name": f"v{settings.app_version}",
        "body": "Current release",
        "html_url": f"https://github.com/not-that-guy-again/bioAF/releases/tag/v{settings.app_version}",
    }

    with patch("app.services.upgrade_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await UpgradeService.check_for_updates(1)

    assert result["update_available"] is False
    assert result["latest_version"] == settings.app_version


@pytest.mark.asyncio
async def test_check_for_updates_handles_github_failure():
    """When GitHub API fails, return no update available gracefully."""
    from app.services.upgrade_service import UpgradeService, _clear_version_cache

    _clear_version_cache()

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.return_value = {"message": "Not Found"}

    with patch("app.services.upgrade_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await UpgradeService.check_for_updates(1)

    assert result["update_available"] is False
    assert result["current_version"] == result["latest_version"]


@pytest.mark.asyncio
async def test_check_for_updates_handles_network_error():
    """When the network call raises an exception, return no update gracefully."""
    from app.services.upgrade_service import UpgradeService, _clear_version_cache

    _clear_version_cache()

    with patch("app.services.upgrade_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client_cls.return_value = mock_client

        result = await UpgradeService.check_for_updates(1)

    assert result["update_available"] is False
