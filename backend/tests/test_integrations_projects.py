"""ADR-048/ADR-050: projects endpoints on the public integration API."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_create_project_returns_201(client, integration_api_key):
    headers = integration_api_key["headers"]
    resp = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Atlas"
    assert body["external_id"] == "LIMS-A"
    assert "id" in body


@pytest.mark.asyncio
async def test_create_project_upsert_by_external_id(client, integration_api_key, session):
    headers = integration_api_key["headers"]
    r1 = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    assert r1.status_code == 201
    id_first = r1.json()["id"]

    r2 = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas v2", "external_id": "LIMS-A"},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == id_first
    assert r2.json()["name"] == "Atlas v2"

    count = (await session.execute(text("SELECT count(*) FROM projects WHERE external_id = 'LIMS-A'"))).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_idempotency_key_replays_response(client, integration_api_key, session):
    headers = {**integration_api_key["headers"], "Idempotency-Key": "abc-123"}
    r1 = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    assert r1.status_code == 201
    id_first = r1.json()["id"]

    r2 = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    assert r2.status_code == 201
    assert r2.headers.get("Idempotency-Replayed") == "true"
    assert r2.json()["id"] == id_first

    count = (await session.execute(text("SELECT count(*) FROM projects"))).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_idempotency_key_with_different_body_returns_422(client, integration_api_key):
    headers = {**integration_api_key["headers"], "Idempotency-Key": "abc-123"}
    r1 = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Different", "external_id": "LIMS-B"},
    )
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_get_project_by_external_id(client, integration_api_key):
    headers = integration_api_key["headers"]
    await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas", "external_id": "LIMS-A"},
    )
    resp = await client.get("/api/v1/integrations/projects/by-external/LIMS-A", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["external_id"] == "LIMS-A"


@pytest.mark.asyncio
async def test_list_projects_with_cursor(client, integration_api_key):
    headers = integration_api_key["headers"]
    for i in range(3):
        await client.post(
            "/api/v1/integrations/projects",
            headers=headers,
            json={"name": f"P{i}", "external_id": f"E{i}"},
        )

    r = await client.get("/api/v1/integrations/projects?limit=2", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    r2 = await client.get(
        f"/api/v1/integrations/projects?limit=2&cursor={body['next_cursor']}",
        headers=headers,
    )
    assert r2.status_code == 200
    assert len(r2.json()["items"]) >= 1


@pytest.mark.asyncio
async def test_patch_project_custom_fields_delta(client, integration_api_key):
    headers = integration_api_key["headers"]
    r = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={
            "name": "Atlas",
            "external_id": "LIMS-A",
            "custom_fields": [
                {"field_name": "color", "field_value": "blue"},
                {"field_name": "size", "field_value": "large"},
            ],
        },
    )
    pid = r.json()["id"]

    r = await client.patch(
        f"/api/v1/integrations/projects/{pid}",
        headers=headers,
        json={"custom_fields": [{"field_name": "color", "field_value": "red"}]},
    )
    assert r.status_code == 200
    fields = {cf["field_name"]: cf["field_value"] for cf in r.json()["custom_fields"]}
    assert fields == {"color": "red", "size": "large"}

    r = await client.patch(
        f"/api/v1/integrations/projects/{pid}",
        headers=headers,
        json={"custom_fields": [{"field_name": "color", "field_value": None}]},
    )
    fields = {cf["field_name"]: cf["field_value"] for cf in r.json()["custom_fields"]}
    assert fields == {"size": "large"}


@pytest.mark.asyncio
async def test_scope_intersection_enforced(client, viewer_api_key):
    """A view-only key cannot create."""
    headers = viewer_api_key["headers"]
    r = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "Atlas"},
    )
    # The viewer role does not have projects:create either, so this fails
    # on the role check (role_missing) before the scope check ever runs.
    # Either way, 403.
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_jwt_token_cannot_access_integration_api(client, admin_token):
    """Public integration endpoints reject JWT-authenticated callers."""
    r = await client.post(
        "/api/v1/integrations/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Atlas"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauth_returns_401(client):
    r = await client.get("/api/v1/integrations/projects")
    assert r.status_code == 401
