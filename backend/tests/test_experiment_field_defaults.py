import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def experiment_id(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Field Defaults Test Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_experiment_with_field_defaults(client, admin_token, session):
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Defaults Experiment",
            "field_defaults": [
                {"field_name": "organism", "default_value": "Homo sapiens"},
                {"field_name": "tissue_type", "default_value": "liver", "is_required": True},
                {"field_name": "molecule_type", "default_value": "total RNA"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    exp_id = response.json()["id"]

    # Verify field defaults persisted via detail endpoint
    detail = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail.status_code == 200
    data = detail.json()
    assert "field_defaults" in data
    defaults = {d["field_name"]: d for d in data["field_defaults"]}
    assert defaults["organism"]["default_value"] == "Homo sapiens"
    assert defaults["tissue_type"]["default_value"] == "liver"
    assert defaults["tissue_type"]["is_required"] is True
    assert defaults["molecule_type"]["default_value"] == "total RNA"


@pytest.mark.asyncio
async def test_create_experiment_rejects_invalid_field_name(client, admin_token):
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Bad Field Exp",
            "field_defaults": [
                {"field_name": "nonexistent_field", "default_value": "foo"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_experiment_field_defaults(client, admin_token, session):
    # Create with initial defaults
    resp = await client.post(
        "/api/experiments",
        json={
            "name": "Update Defaults Exp",
            "field_defaults": [
                {"field_name": "organism", "default_value": "Mus musculus"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Update: replace defaults entirely
    response = await client.patch(
        f"/api/experiments/{exp_id}",
        json={
            "field_defaults": [
                {"field_name": "organism", "default_value": "Homo sapiens"},
                {"field_name": "tissue_type", "default_value": "brain"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    # Verify via detail
    detail = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    defaults = {d["field_name"]: d for d in detail.json()["field_defaults"]}
    assert len(defaults) == 2
    assert defaults["organism"]["default_value"] == "Homo sapiens"
    assert defaults["tissue_type"]["default_value"] == "brain"


@pytest.mark.asyncio
async def test_sample_inherits_experiment_defaults(client, admin_token, session):
    # Create experiment with defaults
    resp = await client.post(
        "/api/experiments",
        json={
            "name": "Sample Defaults Exp",
            "field_defaults": [
                {"field_name": "organism", "default_value": "Homo sapiens"},
                {"field_name": "tissue_type", "default_value": "liver"},
                {"field_name": "molecule_type", "default_value": "total RNA"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Create sample without specifying organism, tissue_type, or molecule_type
    response = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["organism"] == "Homo sapiens"
    assert data["tissue_type"] == "liver"
    assert data["molecule_type"] == "total RNA"


@pytest.mark.asyncio
async def test_sample_override_beats_experiment_default(client, admin_token, session):
    # Create experiment with defaults
    resp = await client.post(
        "/api/experiments",
        json={
            "name": "Override Exp",
            "field_defaults": [
                {"field_name": "organism", "default_value": "Homo sapiens"},
                {"field_name": "tissue_type", "default_value": "liver"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Create sample with explicit organism (should override)
    response = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={
            "sample_id_external": "S002",
            "organism": "Mus musculus",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["organism"] == "Mus musculus"
    assert data["tissue_type"] == "liver"


@pytest.mark.asyncio
async def test_bulk_samples_inherit_defaults(client, admin_token, session):
    resp = await client.post(
        "/api/experiments",
        json={
            "name": "Bulk Defaults Exp",
            "field_defaults": [
                {"field_name": "organism", "default_value": "Homo sapiens"},
                {"field_name": "library_layout", "default_value": "paired"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    response = await client.post(
        f"/api/experiments/{exp_id}/samples/bulk",
        json={
            "samples": [
                {"sample_id_external": "B001"},
                {"sample_id_external": "B002", "organism": "Mus musculus"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["created"] == 2

    # Verify samples via API
    samples_resp = await client.get(
        f"/api/experiments/{exp_id}/samples",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    samples = samples_resp.json()
    by_ext_id = {s["sample_id_external"]: s for s in samples}
    assert by_ext_id["B001"]["organism"] == "Homo sapiens"
    assert by_ext_id["B001"]["library_layout"] == "paired"
    assert by_ext_id["B002"]["organism"] == "Mus musculus"
    assert by_ext_id["B002"]["library_layout"] == "paired"


@pytest.mark.asyncio
async def test_field_default_requirement_override(client, admin_token, session):
    # Create template with organism required
    tmpl_resp = await client.post(
        "/api/templates",
        json={
            "name": "Strict Template",
            "required_fields_json": {"sample_fields": ["organism"]},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = tmpl_resp.json()["id"]

    # Create experiment with template and default organism
    resp = await client.post(
        "/api/experiments",
        json={
            "name": "Requirement Override Exp",
            "template_id": template_id,
            "field_defaults": [
                {"field_name": "organism", "default_value": "Homo sapiens"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Create sample without organism -- default should satisfy template requirement
    response = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_external": "S003"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["organism"] == "Homo sapiens"


@pytest.mark.asyncio
async def test_detail_response_includes_empty_field_defaults(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "No Defaults Exp"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    detail = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail.status_code == 200
    assert detail.json()["field_defaults"] == []
