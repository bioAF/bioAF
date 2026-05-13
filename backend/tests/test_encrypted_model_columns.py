"""Tests that model columns flagged as sensitive are encrypted at rest.

Each test inserts a row via the ORM and then asserts two things:
1. The ORM read returns the original plaintext (round-trip via TypeDecorator).
2. A raw SQL read returns Fernet ciphertext (proves on-disk bytes are encrypted).

This is the contract: model code stays plaintext-only, while pg_dump and
anyone with raw DB access sees nothing but ciphertext.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import encryption_service


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_organization_secrets_encrypted_at_rest(db_engine):
    from app.models.organization import Organization

    async with _factory(db_engine)() as session:
        org = Organization(
            name="Acme",
            slack_client_secret="slack-secret-XYZ",
            slack_signing_secret="slack-signing-XYZ",
            smtp_password="smtp-pass-XYZ",
        )
        session.add(org)
        await session.commit()
        org_id = org.id

    async with _factory(db_engine)() as session:
        result = await session.execute(select(Organization).where(Organization.id == org_id))
        loaded = result.scalar_one()
        assert loaded.slack_client_secret == "slack-secret-XYZ"
        assert loaded.slack_signing_secret == "slack-signing-XYZ"
        assert loaded.smtp_password == "smtp-pass-XYZ"

        raw = (
            await session.execute(
                sa_text(
                    "SELECT slack_client_secret, slack_signing_secret, smtp_password FROM organizations WHERE id = :id"
                ),
                {"id": org_id},
            )
        ).one()
        for value in raw:
            assert value.startswith("gAAAA"), f"expected Fernet ciphertext, got {value!r}"
            assert encryption_service.looks_like_ciphertext(value)


@pytest.mark.asyncio
async def test_organization_public_columns_remain_plaintext(db_engine):
    """slack_client_id is the public half of the OAuth pair; never encrypt."""
    from app.models.organization import Organization

    async with _factory(db_engine)() as session:
        org = Organization(name="Acme", slack_client_id="public-id-123")
        session.add(org)
        await session.commit()
        org_id = org.id

    async with _factory(db_engine)() as session:
        raw = (
            await session.execute(
                sa_text("SELECT slack_client_id FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
        ).scalar_one()
        assert raw == "public-id-123"


@pytest.mark.asyncio
async def test_session_credential_ssh_private_key_encrypted(db_engine, admin_user):
    from app.models.session_credential import SessionCredential

    async with _factory(db_engine)() as session:
        cred = SessionCredential(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            username="alice",
            password_hash="bcrypt-hash-not-encrypted",
            ssh_public_key="ssh-rsa AAAA...",
            ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n-----END",
        )
        session.add(cred)
        await session.commit()
        cred_id = cred.id

    async with _factory(db_engine)() as session:
        loaded = (await session.execute(select(SessionCredential).where(SessionCredential.id == cred_id))).scalar_one()
        assert loaded.ssh_private_key == "-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n-----END"
        assert loaded.password_hash == "bcrypt-hash-not-encrypted"
        assert loaded.ssh_public_key == "ssh-rsa AAAA..."

        raw_priv = (
            await session.execute(
                sa_text("SELECT ssh_private_key FROM session_credentials WHERE id = :id"),
                {"id": cred_id},
            )
        ).scalar_one()
        assert raw_priv.startswith("gAAAA")

        raw_pub = (
            await session.execute(
                sa_text("SELECT ssh_public_key FROM session_credentials WHERE id = :id"),
                {"id": cred_id},
            )
        ).scalar_one()
        assert raw_pub == "ssh-rsa AAAA..."

        raw_hash = (
            await session.execute(
                sa_text("SELECT password_hash FROM session_credentials WHERE id = :id"),
                {"id": cred_id},
            )
        ).scalar_one()
        assert raw_hash == "bcrypt-hash-not-encrypted"


@pytest.mark.asyncio
async def test_compute_session_heartbeat_token_encrypted(db_engine, admin_user):
    from app.models.notebook_session import ComputeSession

    async with _factory(db_engine)() as session:
        cs = ComputeSession(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            session_type="notebook",
            resource_profile="standard",
            cpu_cores=2,
            memory_gb=4,
            status="pending",
            heartbeat_token="hb-token-XYZ-1234",
        )
        session.add(cs)
        await session.commit()
        cs_id = cs.id

    async with _factory(db_engine)() as session:
        loaded = (await session.execute(select(ComputeSession).where(ComputeSession.id == cs_id))).scalar_one()
        assert loaded.heartbeat_token == "hb-token-XYZ-1234"

        raw = (
            await session.execute(
                sa_text("SELECT heartbeat_token FROM compute_sessions WHERE id = :id"),
                {"id": cs_id},
            )
        ).scalar_one()
        assert raw.startswith("gAAAA")


@pytest.mark.asyncio
async def test_slack_installation_bot_token_encrypted(db_engine, admin_user):
    from app.models.notification import SlackInstallation

    async with _factory(db_engine)() as session:
        inst = SlackInstallation(
            organization_id=admin_user.organization_id,
            team_id="T123",
            team_name="Acme",
            bot_token="xoxb-secret-bot-token",
            bot_user_id="U123",
            installed_by=admin_user.id,
        )
        session.add(inst)
        await session.commit()
        inst_id = inst.id

    async with _factory(db_engine)() as session:
        loaded = (await session.execute(select(SlackInstallation).where(SlackInstallation.id == inst_id))).scalar_one()
        assert loaded.bot_token == "xoxb-secret-bot-token"

        raw = (
            await session.execute(
                sa_text("SELECT bot_token FROM slack_installations WHERE id = :id"),
                {"id": inst_id},
            )
        ).scalar_one()
        assert raw.startswith("gAAAA")


@pytest.mark.asyncio
async def test_slack_webhook_url_encrypted(db_engine, admin_user):
    """Slack webhook URLs are bearer tokens by another name; treat as sensitive."""
    from app.models.notification import SlackWebhook

    async with _factory(db_engine)() as session:
        wh = SlackWebhook(
            organization_id=admin_user.organization_id,
            name="alerts",
            webhook_url="https://hooks.slack.com/services/T/B/SECRETPATH",
        )
        session.add(wh)
        await session.commit()
        wh_id = wh.id

    async with _factory(db_engine)() as session:
        loaded = (await session.execute(select(SlackWebhook).where(SlackWebhook.id == wh_id))).scalar_one()
        assert loaded.webhook_url == "https://hooks.slack.com/services/T/B/SECRETPATH"

        raw = (
            await session.execute(
                sa_text("SELECT webhook_url FROM slack_webhooks WHERE id = :id"),
                {"id": wh_id},
            )
        ).scalar_one()
        assert raw.startswith("gAAAA")
