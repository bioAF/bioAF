import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user) -> str:
    return AuthService.create_token(
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
    )


@pytest_asyncio.fixture
async def other_org_user(session):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Other Org", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="other@other.com",
        password_hash=AuthService.hash_password("otherpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def other_org_token(other_org_user) -> str:
    return AuthService.create_token(
        other_org_user.id,
        other_org_user.email,
        other_org_user.role_id,
        other_org_user.organization_id,
        role_name="admin",
    )


@pytest.mark.asyncio
async def test_create_experiment(client, admin_token, session):
    response = await client.post(
        "/api/experiments",
        json={"name": "Test Experiment", "hypothesis": "Test hypothesis"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Experiment"
    assert data["status"] == "registered"

    # Verify audit log
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'experiment' AND action = 'create'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_create_experiment_with_template(client, admin_token, session):
    # Create template first
    resp = await client.post(
        "/api/templates",
        json={
            "name": "scRNA Template",
            "required_fields_json": {"sample_fields": ["organism", "tissue_type"]},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    template_id = resp.json()["id"]

    # Create experiment with template
    response = await client.post(
        "/api/experiments",
        json={"name": "Templated Experiment", "template_id": template_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_experiment_metadata(client, admin_token, session):
    # Create
    resp = await client.post(
        "/api/experiments",
        json={"name": "Original Name"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Update
    response = await client.patch(
        f"/api/experiments/{exp_id}",
        json={"name": "Updated Name", "hypothesis": "New hypothesis"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"

    # Verify audit log captures previous values
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'experiment' AND action = 'update'")
    )
    row = result.fetchone()
    assert row is not None
    assert row.previous_value_json is not None


@pytest.mark.asyncio
async def test_valid_status_transition(client, admin_token, session):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Status Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # registered -> library_prep
    response = await client.patch(
        f"/api/experiments/{exp_id}/status",
        json={"status": "library_prep"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "library_prep"

    # Verify audit
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'experiment' AND action = 'status_change'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_invalid_status_transition(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Invalid Status Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # registered -> complete (invalid)
    response = await client.patch(
        f"/api/experiments/{exp_id}/status",
        json={"status": "complete"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "Cannot transition" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_experiments_with_filters(client, admin_token):
    # Create a few experiments
    await client.post(
        "/api/experiments",
        json={"name": "Alpha Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/api/experiments",
        json={"name": "Beta Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # List all
    response = await client.get(
        "/api/experiments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2

    # Search
    response = await client.get(
        "/api/experiments?search=Alpha",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert all("Alpha" in e["name"] for e in response.json()["experiments"])

    # Filter by status
    response = await client.get(
        "/api/experiments?status=registered",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert all(e["status"] == "registered" for e in response.json()["experiments"])


@pytest.mark.asyncio
async def test_get_experiment_detail(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Detail Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    response = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "samples" in data
    assert "sample_batches" in data
    assert "custom_fields" in data
    assert "audit_trail_count" in data
    assert data["template_id"] is None
    assert data["template_name"] is None


@pytest.mark.asyncio
async def test_get_experiment_detail_includes_template_name(client, admin_token):
    # Create a template
    tmpl_resp = await client.post(
        "/api/templates",
        json={"name": "PBMC scRNA Template", "required_fields_json": {"sample_fields": ["organism"]}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = tmpl_resp.json()["id"]

    # Create experiment using that template
    resp = await client.post(
        "/api/experiments",
        json={"name": "Templated Exp", "template_id": template_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Detail endpoint should return template_id and template_name
    detail = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail.status_code == 200
    data = detail.json()
    assert data["template_id"] == template_id
    assert data["template_name"] == "PBMC scRNA Template"


@pytest.mark.asyncio
async def test_org_scoping(client, admin_token, other_org_token):
    # Create experiment in org A
    resp = await client.post(
        "/api/experiments",
        json={"name": "Org A Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    # Org B user cannot see it
    response = await client.get(
        f"/api/experiments/{exp_id}",
        headers={"Authorization": f"Bearer {other_org_token}"},
    )
    assert response.status_code == 404

    # Org B user list doesn't include it
    response = await client.get(
        "/api/experiments",
        headers={"Authorization": f"Bearer {other_org_token}"},
    )
    assert response.status_code == 200
    exp_ids = [e["id"] for e in response.json()["experiments"]]
    assert exp_id not in exp_ids


@pytest.mark.asyncio
async def test_viewer_cannot_create(client, viewer_token):
    response = await client.post(
        "/api/experiments",
        json={"name": "Should Fail"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bench_can_create(client, bench_token, session):
    response = await client.post(
        "/api/experiments",
        json={"name": "Bench Experiment"},
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 200
