"""Admin endpoints for the Users and Accounts page."""

import pytest


@pytest.mark.asyncio
async def test_create_service_account_via_api(client, admin_user, admin_token):
    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    r = await client.post(
        "/api/admin/service-accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "Benchling Sync", "role_id": role_map["admin"]},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["display_name"] == "Benchling Sync"
    assert body["role_id"] == role_map["admin"]


@pytest.mark.asyncio
async def test_mint_api_key_returns_secret_once(client, admin_user, admin_token):
    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    sa = await client.post(
        "/api/admin/service-accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "SA", "role_id": role_map["admin"]},
    )
    sa_id = sa.json()["id"]

    r = await client.post(
        f"/api/admin/service-accounts/{sa_id}/api-keys",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "primary", "scopes": ["projects:view"]},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["secret"].startswith("biokey_")
    assert body["api_key"]["key_prefix"]
    assert body["api_key"]["scopes"] == ["projects:view"]


@pytest.mark.asyncio
async def test_mint_api_key_rejects_unknown_scope(client, admin_user, admin_token):
    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    sa = await client.post(
        "/api/admin/service-accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "SA", "role_id": role_map["admin"]},
    )
    sa_id = sa.json()["id"]
    r = await client.post(
        f"/api/admin/service-accounts/{sa_id}/api-keys",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "x", "scopes": ["finance:wire"]},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_revoke_api_key(client, admin_user, admin_token):
    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    sa = await client.post(
        "/api/admin/service-accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "SA", "role_id": role_map["admin"]},
    )
    sa_id = sa.json()["id"]
    mint = await client.post(
        f"/api/admin/service-accounts/{sa_id}/api-keys",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "k", "scopes": []},
    )
    key_id = mint.json()["api_key"]["id"]

    r = await client.post(
        f"/api/admin/api-keys/{key_id}/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None


@pytest.mark.asyncio
async def test_create_webhook_returns_secret(client, admin_token):
    r = await client.post(
        "/api/admin/webhooks",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "LIMS",
            "url": "https://lims.example/hooks",
            "events": ["experiment.created"],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["secret"].startswith("whsec_")
    assert body["subscription"]["events"] == ["experiment.created"]


@pytest.mark.asyncio
async def test_list_api_activity(client, admin_user, admin_token, integration_api_key):
    """Audit-log activity rows for API-key callers are filterable via the
    admin endpoint. Rows must carry the service account's display name and
    the key's name so the UI can label them without a follow-up lookup."""
    headers = integration_api_key["headers"]
    # Exercise the API as the SA so audit rows accumulate.
    await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "P", "external_id": "EXT"},
    )
    r = await client.get(
        "/api/admin/audit-log/api-activity",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert rows, "expected at least one audit row"
    api_rows = [row for row in rows if row["api_key_id"] is not None]
    assert api_rows
    row = api_rows[0]
    assert row["service_account_name"] == "Test SA"
    assert row["api_key_name"] == "primary"


@pytest.mark.asyncio
async def test_scope_alphabet_listed(client, admin_token):
    r = await client.get(
        "/api/admin/api-keys/scope-alphabet",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    scopes = r.json()["scopes"]
    assert "projects:view" in scopes
    assert "samples:view" in scopes
    assert "files:view" in scopes


@pytest.mark.asyncio
async def test_webhook_test_endpoint(client, admin_token):
    create = await client.post(
        "/api/admin/webhooks",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "LIMS",
            "url": "https://lims.example/hooks",
            "events": ["experiment.created"],
        },
    )
    sub_id = create.json()["subscription"]["id"]
    r = await client.post(
        f"/api/admin/webhooks/{sub_id}/test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["event_type"] == "webhook.test"
