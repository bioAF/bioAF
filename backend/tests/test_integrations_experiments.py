"""ADR-048/ADR-050: experiments endpoints on the public integration API."""

import pytest


async def _make_project(client, headers, external_id="LIMS-A", name="Atlas"):
    resp = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": name, "external_id": external_id},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_create_experiment_starts_at_registered(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers)
    resp = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={
            "name": "RNA-seq batch 1",
            "project_id": project["id"],
            "external_id": "EXP-001",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "registered"


@pytest.mark.asyncio
async def test_create_experiment_rejects_status(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers)
    resp = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={
            "name": "RNA-seq",
            "project_id": project["id"],
            "status": "library_prep",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_experiment_rejects_status(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers)
    create = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={"name": "RNA-seq", "project_id": project["id"]},
    )
    eid = create.json()["id"]
    resp = await client.patch(
        f"/api/v1/integrations/experiments/{eid}",
        headers=headers,
        json={"status": "library_prep"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upsert_experiment_by_external_id(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers)
    r1 = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={
            "name": "First",
            "project_id": project["id"],
            "external_id": "EXP-001",
        },
    )
    assert r1.status_code == 201
    eid = r1.json()["id"]

    r2 = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={
            "name": "Renamed",
            "project_id": project["id"],
            "external_id": "EXP-001",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == eid
    assert r2.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_resolve_project_by_external_id(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers, external_id="LIMS-PROJ")
    r = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={"name": "EXP", "project_external_id": "LIMS-PROJ"},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == project["id"]


@pytest.mark.asyncio
async def test_get_experiment_by_external_id(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers)
    await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={"name": "EXP", "project_id": project["id"], "external_id": "EXP-X"},
    )
    r = await client.get("/api/v1/integrations/experiments/by-external/EXP-X", headers=headers)
    assert r.status_code == 200
    assert r.json()["external_id"] == "EXP-X"


@pytest.mark.asyncio
async def test_status_is_readable(client, integration_api_key):
    headers = integration_api_key["headers"]
    project = await _make_project(client, headers)
    create = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={"name": "EXP", "project_id": project["id"]},
    )
    eid = create.json()["id"]
    r = await client.get(f"/api/v1/integrations/experiments/{eid}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "registered"
