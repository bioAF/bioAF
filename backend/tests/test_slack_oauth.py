"""Tests for Slack OAuth integration: auth URL, callback, status, disconnect, channels, mappings."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


# ---- Auth URL ----


@pytest.mark.asyncio
async def test_slack_auth_url_returns_url(client: AsyncClient, admin_token: str):
    """Auth URL endpoint returns a valid Slack OAuth URL."""
    with (
        patch("app.api.slack_oauth.settings") as api_settings,
        patch("app.services.slack_oauth_service.settings") as svc_settings,
    ):
        for s in (api_settings, svc_settings):
            s.slack_client_id = "test-client-id"
            s.slack_client_secret = "test-secret"
            s.jwt_secret_key = "dev-secret-key-change-in-production"
            s.jwt_algorithm = "HS256"

        response = await client.get(
            "/api/notifications/slack/auth-url",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "auth_url" in data
    assert "https://slack.com/oauth/v2/authorize" in data["auth_url"]
    assert "client_id=test-client-id" in data["auth_url"]
    # Scopes are URL-encoded (colons become %3A)
    assert "chat" in data["auth_url"]
    assert "channels" in data["auth_url"]


@pytest.mark.asyncio
async def test_slack_auth_url_missing_client_id(client: AsyncClient, admin_token: str):
    """Auth URL returns 400 when Slack client ID is not configured."""
    with patch("app.api.slack_oauth.settings") as mock_settings:
        mock_settings.slack_client_id = ""
        mock_settings.slack_client_secret = ""

        response = await client.get(
            "/api/notifications/slack/auth-url",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 400
    assert "not" in response.json()["detail"].lower()


# ---- Status ----


@pytest.mark.asyncio
async def test_slack_status_not_connected(client: AsyncClient, admin_token: str):
    """Status returns connected=false when no installation exists."""
    response = await client.get(
        "/api/notifications/slack/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is False
    assert data["team_name"] is None


@pytest.mark.asyncio
async def test_slack_status_connected(client: AsyncClient, admin_token: str, admin_user, session):
    """Status returns connected=true with team info when installation exists."""
    from app.models.notification import SlackInstallation

    install = SlackInstallation(
        organization_id=admin_user.organization_id,
        team_id="T12345",
        team_name="Test Workspace",
        bot_token="xoxb-test-token",
        bot_user_id="U12345",
        installed_by=admin_user.id,
    )
    session.add(install)
    await session.flush()
    await session.commit()

    response = await client.get(
        "/api/notifications/slack/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is True
    assert data["team_name"] == "Test Workspace"
    assert data["team_id"] == "T12345"
    assert data["enabled"] is True


# ---- OAuth callback ----


@pytest.mark.asyncio
async def test_slack_callback_exchanges_code(client: AsyncClient, admin_token: str, admin_user):
    """Callback exchanges auth code for token and stores installation."""
    from jose import jwt as jose_jwt
    from app.config import settings

    state = jose_jwt.encode(
        {"org_id": admin_user.organization_id, "user_id": admin_user.id},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    token_data = {
        "ok": True,
        "access_token": "xoxb-new-bot-token",
        "team": {"id": "T99999", "name": "New Workspace"},
        "bot_user_id": "UBOT99",
        "authed_user": {"id": "UAUTH99"},
    }

    with patch(
        "app.services.slack_oauth_service.SlackOAuthService.exchange_code", new_callable=AsyncMock
    ) as mock_exchange:
        mock_exchange.return_value = token_data

        response = await client.get(
            f"/api/notifications/slack/callback?code=test-code&state={state}",
            follow_redirects=False,
        )

    # Callback redirects browser back to the Slack settings page
    assert response.status_code == 307
    assert "/settings/slack?connected=true" in response.headers["location"]


@pytest.mark.asyncio
async def test_slack_callback_invalid_state(client: AsyncClient):
    """Callback rejects invalid state token."""
    response = await client.get(
        "/api/notifications/slack/callback?code=test-code&state=bad-state",
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_slack_callback_missing_code(client: AsyncClient):
    """Callback rejects missing code parameter."""
    response = await client.get(
        "/api/notifications/slack/callback?state=something",
    )
    assert response.status_code == 422 or response.status_code == 400


# ---- Disconnect ----


@pytest.mark.asyncio
async def test_slack_disconnect(client: AsyncClient, admin_token: str, admin_user, session):
    """Disconnect removes the Slack installation and channel mappings."""
    from app.models.notification import SlackInstallation, SlackChannelMapping

    install = SlackInstallation(
        organization_id=admin_user.organization_id,
        team_id="T12345",
        team_name="Test Workspace",
        bot_token="xoxb-test-token",
        bot_user_id="U12345",
        installed_by=admin_user.id,
    )
    session.add(install)

    mapping = SlackChannelMapping(
        organization_id=admin_user.organization_id,
        channel_id="C12345",
        channel_name="#general",
    )
    session.add(mapping)
    await session.flush()
    await session.commit()

    response = await client.delete(
        "/api/notifications/slack/disconnect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["disconnected"] is True

    # Verify status is now disconnected
    response = await client.get(
        "/api/notifications/slack/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.json()["connected"] is False


@pytest.mark.asyncio
async def test_slack_disconnect_not_connected(client: AsyncClient, admin_token: str):
    """Disconnect returns 404 when no installation exists."""
    response = await client.delete(
        "/api/notifications/slack/disconnect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


# ---- Channel listing ----


@pytest.mark.asyncio
async def test_slack_list_channels(client: AsyncClient, admin_token: str, admin_user, session):
    """List channels returns Slack channels from the workspace."""
    from app.models.notification import SlackInstallation

    install = SlackInstallation(
        organization_id=admin_user.organization_id,
        team_id="T12345",
        team_name="Test Workspace",
        bot_token="xoxb-test-token",
        bot_user_id="U12345",
        installed_by=admin_user.id,
    )
    session.add(install)
    await session.flush()
    await session.commit()

    mock_channels = [
        {"id": "C001", "name": "general", "is_private": False},
        {"id": "C002", "name": "comp-bio", "is_private": False},
        {"id": "C003", "name": "alerts", "is_private": True},
    ]

    with patch(
        "app.services.slack_oauth_service.SlackOAuthService.list_channels",
        new_callable=AsyncMock,
        return_value=mock_channels,
    ):
        response = await client.get(
            "/api/notifications/slack/channels",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    channels = response.json()
    assert len(channels) == 3
    assert channels[0]["name"] == "general"
    assert channels[2]["is_private"] is True


@pytest.mark.asyncio
async def test_slack_list_channels_not_connected(client: AsyncClient, admin_token: str):
    """List channels returns 404 when not connected."""
    response = await client.get(
        "/api/notifications/slack/channels",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


# ---- Channel mappings CRUD ----


@pytest.mark.asyncio
async def test_slack_channel_mapping_crud(client: AsyncClient, admin_token: str, admin_user, session):
    """Full CRUD cycle for channel mappings."""
    from app.models.notification import SlackInstallation

    install = SlackInstallation(
        organization_id=admin_user.organization_id,
        team_id="T12345",
        team_name="Test Workspace",
        bot_token="xoxb-test-token",
        bot_user_id="U12345",
        installed_by=admin_user.id,
    )
    session.add(install)
    await session.flush()
    await session.commit()

    # Create
    response = await client.post(
        "/api/notifications/slack/channel-mappings",
        json={
            "channel_id": "C001",
            "channel_name": "#comp-bio",
            "event_types": ["pipeline.completed", "pipeline.failed"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mapping_id = response.json()["id"]
    assert response.json()["channel_name"] == "#comp-bio"
    assert len(response.json()["event_types_json"]) == 2

    # List
    response = await client.get(
        "/api/notifications/slack/channel-mappings",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Update
    response = await client.put(
        f"/api/notifications/slack/channel-mappings/{mapping_id}",
        json={"event_types": ["pipeline.completed"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()["event_types_json"]) == 1

    # Delete
    response = await client.delete(
        f"/api/notifications/slack/channel-mappings/{mapping_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    # Verify empty
    response = await client.get(
        "/api/notifications/slack/channel-mappings",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_slack_channel_mapping_not_connected(client: AsyncClient, admin_token: str):
    """Channel mapping creation fails when not connected."""
    response = await client.post(
        "/api/notifications/slack/channel-mappings",
        json={"channel_id": "C001", "channel_name": "#test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


# ---- Viewer forbidden ----


@pytest.mark.asyncio
async def test_slack_endpoints_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    """Viewer role cannot access Slack configuration endpoints."""
    endpoints = [
        ("GET", "/api/notifications/slack/auth-url"),
        ("GET", "/api/notifications/slack/channels"),
        ("DELETE", "/api/notifications/slack/disconnect"),
        ("POST", "/api/notifications/slack/channel-mappings"),
    ]
    for method, url in endpoints:
        if method == "GET":
            response = await client.get(url, headers={"Authorization": f"Bearer {viewer_token}"})
        elif method == "DELETE":
            response = await client.delete(url, headers={"Authorization": f"Bearer {viewer_token}"})
        else:
            response = await client.post(url, json={}, headers={"Authorization": f"Bearer {viewer_token}"})
        assert response.status_code == 403, f"{method} {url} should be forbidden for viewer"


# ---- Slack adapter with bot token ----


@pytest.mark.asyncio
async def test_slack_adapter_chat_post_message():
    """SlackChannel.deliver posts via chat.postMessage API."""
    from app.services.notification_channels.slack_adapter import SlackChannel

    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch("app.services.notification_channels.slack_adapter.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        success, error_code = await SlackChannel.deliver(
            bot_token="xoxb-test-token",
            channel_id="C001",
            title="Pipeline Complete",
            message="nf-core/rnaseq finished successfully",
            severity="info",
        )

    assert success is True
    assert error_code == ""
    call_kwargs = mock_client.post.call_args
    assert "chat.postMessage" in str(call_kwargs)


@pytest.mark.asyncio
async def test_slack_adapter_webhook_fallback():
    """SlackChannel.deliver_webhook still works for legacy webhook URLs."""
    from app.services.notification_channels.slack_adapter import SlackChannel

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.notification_channels.slack_adapter.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = await SlackChannel.deliver_webhook(
            webhook_url="https://hooks.slack.com/services/T00/B00/xxx",
            title="Test",
            message="Test message",
            severity="info",
        )

    assert result is True


# ---- Test delivery uses new OAuth path ----


@pytest.mark.asyncio
async def test_test_delivery_slack_oauth(client: AsyncClient, admin_token: str, admin_user, session):
    """Test delivery uses OAuth installation when available."""
    from app.models.notification import SlackInstallation, SlackChannelMapping

    install = SlackInstallation(
        organization_id=admin_user.organization_id,
        team_id="T12345",
        team_name="Test Workspace",
        bot_token="xoxb-test-token",
        bot_user_id="U12345",
        installed_by=admin_user.id,
    )
    session.add(install)

    mapping = SlackChannelMapping(
        organization_id=admin_user.organization_id,
        channel_id="C001",
        channel_name="#alerts",
    )
    session.add(mapping)
    await session.flush()
    await session.commit()

    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch("app.services.notification_channels.slack_adapter.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = await client.post(
            "/api/notifications/test",
            json={"channel": "slack"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["channel"] == "slack"
    assert data["status"] == "sent"
