import pytest
import pytest_asyncio
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest.mark.asyncio
async def test_list_templates_initializes_builtins(client, admin_token):
    """First call initializes built-in templates."""
    response = await client.get(
        "/api/template-notebooks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    names = [n["name"] for n in data["notebooks"]]
    assert "QC & Filtering" in names
    assert "Clustering & Marker Genes" in names
    assert "Trajectory Inference" in names


@pytest.mark.asyncio
async def test_list_templates_idempotent(client, admin_token):
    """Calling list twice doesn't duplicate templates."""
    await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    response = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    names = [n["name"] for n in data["notebooks"]]
    assert names.count("QC & Filtering") == 1


@pytest.mark.asyncio
async def test_get_template_detail(client, admin_token):
    """Get a specific template."""
    # Initialize
    list_resp = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    templates = list_resp.json()["notebooks"]
    template_id = templates[0]["id"]

    response = await client.get(
        f"/api/template-notebooks/{template_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == template_id
    assert data["is_builtin"] is True
    assert "parameters" in data


@pytest.mark.asyncio
async def test_get_template_not_found(client, admin_token):
    response = await client.get(
        "/api/template-notebooks/9999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_viewer_can_list_templates(client, viewer_token):
    """Viewers can list templates."""
    response = await client.get(
        "/api/template-notebooks",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_clone_template(client, viewer_token):
    """Viewers cannot clone templates."""
    response = await client.post(
        "/api/template-notebooks/1/clone",
        json={"new_name": "test"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_templates_have_correct_categories(client, admin_token):
    """Templates have expected categories."""
    response = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    categories = {n["category"] for n in data["notebooks"]}
    assert "qc" in categories
    assert "normalization" in categories
    assert "clustering" in categories
    assert "differential_expression" in categories
    assert "trajectory" in categories


@pytest.mark.asyncio
async def test_templates_ordered_by_sort_order(client, admin_token):
    """Templates are returned in correct order."""
    response = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    notebooks = data["notebooks"]
    # QC should come first, trajectory last
    assert notebooks[0]["category"] == "qc"
    assert notebooks[-1]["category"] == "trajectory"
