import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_smtp_settings_persist_after_save(client: AsyncClient, admin_token: str):
    """SMTP settings saved via POST should be retrievable via GET."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Save SMTP settings
    resp = await client.post(
        "/api/bootstrap/configure-smtp",
        json={
            "host": "smtp.example.com",
            "port": 465,
            "username": "user@example.com",
            "password": "s3cret",
            "from_address": "noreply@example.com",
            "encryption": "ssl",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Retrieve settings -- they should come back (password masked)
    resp = await client.get("/api/bootstrap/smtp-settings", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["host"] == "smtp.example.com"
    assert data["port"] == 465
    assert data["username"] == "user@example.com"
    assert data["from_address"] == "noreply@example.com"
    assert data["encryption"] == "ssl"
    # Password should be masked
    assert data["password"] != "s3cret"
    assert "***" in data["password"]


@pytest.mark.asyncio
async def test_smtp_settings_stored_in_database(client: AsyncClient, admin_token: str, session: AsyncSession):
    """SMTP credentials should be persisted in the organizations table, not just in memory."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    await client.post(
        "/api/bootstrap/configure-smtp",
        json={
            "host": "mail.test.io",
            "port": 587,
            "username": "testuser",
            "password": "testpass",
            "from_address": "bot@test.io",
            "encryption": "starttls",
        },
        headers=headers,
    )

    # Verify the DB has the actual values
    result = await session.execute(
        text(
            "SELECT smtp_host, smtp_port, smtp_username, smtp_from_address, smtp_encryption FROM organizations LIMIT 1"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert row.smtp_host == "mail.test.io"
    assert row.smtp_port == 587
    assert row.smtp_username == "testuser"
    assert row.smtp_from_address == "bot@test.io"
    assert row.smtp_encryption == "starttls"


@pytest.mark.asyncio
async def test_smtp_encryption_field_accepted(client: AsyncClient, admin_token: str):
    """POST should accept and store the encryption field."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    for enc in ["starttls", "ssl", "none"]:
        resp = await client.post(
            "/api/bootstrap/configure-smtp",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "u",
                "password": "p",
                "from_address": "a@b.com",
                "encryption": enc,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        resp = await client.get("/api/bootstrap/smtp-settings", headers=headers)
        assert resp.json()["encryption"] == enc


@pytest.mark.asyncio
async def test_smtp_get_requires_admin(client: AsyncClient, viewer_token: str):
    """Non-admin users should not be able to read SMTP settings."""
    resp = await client.get(
        "/api/bootstrap/smtp-settings",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_smtp_get_returns_empty_when_not_configured(client: AsyncClient, admin_token: str):
    """GET should return empty/default values when SMTP has not been configured."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.get("/api/bootstrap/smtp-settings", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["host"] == ""
    assert data["configured"] is False


@pytest.mark.asyncio
async def test_test_email_sends_to_specified_address(client: AsyncClient, admin_token: str):
    """Test email endpoint should accept a destination address and attempt delivery."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # First configure SMTP
    await client.post(
        "/api/bootstrap/configure-smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "u",
            "password": "p",
            "from_address": "noreply@example.com",
            "encryption": "starttls",
        },
        headers=headers,
    )

    # Send test email with destination (mock the SMTP connection)
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with (
        patch("app.services.email_service.EmailService.is_configured", return_value=True),
        patch("smtplib.SMTP", return_value=mock_server) as mock_smtp,
    ):
        resp = await client.post(
            "/api/bootstrap/test-smtp",
            json={"to": "recipient@example.com"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert data["to"] == "recipient@example.com"
        mock_smtp.assert_called_once()
        mock_server.send_message.assert_called_once()
        # Verify the destination address was in the message
        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["To"] == "recipient@example.com"


@pytest.mark.asyncio
async def test_test_email_fails_when_smtp_not_configured(client: AsyncClient, admin_token: str):
    """Test email should fail gracefully when SMTP is not configured."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.post(
        "/api/bootstrap/test-smtp",
        json={"to": "someone@example.com"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"


@pytest.mark.asyncio
async def test_test_email_surfaces_smtp_error_detail(client: AsyncClient, admin_token: str):
    """SMTP errors should be returned with human-readable detail."""
    import smtplib

    headers = {"Authorization": f"Bearer {admin_token}"}

    await client.post(
        "/api/bootstrap/configure-smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "u",
            "password": "p",
            "from_address": "noreply@example.com",
            "encryption": "starttls",
        },
        headers=headers,
    )

    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    mock_server.send_message.side_effect = smtplib.SMTPSenderRefused(
        550, b"Sender address rejected: not verified", "noreply@example.com"
    )

    with (
        patch("app.services.email_service.EmailService.is_configured", return_value=True),
        patch("smtplib.SMTP", return_value=mock_server),
    ):
        resp = await client.post(
            "/api/bootstrap/test-smtp",
            json={"to": "recipient@example.com"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "rejected the From address" in data["detail"]
        assert "not verified" in data["detail"]
