import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.experiment import Experiment
from app.models.sample import Sample
from app.models.user import User
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
        name="Sarah CompBio",
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


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
        name="Jake Bench",
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
async def two_experiments_with_samples(session, admin_user):
    """Create two experiments with samples for cross-experiment testing."""
    exp1 = Experiment(
        organization_id=admin_user.organization_id,
        name="Tumor Samples Exp",
        status="registered",
    )
    exp2 = Experiment(
        organization_id=admin_user.organization_id,
        name="Healthy Controls Exp",
        status="registered",
    )
    session.add_all([exp1, exp2])
    await session.flush()

    samples = []
    for i in range(4):
        s = Sample(
            experiment_id=exp1.id,
            sample_id_external=f"TUMOR-{i + 1}",
            organism="Homo sapiens",
            tissue_type="brain",
            status="registered",
        )
        samples.append(s)
    for i in range(4):
        s = Sample(
            experiment_id=exp2.id,
            sample_id_external=f"HEALTHY-{i + 1}",
            organism="Homo sapiens",
            tissue_type="brain",
            status="registered",
        )
        samples.append(s)
    session.add_all(samples)
    await session.flush()
    await session.commit()

    return exp1, exp2, samples


@pytest.mark.asyncio
async def test_create_project(client, admin_token, session):
    response = await client.post(
        "/api/projects",
        json={"name": "Cancer Genomics", "description": "Cancer research", "hypothesis": "Test hypothesis"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Cancer Genomics"
    assert data["hypothesis"] == "Test hypothesis"
    assert data["status"] == "active"
    assert data["sample_count"] == 0

    # Verify audit
    result = await session.execute(text("SELECT * FROM audit_log WHERE entity_type = 'project' AND action = 'created'"))
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_create_project_with_initial_samples(client, admin_token, two_experiments_with_samples, session):
    _, _, samples = two_experiments_with_samples
    sample_ids = [samples[0].id, samples[4].id]  # one from each experiment

    response = await client.post(
        "/api/projects",
        json={"name": "Cross-Exp Project", "sample_ids": sample_ids},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sample_count"] == 2


@pytest.mark.asyncio
async def test_create_project_invalid_sample_returns_404(client, admin_token):
    response = await client.post(
        "/api/projects",
        json={"name": "Bad Project", "sample_ids": [99999]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_projects(client, admin_token):
    await client.post(
        "/api/projects",
        json={"name": "Project A"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/api/projects",
        json={"name": "Project B"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_projects_filter_by_status(client, admin_token):
    await client.post(
        "/api/projects",
        json={"name": "Active Project"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/projects?status=active",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    for p in data["projects"]:
        assert p["status"] == "active"


@pytest.mark.asyncio
async def test_list_projects_search(client, admin_token):
    await client.post(
        "/api/projects",
        json={"name": "UniqueSearchName123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/projects?search=UniqueSearch",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert any("UniqueSearchName123" in p["name"] for p in data["projects"])


@pytest.mark.asyncio
async def test_update_project(client, admin_token, session):
    resp = await client.post(
        "/api/projects",
        json={"name": "Old Name"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    response = await client.patch(
        f"/api/projects/{project_id}",
        json={"name": "New Name", "status": "archived"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["status"] == "archived"

    # Verify audit
    result = await session.execute(text("SELECT * FROM audit_log WHERE entity_type = 'project' AND action = 'updated'"))
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_get_project_detail(client, admin_token, two_experiments_with_samples):
    exp1, exp2, samples = two_experiments_with_samples
    sample_ids = [samples[0].id, samples[1].id, samples[4].id]

    # Create project with samples from both experiments
    resp = await client.post(
        "/api/projects",
        json={"name": "Detail Project", "sample_ids": sample_ids},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    response = await client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sample_count"] == 3
    assert data["experiment_count"] == 2
    assert len(data["samples"]) == 2  # two experiment groups

    # Verify grouping
    exp_ids = {g["experiment_id"] for g in data["samples"]}
    assert exp1.id in exp_ids
    assert exp2.id in exp_ids


@pytest.mark.asyncio
async def test_add_samples(client, admin_token, two_experiments_with_samples, session):
    _, _, samples = two_experiments_with_samples

    # Create empty project
    resp = await client.post(
        "/api/projects",
        json={"name": "Empty Project"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    # Add samples
    response = await client.post(
        f"/api/projects/{project_id}/samples",
        json={"sample_ids": [samples[0].id, samples[1].id], "notes": "tumor samples"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["added"] == 2

    # Verify audit per sample
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE entity_type = 'project_sample' AND action = 'sample_added'")
    )
    assert result.scalar() >= 2


@pytest.mark.asyncio
async def test_add_samples_duplicate_returns_409(client, admin_token, two_experiments_with_samples):
    _, _, samples = two_experiments_with_samples
    sample_id = samples[0].id

    resp = await client.post(
        "/api/projects",
        json={"name": "Dupe Test", "sample_ids": [sample_id]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    # Try to add the same sample again
    response = await client.post(
        f"/api/projects/{project_id}/samples",
        json={"sample_ids": [sample_id]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_add_samples_nonexistent_returns_404(client, admin_token):
    resp = await client.post(
        "/api/projects",
        json={"name": "404 Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    response = await client.post(
        f"/api/projects/{project_id}/samples",
        json={"sample_ids": [99999]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_sample(client, admin_token, two_experiments_with_samples, session):
    _, _, samples = two_experiments_with_samples
    sample_id = samples[0].id

    resp = await client.post(
        "/api/projects",
        json={"name": "Remove Test", "sample_ids": [sample_id]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    response = await client.delete(
        f"/api/projects/{project_id}/samples/{sample_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    # Verify sample was removed
    detail = await client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail.json()["sample_count"] == 0


@pytest.mark.asyncio
async def test_remove_sample_not_in_project_returns_404(client, admin_token):
    resp = await client.post(
        "/api/projects",
        json={"name": "Remove 404 Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    response = await client.delete(
        f"/api/projects/{project_id}/samples/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_comp_bio_can_create_project(client, comp_bio_token):
    response = await client.post(
        "/api/projects",
        json={"name": "CompBio Project"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bench_cannot_create_project(client, bench_token):
    response = await client.post(
        "/api/projects",
        json={"name": "Should Fail"},
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bench_cannot_add_samples(client, admin_token, bench_token):
    resp = await client.post(
        "/api/projects",
        json={"name": "Auth Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    response = await client.post(
        f"/api/projects/{project_id}/samples",
        json={"sample_ids": [1]},
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read_projects(client, admin_token, viewer_token):
    await client.post(
        "/api/projects",
        json={"name": "Readable Project"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_create_project(client, viewer_token):
    response = await client.post(
        "/api/projects",
        json={"name": "Should Fail"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_projects_counts_experiments_via_project_id(client, admin_token, session, admin_user):
    """Experiment/sample counts should reflect experiments linked via project_id,
    not just samples manually added through the project_samples table."""
    # Create a project
    resp = await client.post(
        "/api/projects",
        json={"name": "Counts Test Project"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    project_id = resp.json()["id"]

    # Create experiments linked to the project via project_id
    exp1 = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project_id,
        name="Linked Exp 1",
        status="registered",
    )
    exp2 = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project_id,
        name="Linked Exp 2",
        status="registered",
    )
    session.add_all([exp1, exp2])
    await session.flush()

    # Add samples to one of the experiments
    s1 = Sample(experiment_id=exp1.id, sample_id_external="CNT-1", organism="Homo sapiens", status="registered")
    s2 = Sample(experiment_id=exp1.id, sample_id_external="CNT-2", organism="Homo sapiens", status="registered")
    s3 = Sample(experiment_id=exp2.id, sample_id_external="CNT-3", organism="Homo sapiens", status="registered")
    session.add_all([s1, s2, s3])
    await session.commit()

    # List projects and verify counts
    response = await client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    projects = response.json()["projects"]
    project = next(p for p in projects if p["id"] == project_id)

    assert project["experiment_count"] == 2, f"Expected 2 experiments, got {project['experiment_count']}"
    assert project["sample_count"] == 3, f"Expected 3 samples, got {project['sample_count']}"


@pytest.mark.asyncio
async def test_project_detail_counts_experiments_via_project_id(client, admin_token, session, admin_user):
    """Project detail should include experiments linked via project_id."""
    resp = await client.post(
        "/api/projects",
        json={"name": "Detail Counts Project"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["id"]

    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project_id,
        name="Detail Linked Exp",
        status="registered",
    )
    session.add(exp)
    await session.flush()

    s1 = Sample(experiment_id=exp.id, sample_id_external="DET-1", organism="Homo sapiens", status="registered")
    session.add(s1)
    await session.commit()

    response = await client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["experiment_count"] >= 1, f"Expected >= 1 experiment, got {data['experiment_count']}"
    assert data["sample_count"] >= 1, f"Expected >= 1 sample, got {data['sample_count']}"
