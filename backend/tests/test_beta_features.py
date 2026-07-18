"""Beta feature flags (lit_validation Phase 4, spec-07).

Availability is a system property (an active admin uses a @bioaf.co email); enablement is a per-key
platform_config flag (default off). The lit_validation API 404s when its flag is off.
"""

import pytest

from app.services import beta_features_service
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _add_user(session, admin_user, email, role_key):
    from app.models.user import User

    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    user = User(
        email=email,
        password_hash=AuthService.hash_password("pw-testing-123"),
        role_id=role_map[role_key],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


class TestAvailability:
    async def test_false_for_non_bioaf_admin(self, session, admin_user):
        # The only admin is admin@test.com -> not a bioAF-operated instance.
        assert await beta_features_service.is_available(session) is False

    async def test_true_when_any_admin_is_bioaf(self, session, admin_user):
        await _add_user(session, admin_user, "someone@bioaf.co", "admin")
        assert await beta_features_service.is_available(session) is True

    async def test_case_insensitive_domain(self, session, admin_user):
        await _add_user(session, admin_user, "Mixed.Case@BioAF.Co", "admin")
        assert await beta_features_service.is_available(session) is True

    async def test_non_admin_bioaf_user_does_not_count(self, session, admin_user):
        # A @bioaf.co VIEWER is not an admin, so availability stays false.
        await _add_user(session, admin_user, "viewer@bioaf.co", "viewer")
        assert await beta_features_service.is_available(session) is False

    async def test_inactive_bioaf_admin_does_not_count(self, session, admin_user):
        from app.models.user import User

        role_map = admin_user._test_role_map  # type: ignore[attr-defined]
        session.add(
            User(
                email="left@bioaf.co",
                password_hash=AuthService.hash_password("pw-testing-123"),
                role_id=role_map["admin"],
                organization_id=admin_user.organization_id,
                status="disabled",
            )
        )
        await session.commit()
        assert await beta_features_service.is_available(session) is False


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
        assert set(state) == {"available", "flags"}
        assert "lit_validation" in state["flags"]


class TestApi:
    async def test_get_state_for_authed_user(self, client, admin_token):
        r = await client.get("/api/beta-features", headers=_auth(admin_token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False  # only admin@test.com
        assert body["flags"]["lit_validation"] is False

    async def test_put_toggles_when_admin_and_available(self, client, session, admin_user, admin_token):
        await _add_user(session, admin_user, "op@bioaf.co", "admin")  # instance becomes bioAF-operated
        r = await client.put(
            "/api/beta-features/lit_validation", json={"enabled": True}, headers=_auth(admin_token)
        )
        assert r.status_code == 200, r.text
        assert r.json()["flags"]["lit_validation"] is True

    async def test_put_403_when_not_available(self, client, admin_token):
        r = await client.put(
            "/api/beta-features/lit_validation", json={"enabled": True}, headers=_auth(admin_token)
        )
        assert r.status_code == 403, r.text

    async def test_put_403_for_viewer(self, client, session, admin_user, viewer_token):
        await _add_user(session, admin_user, "op@bioaf.co", "admin")
        r = await client.put(
            "/api/beta-features/lit_validation", json={"enabled": True}, headers=_auth(viewer_token)
        )
        assert r.status_code == 403, r.text

    async def test_put_404_unknown_key(self, client, session, admin_user, admin_token):
        await _add_user(session, admin_user, "op@bioaf.co", "admin")
        r = await client.put(
            "/api/beta-features/nonsense", json={"enabled": True}, headers=_auth(admin_token)
        )
        assert r.status_code == 404, r.text


class TestLitValidationGate:
    async def test_validation_endpoint_404_when_flag_off(self, client, admin_token):
        r = await client.get("/api/validation-studies", headers=_auth(admin_token))
        assert r.status_code == 404, r.text

    async def test_validation_endpoint_reachable_when_flag_on(self, client, session, admin_user, admin_token):
        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()
        r = await client.get("/api/validation-studies", headers=_auth(admin_token))
        assert r.status_code == 200, r.text
