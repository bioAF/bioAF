"""Slack OAuth service - token exchange, channel listing, installation management."""

import logging
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.notification import SlackChannelMapping, SlackInstallation
from app.models.organization import Organization
from app.models.user import User

logger = logging.getLogger("bioaf.slack_oauth")

SLACK_OAUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_CONVERSATIONS_URL = "https://slack.com/api/conversations.list"
SLACK_SCOPES = "chat:write,channels:read,groups:read"


class SlackOAuthService:
    @staticmethod
    def build_auth_url(org_id: int, user_id: int, client_id_override: str = "") -> str:
        """Build the Slack OAuth authorization URL with signed state."""
        state = jwt.encode(
            {"org_id": org_id, "user_id": user_id},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        params = {
            "client_id": client_id_override or settings.slack_client_id,
            "scope": SLACK_SCOPES,
            "state": state,
        }
        return f"{SLACK_OAUTH_URL}?{urlencode(params)}"

    @staticmethod
    def decode_state(state: str) -> dict:
        """Decode and verify the signed state token."""
        try:
            return jwt.decode(
                state,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
            )
        except JWTError as e:
            raise ValueError(f"Invalid state token: {e}") from e

    @staticmethod
    async def exchange_code(
        code: str,
        client_id: str = "",
        client_secret: str = "",
    ) -> dict:
        """Exchange an authorization code for a bot token."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                SLACK_TOKEN_URL,
                data={
                    "client_id": client_id or settings.slack_client_id,
                    "client_secret": client_secret or settings.slack_client_secret,
                    "code": code,
                },
            )
        data = response.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            logger.error("Slack token exchange failed: %s", error)
            raise ValueError(f"Slack OAuth error: {error}")
        return data

    @staticmethod
    async def get_org_credentials(session: AsyncSession, org_id: int) -> tuple[str, str]:
        """Return (client_id, client_secret) from org DB or env var fallback."""
        result = await session.execute(select(Organization).where(Organization.id == org_id))
        org = result.scalar_one_or_none()
        if org and org.slack_client_id and org.slack_client_secret:
            return org.slack_client_id, org.slack_client_secret
        return settings.slack_client_id, settings.slack_client_secret

    @staticmethod
    async def save_installation(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        token_data: dict,
    ) -> SlackInstallation:
        """Store or replace the Slack installation for an org."""
        # Remove existing installation if any
        await session.execute(delete(SlackInstallation).where(SlackInstallation.organization_id == org_id))

        install = SlackInstallation(
            organization_id=org_id,
            team_id=token_data["team"]["id"],
            team_name=token_data["team"]["name"],
            bot_token=token_data["access_token"],
            bot_user_id=token_data.get("bot_user_id", ""),
            authed_user_id=token_data.get("authed_user", {}).get("id"),
            installed_by=user_id,
        )
        session.add(install)
        await session.flush()
        return install

    @staticmethod
    async def get_installation(session: AsyncSession, org_id: int) -> SlackInstallation | None:
        result = await session.execute(select(SlackInstallation).where(SlackInstallation.organization_id == org_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def disconnect(session: AsyncSession, org_id: int) -> bool:
        """Remove installation and all channel mappings for an org."""
        install = await SlackOAuthService.get_installation(session, org_id)
        if not install:
            return False

        await session.execute(delete(SlackChannelMapping).where(SlackChannelMapping.organization_id == org_id))
        await session.delete(install)
        await session.flush()
        return True

    @staticmethod
    async def list_channels(session: AsyncSession, org_id: int) -> list[dict]:
        """Fetch channels from Slack using the stored bot token."""
        install = await SlackOAuthService.get_installation(session, org_id)
        if not install:
            return []

        channels: list[dict] = []
        cursor = None

        async with httpx.AsyncClient(timeout=15.0) as client:
            while True:
                params: dict[str, str | int] = {
                    "types": "public_channel,private_channel",
                    "exclude_archived": "true",
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor

                response = await client.get(
                    SLACK_CONVERSATIONS_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {install.bot_token}"},
                )
                data = response.json()
                if not data.get("ok"):
                    logger.warning("Slack conversations.list failed: %s", data.get("error"))
                    break

                for ch in data.get("channels", []):
                    channels.append(
                        {
                            "id": ch["id"],
                            "name": ch["name"],
                            "is_private": ch.get("is_private", False),
                        }
                    )

                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        return channels

    # ---- Channel mapping CRUD ----

    @staticmethod
    async def list_channel_mappings(session: AsyncSession, org_id: int) -> list[SlackChannelMapping]:
        result = await session.execute(select(SlackChannelMapping).where(SlackChannelMapping.organization_id == org_id))
        return list(result.scalars().all())

    @staticmethod
    async def create_channel_mapping(session: AsyncSession, org_id: int, data: dict) -> SlackChannelMapping:
        mapping = SlackChannelMapping(
            organization_id=org_id,
            channel_id=data["channel_id"],
            channel_name=data["channel_name"],
            event_types_json=data.get("event_types", []),
            enabled=data.get("enabled", True),
        )
        session.add(mapping)
        await session.flush()
        return mapping

    @staticmethod
    async def update_channel_mapping(
        session: AsyncSession, mapping_id: int, org_id: int, data: dict
    ) -> SlackChannelMapping | None:
        result = await session.execute(
            select(SlackChannelMapping).where(
                SlackChannelMapping.id == mapping_id,
                SlackChannelMapping.organization_id == org_id,
            )
        )
        mapping = result.scalar_one_or_none()
        if not mapping:
            return None

        if "event_types" in data:
            mapping.event_types_json = data["event_types"]
        if "enabled" in data:
            mapping.enabled = data["enabled"]

        await session.flush()
        return mapping

    @staticmethod
    async def delete_channel_mapping(session: AsyncSession, mapping_id: int, org_id: int) -> bool:
        result = await session.execute(
            select(SlackChannelMapping).where(
                SlackChannelMapping.id == mapping_id,
                SlackChannelMapping.organization_id == org_id,
            )
        )
        mapping = result.scalar_one_or_none()
        if mapping:
            await session.delete(mapping)
            await session.flush()
            return True
        return False

    @staticmethod
    async def get_status(session: AsyncSession, org_id: int) -> dict:
        """Get the current Slack connection status for an org."""
        client_id, client_secret = await SlackOAuthService.get_org_credentials(session, org_id)
        configured = bool(client_id and client_secret)
        install = await SlackOAuthService.get_installation(session, org_id)
        if not install:
            return {
                "configured": configured,
                "connected": False,
                "team_name": None,
                "team_id": None,
                "installed_by": None,
                "installed_at": None,
                "enabled": False,
            }

        # Look up installer email
        result = await session.execute(select(User.email).where(User.id == install.installed_by))
        installer_email = result.scalar_one_or_none()

        return {
            "configured": configured,
            "connected": True,
            "team_name": install.team_name,
            "team_id": install.team_id,
            "installed_by": installer_email,
            "installed_at": install.created_at,
            "enabled": install.enabled,
        }
