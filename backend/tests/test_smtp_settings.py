from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
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


def _fake_settings():
    """An isolated stand-in for the settings singleton with SMTP fields defaulted."""
    return SimpleNamespace(
        smtp_host="",
        smtp_port=0,
        smtp_username="",
        smtp_password="",
        smtp_from_address="",
        smtp_encryption="",
        smtp_configured=False,
    )


def test_apply_smtp_to_settings_writes_all_fields(monkeypatch):
    """The shared helper is the single writer of SMTP config into settings.

    Both the save path (configure_smtp) and the startup load path go through it,
    so the set of fields that make up SMTP config lives in exactly one place.
    Patch the module-level settings to an isolated object so the assertions read
    exactly what the helper wrote, with no dependency on (or mutation of) the
    process-wide singleton shared by the rest of the suite.
    """
    from app.services import email_service

    fake = _fake_settings()
    monkeypatch.setattr(email_service, "settings", fake)

    email_service.apply_smtp_to_settings(
        host="h.example.com",
        port=2525,
        username="bob",
        password="pw",
        from_address="from@example.com",
        encryption="ssl",
    )

    assert fake.smtp_host == "h.example.com"
    assert fake.smtp_port == 2525
    assert fake.smtp_username == "bob"
    assert fake.smtp_password == "pw"
    assert fake.smtp_from_address == "from@example.com"
    assert fake.smtp_encryption == "ssl"
    assert fake.smtp_configured is True


@pytest.mark.asyncio
async def test_startup_load_decrypts_smtp_password(session: AsyncSession, db_engine, monkeypatch):
    """Persisted SMTP settings loaded at startup must yield the decrypted
    password in settings, not the stored Fernet ciphertext.

    Regression: the startup loader read the encrypted smtp_password column via
    raw SQL, which bypasses the EncryptedString decryptor, so the in-memory
    settings held the ciphertext and the app authenticated to the SMTP server
    with the encrypted blob as the password. The symptom was outbound SMTP
    breaking on every restart until the password was re-saved.
    """
    from app.models.organization import Organization
    from app.services import email_service

    plaintext_password = "super-secret-smtp-pw"

    # Persist an org with SMTP configured; the ORM encrypts smtp_password at rest.
    org = Organization(
        name="Acme",
        smtp_configured=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="mailer",
        smtp_password=plaintext_password,
        smtp_from_address="noreply@example.com",
        smtp_encryption="starttls",
    )
    session.add(org)
    await session.commit()

    # Sanity: the column is stored as Fernet ciphertext, not plaintext.
    stored = (await session.execute(text("SELECT smtp_password FROM organizations LIMIT 1"))).scalar_one()
    assert stored != plaintext_password
    assert stored.startswith("gAAAA")  # Fernet token prefix

    # Isolate the settings object the loader writes to, so the assertions read
    # exactly what was loaded without depending on the shared global singleton.
    fake = _fake_settings()
    monkeypatch.setattr(email_service, "settings", fake)

    # Load through a fresh session so the value is read back from the DB through
    # the EncryptedString decryptor, mirroring a real restart.
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh:
        applied = await email_service.load_persisted_smtp_settings(fresh)

    assert applied is True
    assert fake.smtp_configured is True
    assert fake.smtp_host == "smtp.example.com"
    assert fake.smtp_username == "mailer"
    # The crux: settings hold the decrypted plaintext, never the ciphertext.
    assert fake.smtp_password == plaintext_password
    assert not fake.smtp_password.startswith("gAAAA")


@pytest.mark.asyncio
async def test_startup_load_skips_when_not_configured(session: AsyncSession, db_engine, monkeypatch):
    """The loader is a no-op when no org has SMTP configured, leaving settings untouched."""
    from app.models.organization import Organization
    from app.services import email_service

    org = Organization(name="Acme", smtp_configured=False)
    session.add(org)
    await session.commit()

    fake = _fake_settings()
    fake.smtp_host = "sentinel.example.com"
    fake.smtp_password = "sentinel-pw"
    monkeypatch.setattr(email_service, "settings", fake)

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh:
        applied = await email_service.load_persisted_smtp_settings(fresh)

    assert applied is False
    # Settings left exactly as they were.
    assert fake.smtp_host == "sentinel.example.com"
    assert fake.smtp_password == "sentinel-pw"


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
