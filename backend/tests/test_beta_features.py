"""Beta feature flags (lit_validation Phase 4, spec-07).

Enablement is a per-key platform_config flag, default off, toggled by an admin. The lit_validation
API 404s when its flag is off.

The instance-level ``@bioaf.co`` availability gate was REMOVED: it meant a beta feature could only
ever be enabled, or even seen, on an instance staffed by bioAF, which made "beta" indistinguishable
from "internal-only" for every customer. Beta now means what it says: off by default, an admin turns
it on, and any user with the feature's view permission sees it.
"""

import pytest

from app.services import beta_features_service

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestFlags:
    async def test_flag_defaults_off_and_round_trips(self, session, admin_user):
        assert await beta_features_service.is_enabled(session, "lit_validation") is False
        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()
        assert await beta_features_service.is_enabled(session, "lit_validation") is True
        flags = await beta_features_service.get_flags(session)
        assert flags["lit_validation"] is True

    async def test_set_flag_unknown_key_raises(self, session, admin_user):
        with pytest.raises(ValueError):
            await beta_features_service.set_flag(session, "not_a_feature", True)

    async def test_get_state_shape(self, session, admin_user):
        state = await beta_features_service.get_state(session)
        assert set(state) == {"flags"}
        assert "lit_validation" in state["flags"]


class TestApi:
    async def test_get_state_for_authed_user(self, client, admin_token):
        r = await client.get("/api/beta-features", headers=_auth(admin_token))
        assert r.status_code == 200, r.text
        assert r.json()["flags"]["lit_validation"] is False

    async def test_any_admin_can_toggle_whatever_their_email_domain(self, client, admin_token):
        """The only admin here is admin@test.com. Enabling a beta feature used to 403 on exactly this
        instance, which is every customer instance."""
        r = await client.put("/api/beta-features/lit_validation", json={"enabled": True}, headers=_auth(admin_token))
        assert r.status_code == 200, r.text
        assert r.json()["flags"]["lit_validation"] is True

    async def test_put_403_for_viewer(self, client, viewer_token):
        """Still admin-gated: beta is off by default and only an admin turns it on."""
        r = await client.put("/api/beta-features/lit_validation", json={"enabled": True}, headers=_auth(viewer_token))
        assert r.status_code == 403, r.text

    async def test_put_404_unknown_key(self, client, admin_token):
        r = await client.put("/api/beta-features/nonsense", json={"enabled": True}, headers=_auth(admin_token))
        assert r.status_code == 404, r.text


class TestLitValidationGate:
    async def test_a_non_bioaf_user_sees_the_feature_once_it_is_enabled(
        self, client, session, admin_user, viewer_token
    ):
        """The point of the change: with the flag on, a plain viewer on a customer instance reaches
        the feature. Their email domain is not consulted anywhere."""
        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()
        r = await client.get("/api/validation-studies", headers=_auth(viewer_token))
        assert r.status_code == 200, r.text

    async def test_validation_endpoint_404_when_flag_off(self, client, admin_token):
        r = await client.get("/api/validation-studies", headers=_auth(admin_token))
        assert r.status_code == 404, r.text

    async def test_validation_endpoint_reachable_when_flag_on(self, client, session, admin_user, admin_token):
        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()
        r = await client.get("/api/validation-studies", headers=_auth(admin_token))
        assert r.status_code == 200, r.text
