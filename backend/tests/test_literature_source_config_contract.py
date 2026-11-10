"""Source configuration contract: override validation, reset, and resolution.

``rate_limit_override`` is requests per second at actual dispatch, a positive
integer. Omitted means unchanged, explicit null resets, and 0 remains a legacy
reset alias. Negatives, booleans, strings, and fractions are rejected at the
API and negatives at the service. Resolution snapshots each source's enabled
state, credential, and effective rate for one operation; a persisted value
that is not a positive integer resolves to the source default.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.exceptions import ValidationError
from app.models.literature import LiteratureSourcesConfig
from app.services.bootstrap_literature import seed_literature_sources
from app.services.literature import sources_config_service

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _seed(session, user):
    await seed_literature_sources(session, user.organization_id)
    await session.commit()


async def _patch(client, token, body, source="pubmed"):
    return await client.patch(f"/api/literature/sources/{source}", json=body, headers=_auth(token))


async def test_explicit_null_resets_an_override(client, admin_token, admin_user, session):
    await _seed(session, admin_user)
    assert (await _patch(client, admin_token, {"rate_limit_override": 5})).json()["rate_limit_override"] == 5
    resp = await _patch(client, admin_token, {"rate_limit_override": None})
    assert resp.status_code == 200
    assert resp.json()["rate_limit_override"] is None


async def test_an_omitted_override_is_unchanged(client, admin_token, admin_user, session):
    await _seed(session, admin_user)
    await _patch(client, admin_token, {"rate_limit_override": 5})
    resp = await _patch(client, admin_token, {"enabled": False})
    assert resp.json()["rate_limit_override"] == 5


async def test_zero_remains_a_legacy_reset(client, admin_token, admin_user, session):
    await _seed(session, admin_user)
    await _patch(client, admin_token, {"rate_limit_override": 5})
    assert (await _patch(client, admin_token, {"rate_limit_override": 0})).json()["rate_limit_override"] is None


@pytest.mark.parametrize("value", [-1, True, "5", 2.5])
async def test_invalid_overrides_are_rejected(client, admin_token, admin_user, session, value):
    await _seed(session, admin_user)
    await _patch(client, admin_token, {"rate_limit_override": 3})
    resp = await _patch(client, admin_token, {"rate_limit_override": value})
    assert resp.status_code == 422
    listed = (await client.get("/api/literature/sources", headers=_auth(admin_token))).json()["items"]
    assert {i["source"]: i["rate_limit_override"] for i in listed}["pubmed"] == 3


async def test_the_service_rejects_a_negative_override(session, admin_user):
    await _seed(session, admin_user)
    with pytest.raises(ValidationError):
        await sources_config_service.update(
            session, org_id=admin_user.organization_id, source="pubmed", user_id=admin_user.id, rate_limit_override=-2
        )


async def test_the_service_resets_on_explicit_none_and_keeps_on_omission(session, admin_user):
    await _seed(session, admin_user)
    org, uid = admin_user.organization_id, admin_user.id
    await sources_config_service.update(session, org_id=org, source="pubmed", user_id=uid, rate_limit_override=4)
    row = await sources_config_service.update(session, org_id=org, source="pubmed", user_id=uid, enabled=True)
    assert row.rate_limit_override == 4
    row = await sources_config_service.update(
        session, org_id=org, source="pubmed", user_id=uid, rate_limit_override=None
    )
    assert row.rate_limit_override is None


async def test_resolution_applies_defaults_keys_and_overrides(session, admin_user):
    await _seed(session, admin_user)
    org, uid = admin_user.organization_id, admin_user.id
    await sources_config_service.update(session, org_id=org, source="pubmed", user_id=uid, api_key="k-pubmed")
    await sources_config_service.update(session, org_id=org, source="europepmc", user_id=uid, rate_limit_override=2)
    await sources_config_service.update(
        session, org_id=org, source="semanticscholar", user_id=uid, api_key="k-s2", enabled=False
    )
    resolved = await sources_config_service.resolve(session, org, ["pubmed", "europepmc", "semanticscholar", "biorxiv"])

    assert resolved["pubmed"].api_key == "k-pubmed" and resolved["pubmed"].requests_per_second == 10.0
    assert resolved["europepmc"].requests_per_second == 2.0 and resolved["europepmc"].override == 2
    assert resolved["semanticscholar"].enabled is False
    # Semantic Scholar's documented keyed rate (1 req/s), not the earlier assumed 5.
    assert resolved["semanticscholar"].requests_per_second == 1.0
    assert resolved["biorxiv"].enabled is True and resolved["biorxiv"].requests_per_second == 5.0
    assert "k-pubmed" not in repr(resolved["pubmed"])


async def test_a_missing_row_resolves_enabled_with_defaults(session, admin_user):
    resolved = await sources_config_service.resolve(session, admin_user.organization_id, ["openalex"])
    assert resolved["openalex"].enabled is True
    assert resolved["openalex"].api_key is None
    assert resolved["openalex"].requests_per_second == 10.0


@pytest.mark.parametrize("persisted", [-3, 0])
async def test_an_invalid_persisted_override_resolves_to_the_default(session, admin_user, persisted):
    await _seed(session, admin_user)
    await session.execute(
        update(LiteratureSourcesConfig)
        .where(
            LiteratureSourcesConfig.organization_id == admin_user.organization_id,
            LiteratureSourcesConfig.source == "pubmed",
        )
        .values(rate_limit_override=persisted)
    )
    resolved = await sources_config_service.resolve(session, admin_user.organization_id, ["pubmed"])
    assert resolved["pubmed"].override is None
    assert resolved["pubmed"].requests_per_second == 3.0


async def test_a_key_change_is_seen_by_the_next_resolution(session, admin_user):
    await _seed(session, admin_user)
    org, uid = admin_user.organization_id, admin_user.id
    before = (await sources_config_service.resolve(session, org, ["pubmed"]))["pubmed"]
    await sources_config_service.update(session, org_id=org, source="pubmed", user_id=uid, api_key="k-new")
    after = (await sources_config_service.resolve(session, org, ["pubmed"]))["pubmed"]
    assert before.api_key is None and before.requests_per_second == 3.0
    assert after.api_key == "k-new" and after.requests_per_second == 10.0
    # Removing the key (empty string) returns to the unauthenticated rate.
    await sources_config_service.update(session, org_id=org, source="pubmed", user_id=uid, api_key="")
    removed = (await sources_config_service.resolve(session, org, ["pubmed"]))["pubmed"]
    assert removed.api_key is None and removed.requests_per_second == 3.0


# --- OpenAlex is always on (owner, 2026-11-10) -----------------------------------------------


async def _disable_openalex_row(session, user):
    await session.execute(
        update(LiteratureSourcesConfig)
        .where(
            LiteratureSourcesConfig.organization_id == user.organization_id,
            LiteratureSourcesConfig.source == "openalex",
        )
        .values(enabled=False)
    )
    await session.commit()


async def test_openalex_cannot_be_turned_off(client, admin_token, admin_user, session):
    await _seed(session, admin_user)
    resp = await _patch(client, admin_token, {"enabled": False}, source="openalex")
    assert resp.status_code in (400, 422)
    listed = (await client.get("/api/literature/sources", headers=_auth(admin_token))).json()["items"]
    assert {i["source"]: i["enabled"] for i in listed}["openalex"] is True


async def test_openalex_settings_other_than_enabled_still_apply(client, admin_token, admin_user, session):
    await _seed(session, admin_user)
    resp = await _patch(client, admin_token, {"rate_limit_override": 4, "enabled": True}, source="openalex")
    assert resp.status_code == 200 and resp.json()["rate_limit_override"] == 4


async def test_a_persisted_disabled_openalex_row_still_resolves_enabled(client, admin_token, admin_user, session):
    await _seed(session, admin_user)
    await _disable_openalex_row(session, admin_user)
    resolved = await sources_config_service.resolve(session, admin_user.organization_id, ["openalex"])
    assert resolved["openalex"].enabled is True
    enabled = {row.source for row in await sources_config_service.list_enabled(session, admin_user.organization_id)}
    assert "openalex" in enabled
    listed = (await client.get("/api/literature/sources", headers=_auth(admin_token))).json()["items"]
    assert {i["source"]: i["enabled"] for i in listed}["openalex"] is True
