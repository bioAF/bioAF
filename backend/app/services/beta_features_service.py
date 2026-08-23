"""Beta feature flags (lit_validation Phase 4).

Platform-wide, admin-gated toggles for features not yet ready for every user. State lives in the
`platform_config` key-value table under the ``beta_feature_<key>`` prefix (default off), so no migration
is needed and the flag is instance-wide.

One concept: **enablement**, whether a specific beta feature is toggled on. Independent per key,
default off, and only an admin (``infrastructure:configure``) can change it.

There was a second concept, **availability**, which asked whether the instance was bioAF-operated by
looking for an active admin with a ``@bioaf.co`` email, and it hid the Beta Features surface entirely
from everyone else. It was removed: it meant a beta feature could only ever be enabled, or even seen,
on an instance bioAF staffs, so "beta" and "internal-only" were the same thing for every customer.
Beta now means what it says. Gating a feature to particular PEOPLE is what roles and permissions are
for; an email domain is not an authorization mechanism.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

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


def _flag_key(key: str) -> str:
    return f"{_FLAG_PREFIX}{key}"


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
    """The full client-facing state: per-feature enablement."""
    return {"flags": await get_flags(session)}
