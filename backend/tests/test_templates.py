import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_create_template(client, admin_token, session):
    response = await client.post(
        "/api/templates",
        json={
            "name": "scRNA-seq Template",
            "description": "Standard template for scRNA-seq experiments",
            "required_fields_json": {
                "sample_fields": ["organism", "tissue_type", "chemistry_version"],
                "experiment_fields": ["hypothesis"],
            },
            "custom_fields_schema_json": {
                "fields": [
                    {"name": "drug_concentration", "type": "number", "required": True},
                    {"name": "timepoint", "type": "string", "required": True},
                ]
            },
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "scRNA-seq Template"
    assert "organism" in data["required_fields_json"]["sample_fields"]

    # Verify audit
    result = await session.execute(text("SELECT * FROM audit_log WHERE entity_type = 'template' AND action = 'create'"))
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_list_templates(client, admin_token):
    await client.post(
        "/api/templates",
        json={"name": "Template A"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/templates",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.asyncio
async def test_update_template(client, admin_token, session):
    resp = await client.post(
        "/api/templates",
        json={"name": "Update Me"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = resp.json()["id"]

    response = await client.patch(
        f"/api/templates/{template_id}",
        json={"name": "Updated Template", "description": "Now with description"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Template"


@pytest.mark.asyncio
async def test_template_enforces_sample_fields(client, admin_token):
    # Create template
    resp = await client.post(
        "/api/templates",
        json={
            "name": "Strict Template",
            "required_fields_json": {"sample_fields": ["organism", "donor_source"]},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = resp.json()["id"]

    # Create experiment with template
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Strict Experiment", "template_id": template_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]

    # Sample without required fields should fail
    response = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "MISS001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400

    # Sample with required fields should succeed
    response = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "OK001", "organism": "Human", "donor_source": "Biobank"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_can_list_templates(client, viewer_token, admin_token):
    await client.post(
        "/api/templates",
        json={"name": "Viewer Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/templates",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_create_template(client, viewer_token):
    response = await client.post(
        "/api/templates",
        json={"name": "Should Fail"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
