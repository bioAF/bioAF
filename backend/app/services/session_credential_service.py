import base64
import hashlib
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_work_node_adapter
from app.exceptions import ConflictError, ValidationError
from app.models.session_credential import SessionCredential
from app.services.auth_service import AuthService
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.session_credentials")

# Plan 03B (D03-10) copy: a key a Work Node may still need is never replaced.
KEY_REPLACEMENT_REFUSAL = (
    "Finish and stop your current work nodes before replacing this key. You can download your current key again."
)
_ACTIVE_NODE_STATUSES = ("pending", "starting", "running", "stopping", "stop_failed")


class SessionCredentialService:
    @staticmethod
    def generate_username(email: str) -> str:
        """Generate a Unix username from an email address.

        Strips the domain, removes dots and non-alphanumeric chars, lowercases.
        """
        local_part = email.split("@")[0].lower()
        username = re.sub(r"[^a-z0-9]", "", local_part)
        return username or "user"

    @staticmethod
    def validate_username(username: str) -> str | None:
        """Validate a custom username. Returns error message or None."""
        if len(username) < 3:
            return "Username must be at least 3 characters"
        if len(username) > 32:
            return "Username must be at most 32 characters"
        if not re.match(r"^[a-z][a-z0-9_]*$", username):
            return "Username must start with a letter and contain only lowercase letters, numbers, and underscores"
        return None

    @staticmethod
    async def get_by_user_id(session: AsyncSession, user_id: int) -> SessionCredential | None:
        result = await session.execute(select(SessionCredential).where(SessionCredential.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def _resolve_unique_username(
        session: AsyncSession,
        base_username: str,
        org_id: int,
        exclude_user_id: int | None = None,
    ) -> str:
        """Find a unique username within the org, appending numeric suffix if needed."""
        candidate = base_username
        suffix = 2
        while True:
            query = select(SessionCredential).where(
                SessionCredential.organization_id == org_id,
                SessionCredential.username == candidate,
            )
            if exclude_user_id is not None:
                query = query.where(SessionCredential.user_id != exclude_user_id)
            result = await session.execute(query)
            if result.scalar_one_or_none() is None:
                return candidate
            candidate = f"{base_username}{suffix}"
            suffix += 1

    @staticmethod
    async def create_or_update(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        email: str,
        password: str,
        username: str | None = None,
        actor_user_id: int | None = None,
    ) -> SessionCredential:
        """Create or update session credentials for a user."""
        existing = await SessionCredentialService.get_by_user_id(session, user_id)

        if username is None:
            base = SessionCredentialService.generate_username(email)
            if existing and existing.username:
                # Keep existing username if not explicitly changing
                resolved_username = existing.username
            else:
                resolved_username = await SessionCredentialService._resolve_unique_username(
                    session,
                    base,
                    org_id,
                    exclude_user_id=user_id,
                )
        else:
            resolved_username = await SessionCredentialService._resolve_unique_username(
                session,
                username,
                org_id,
                exclude_user_id=user_id,
            )

        password_hash = AuthService.hash_password(password)

        if existing:
            existing.username = resolved_username
            existing.password_hash = password_hash
            await session.flush()
            action = "update"
            cred = existing
        else:
            cred = SessionCredential(
                user_id=user_id,
                organization_id=org_id,
                username=resolved_username,
                password_hash=password_hash,
            )
            session.add(cred)
            await session.flush()
            action = "create"

        await log_action(
            session,
            user_id=actor_user_id or user_id,
            entity_type="session_credential",
            entity_id=cred.id,
            action=action,
            details={"username": resolved_username},
        )
        return cred

    # -- The user's bioAF SSH key: Git key and Work Node login key (plan 03B, D03-6 / D03-10) --------

    @staticmethod
    def generate_ssh_keypair(email: str) -> tuple[str, str]:
        """A new RSA 4096 keypair: (OpenSSH private key, public key with the email as comment)."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_openssh = (
            private_key.public_key()
            .public_bytes(encoding=serialization.Encoding.OpenSSH, format=serialization.PublicFormat.OpenSSH)
            .decode("utf-8")
        )
        return private_pem, f"{public_openssh} {email}"

    @staticmethod
    def key_fingerprint(public_key: str | None) -> str | None:
        """OpenSSH SHA256 fingerprint of a public key line, or None."""
        if not public_key:
            return None
        try:
            blob = base64.b64decode(public_key.split()[1])
        except (IndexError, ValueError):
            return None
        return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")

    @staticmethod
    def _key_is_complete(cred: SessionCredential) -> bool:
        if not cred.ssh_private_key or not cred.ssh_public_key:
            return False
        try:
            import asyncssh

            derived = asyncssh.import_private_key(cred.ssh_private_key).export_public_key().decode().split()[:2]
        except Exception:
            return False
        return derived == cred.ssh_public_key.split()[:2]

    @staticmethod
    async def lock_for_user(session: AsyncSession, user_id: int) -> SessionCredential | None:
        """The user's credential row, locked until commit: launch and key replacement serialize here."""
        result = await session.execute(
            select(SessionCredential).where(SessionCredential.user_id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def ensure_login_key(session: AsyncSession, cred: SessionCredential, email: str) -> bool:
        """Generate the user's key if they have none; True when one was generated. Never replaces a key.

        Partial or unreadable material is an explicit error: it is not silently replaced (a running
        node may hold the old public half) and never becomes password login outside a range.
        """
        if not cred.ssh_private_key and not cred.ssh_public_key:
            cred.ssh_private_key, cred.ssh_public_key = SessionCredentialService.generate_ssh_keypair(email)
            await session.flush()
            await log_action(
                session,
                user_id=cred.user_id,
                entity_type="session_credential",
                entity_id=cred.id,
                action="generate_ssh_key",
                details={
                    "reason": "work_node_login",
                    "fingerprint": SessionCredentialService.key_fingerprint(cred.ssh_public_key),
                },
            )
            return True
        if not SessionCredentialService._key_is_complete(cred):
            raise ValidationError(
                "Your bioAF SSH key is incomplete, so this work node cannot be launched. "
                "Contact your administrator; the key has not been changed."
            )
        return False

    @staticmethod
    async def assert_key_replaceable(session: AsyncSession, user_id: int, org_id: int) -> None:
        """Refuse replacement while any of the user's Work Nodes may still need the current key.

        Pending, live, stopping or kept nodes block. For other records with an instance, or with a
        launch attempt whose instance was never recorded, the provider inventory decides (a database
        "stopped" alone is not proof); an unavailable inventory blocks, and so does an unrecorded
        attempt that has not settled (``LAUNCH_SETTLE_WINDOW``).
        """
        from app.models.notebook_session import ComputeSession

        rows = (
            await session.execute(
                select(
                    ComputeSession.id,
                    ComputeSession.status,
                    ComputeSession.gce_instance_name,
                    ComputeSession.launch_attempt_id,
                    ComputeSession.created_at,
                ).where(
                    ComputeSession.user_id == user_id,
                    ComputeSession.organization_id == org_id,
                    ComputeSession.session_type == "ssh",
                )
            )
        ).all()
        if any(r.status in _ACTIVE_NODE_STATUSES for r in rows):
            raise ConflictError(KEY_REPLACEMENT_REFUSAL)
        from app.services.work_node_readiness import launch_settled

        # A launch attempt with no recorded instance may still have created one.
        unresolved = [r for r in rows if not r.gce_instance_name and r.launch_attempt_id]
        if any(not launch_settled(r.created_at) for r in unresolved):
            raise ConflictError(KEY_REPLACEMENT_REFUSAL)
        with_instances = {str(r.id) for r in rows if r.gce_instance_name} | {str(r.id) for r in unresolved}
        if not with_instances:
            return
        try:
            instances = await get_work_node_adapter().inventory_instances()
        except Exception:
            logger.warning("Work node inventory unavailable; keeping the current SSH key for user %s", user_id)
            raise ConflictError(KEY_REPLACEMENT_REFUSAL) from None
        if any(i.session_id in with_instances for i in instances):
            raise ConflictError(KEY_REPLACEMENT_REFUSAL)
