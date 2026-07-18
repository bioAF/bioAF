"""Beta feature flags (lit_validation Phase 4).

Platform-wide, admin-gated toggles for features not yet ready for every user. State lives in the
`platform_config` key-value table under the ``beta_feature_<key>`` prefix (default off), so no migration
is needed and the flag is instance-wide.

Two orthogonal concepts:
- **availability**: whether the Beta Features surface is exposed at all. True only when the instance is
  bioAF-operated, i.e. an active, human admin account uses a ``@bioaf.co`` email. This is what hides the
  whole Settings > Beta Features menu from customer instances.
- **enablement**: whether a specific beta feature is toggled on. Independent per key; default off.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.platform.platform_config_service import PlatformConfigService

# The registry of beta features. Adding a key here is all it takes to surface a new toggle; the label
# and description drive the Settings > Beta Features page.
BETA_FEATURES: dict[str, dict[str, str]] = {
    "lit_validation": {
        "label": "Literature Validation",
        "description": "AI-assisted reproduction triage for scientific papers (Validation Studies).",
    },
}

_FLAG_PREFIX = "beta_feature_"
_BIOAF_EMAIL_SUFFIX = "@bioaf.co"


def _flag_key(key: str) -> str:
    return f"{_FLAG_PREFIX}{key}"


async def is_available(session: AsyncSession) -> bool:
    """True when this instance is bioAF-operated: an active, non-service-account admin uses a
    ``@bioaf.co`` email. Gates whether the Beta Features surface is exposed at all."""
    stmt = (
        select(User.id)
        .join(Role, User.role_id == Role.id)
        .where(
            Role.name == "admin",
            User.status == "active",
            User.is_service_account.is_(False),
            func.lower(User.email).like(f"%{_BIOAF_EMAIL_SUFFIX}"),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def is_enabled(session: AsyncSession, key: str) -> bool:
    """True only when the flag for ``key`` is explicitly set on. Missing/anything else -> off."""
    value = await PlatformConfigService.get(session, _flag_key(key))
    return (value or "").strip().lower() == "true"


async def set_flag(session: AsyncSession, key: str, enabled: bool) -> None:
    """Persist a beta flag. Caller commits."""
    if key not in BETA_FEATURES:
        raise ValueError(f"unknown beta feature: {key}")
    await PlatformConfigService.set(session, _flag_key(key), "true" if enabled else "false")


async def get_flags(session: AsyncSession) -> dict[str, bool]:
    """The enablement of every known beta feature (default off)."""
    values = await PlatformConfigService.get_many(session, [_flag_key(k) for k in BETA_FEATURES])
    return {k: (values.get(_flag_key(k)) or "").strip().lower() == "true" for k in BETA_FEATURES}


async def get_state(session: AsyncSession) -> dict:
    """The full client-facing state: availability + per-feature enablement."""
    return {"available": await is_available(session), "flags": await get_flags(session)}
