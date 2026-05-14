"""ADR-048: integration sub-app OpenAPI exposure."""

import pytest


@pytest.mark.asyncio
async def test_integration_openapi_is_reachable_without_auth(client):
    resp = await client.get("/api/v1/integrations/openapi.json")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["info"]["title"] == "bioAF Integration API"
    assert doc["info"]["version"] == "1.0"
    paths = doc["paths"]
    for expected in (
        "/projects",
        "/projects/{project_id}",
        "/projects/by-external/{external_id}",
        "/experiments",
        "/experiments/{experiment_id}",
        "/samples",
        "/samples/{sample_id}",
        "/files",
        "/files/{file_id}",
    ):
        assert expected in paths, f"missing operation: {expected}"


@pytest.mark.asyncio
async def test_integration_docs_is_reachable_without_auth(client):
    resp = await client.get("/api/v1/integrations/docs")
    assert resp.status_code == 200
    assert "Swagger UI" in resp.text or "swagger" in resp.text.lower()
