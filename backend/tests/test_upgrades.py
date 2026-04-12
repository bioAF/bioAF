import json
import os
import tempfile
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


# ---------------------------------------------------------------------------
# Update execution tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_upgrade_writes_trigger_file(client: AsyncClient, admin_token: str):
    """POST /api/upgrades/execute should write a trigger JSON file for the host agent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        requests_dir = os.path.join(tmpdir, "requests")
        status_dir = os.path.join(tmpdir, "status")
        os.makedirs(requests_dir)
        os.makedirs(status_dir)

        with (
            patch("app.services.upgrade_service.settings") as mock_settings,
            patch("app.api.upgrades.UpgradeService.check_for_updates") as mock_check,
        ):
            mock_settings.app_version = "0.6.4"
            mock_settings.update_requests_dir = requests_dir
            mock_settings.update_status_dir = status_dir
            mock_check.return_value = {
                "current_version": "0.6.4",
                "latest_version": "0.7.0",
                "update_available": True,
            }

            response = await client.post(
                "/api/upgrades/execute",
                json={"target_version": "0.7.0"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["to_version"] == "0.7.0"
        assert "upgrade_id" in data

        # Verify trigger file was written
        trigger_files = [f for f in os.listdir(requests_dir) if f.endswith(".json")]
        assert len(trigger_files) == 1
        with open(os.path.join(requests_dir, trigger_files[0])) as f:
            trigger = json.loads(f.read())
        assert trigger["version"] == "0.7.0"
        assert trigger["upgrade_id"] == data["upgrade_id"]


@pytest.mark.asyncio
async def test_execute_upgrade_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    """Only admins with infrastructure:deploy can execute upgrades."""
    response = await client.post(
        "/api/upgrades/execute",
        json={"target_version": "0.7.0"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_execute_upgrade_rejects_same_version(client: AsyncClient, admin_token: str):
    """Should reject an upgrade to the currently running version."""
    from app.config import settings

    response = await client.post(
        "/api/upgrades/execute",
        json={"target_version": settings.app_version},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_execute_upgrade_rejects_invalid_version(client: AsyncClient, admin_token: str):
    """Should reject versions that don't match semver format."""
    response = await client.post(
        "/api/upgrades/execute",
        json={"target_version": "not-a-version"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_update_status_idle(client: AsyncClient, admin_token: str):
    """GET /api/upgrades/status should return idle when no update is running."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.upgrade_service.settings") as mock_settings:
            mock_settings.update_status_dir = tmpdir
            mock_settings.app_version = "0.6.4"

            response = await client.get(
                "/api/upgrades/status",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "idle"


@pytest.mark.asyncio
async def test_get_update_status_in_progress(client: AsyncClient, admin_token: str):
    """GET /api/upgrades/status should return in_progress when an update is running."""
    with tempfile.TemporaryDirectory() as tmpdir:
        status_file = os.path.join(tmpdir, "current.json")
        with open(status_file, "w") as f:
            json.dump(
                {
                    "status": "in_progress",
                    "from_version": "0.6.4",
                    "to_version": "0.7.0",
                    "step": "build",
                },
                f,
            )

        with patch("app.services.upgrade_service.settings") as mock_settings:
            mock_settings.update_status_dir = tmpdir
            mock_settings.app_version = "0.6.4"

            response = await client.get(
                "/api/upgrades/status",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "in_progress"
    assert data["step"] == "build"
    assert data["to_version"] == "0.7.0"


@pytest.mark.asyncio
async def test_resolve_pending_upgrade_on_startup():
    """Pending 'started' upgrades should be resolved when the app version matches the target."""
    from app.services.upgrade_service import UpgradeService

    mock_upgrade = MagicMock()
    mock_upgrade.id = 1
    mock_upgrade.to_version = "0.6.4"
    mock_upgrade.status = "started"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_upgrade]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.flush = AsyncMock()

    with patch("app.services.upgrade_service.settings") as mock_settings:
        mock_settings.app_version = "0.6.4"

        await UpgradeService.resolve_pending_upgrades(mock_session)

    assert mock_upgrade.status == "completed"
    assert mock_upgrade.completed_at is not None
