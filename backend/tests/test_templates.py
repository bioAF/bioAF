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


@pytest.mark.asyncio
async def test_template_config_copied_to_experiment(client, admin_token):
    # Template has 2 required sample fields and 2 custom fields (one required)
    resp = await client.post(
        "/api/templates",
        json={
            "name": "Copy Me",
            "required_fields_json": {"sample_fields": ["organism", "tissue_type"]},
            "custom_fields_schema_json": {
                "fields": [
                    {"name": "centrifuge_number", "type": "number", "required": True},
                    {"name": "Notes", "type": "string", "required": False},
                ]
            },
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = resp.json()["id"]

    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "From Template", "template_id": template_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert exp_resp.status_code == 200
    exp_id = exp_resp.json()["id"]

    detail = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = detail.json()

    # Required sample fields → field_defaults with is_required=True
    by_field = {fd["field_name"]: fd for fd in body["field_defaults"]}
    assert set(by_field.keys()) == {"organism", "tissue_type"}
    assert all(fd["is_required"] is True for fd in by_field.values())

    # Custom field schema → custom_fields with type and is_required preserved
    by_name = {cf["field_name"]: cf for cf in body["custom_fields"]}
    assert set(by_name.keys()) == {"centrifuge_number", "Notes"}
    assert by_name["centrifuge_number"]["field_type"] == "number"
    assert by_name["centrifuge_number"]["is_required"] is True
    assert by_name["Notes"]["is_required"] is False


@pytest.mark.asyncio
async def test_user_provided_values_override_template(client, admin_token):
    resp = await client.post(
        "/api/templates",
        json={
            "name": "Defaults Template",
            "required_fields_json": {"sample_fields": ["organism"]},
            "custom_fields_schema_json": {
                "fields": [{"name": "Notes", "type": "string", "required": True}]
            },
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = resp.json()["id"]

    # User submits a default value for organism and a value for Notes
    exp_resp = await client.post(
        "/api/experiments",
        json={
            "name": "User Wins",
            "template_id": template_id,
            "field_defaults": [
                {"field_name": "organism", "default_value": "Mus musculus", "is_required": None},
            ],
            "custom_fields": [
                {"field_name": "Notes", "field_value": "user value", "field_type": "string"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]

    detail = (await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()

    by_field = {fd["field_name"]: fd for fd in detail["field_defaults"]}
    # Template wins on is_required even if user passed None; user value preserved
    assert by_field["organism"]["default_value"] == "Mus musculus"
    assert by_field["organism"]["is_required"] is True

    by_name = {cf["field_name"]: cf for cf in detail["custom_fields"]}
    assert by_name["Notes"]["field_value"] == "user value"
    assert by_name["Notes"]["is_required"] is True
