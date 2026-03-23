import pytest
import pytest_asyncio
from sqlalchemy import text

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
async def experiment_and_run(client, admin_token, session):
    """Create an experiment and a completed pipeline run."""
    from app.models.pipeline_run import PipelineRun

    # Get org_id from the experiment creation
    resp = await client.post(
        "/api/experiments",
        json={"name": "Review Test Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    experiment_id = resp.json()["id"]

    # Get org_id
    result = await session.execute(text(f"SELECT organization_id FROM experiments WHERE id = {experiment_id}"))
    org_id = result.scalar()

    # Update experiment to pipeline_complete
    await session.execute(text(f"UPDATE experiments SET status = 'pipeline_complete' WHERE id = {experiment_id}"))

    # Create sample
    resp = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={"sample_id_external": "S001", "organism": "Human"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample_id = resp.json()["id"]

    # Create pipeline run directly
    run = PipelineRun(
        organization_id=org_id,
        experiment_id=experiment_id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
    )
    session.add(run)
    await session.flush()
    await session.commit()

    return {"experiment_id": experiment_id, "run_id": run.id, "sample_id": sample_id}


@pytest.mark.asyncio
async def test_create_review_as_comp_bio(client, comp_bio_token, experiment_and_run, session):
    run_id = experiment_and_run["run_id"]
    sample_id = experiment_and_run["sample_id"]

    response = await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={
            "verdict": "approved",
            "notes": "All samples look good.",
            "sample_verdicts": {str(sample_id): {"verdict": "pass", "notes": "Good quality"}},
            "recommended_exclusions": [],
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["verdict"] == "approved"
    assert data["is_active"] is True

    # Verify audit log
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'pipeline_run_review' AND action = 'created'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_create_review_updates_sample_qc(client, comp_bio_token, experiment_and_run, session):
    run_id = experiment_and_run["run_id"]
    sample_id = experiment_and_run["sample_id"]

    await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={
            "verdict": "approved_with_caveats",
            "notes": "One sample borderline.",
            "sample_verdicts": {str(sample_id): {"verdict": "warning", "notes": "Low viability"}},
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    # Check sample QC was updated
    result = await session.execute(text(f"SELECT qc_status FROM samples WHERE id = {sample_id}"))
    qc_status = result.scalar()
    assert qc_status == "warning"


@pytest.mark.asyncio
async def test_create_review_transitions_experiment_status(client, comp_bio_token, experiment_and_run, session):
    run_id = experiment_and_run["run_id"]
    experiment_id = experiment_and_run["experiment_id"]

    await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved", "notes": "Looks great."},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    # Check experiment status changed to "reviewed"
    result = await session.execute(text(f"SELECT status FROM experiments WHERE id = {experiment_id}"))
    status = result.scalar()
    assert status == "reviewed"


@pytest.mark.asyncio
async def test_review_superseding(client, comp_bio_token, experiment_and_run, session):
    run_id = experiment_and_run["run_id"]

    # First review
    resp1 = await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved", "notes": "First review"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    first_review_id = resp1.json()["id"]

    # Reset experiment status for second review test
    experiment_id = experiment_and_run["experiment_id"]
    await session.execute(text(f"UPDATE experiments SET status = 'pipeline_complete' WHERE id = {experiment_id}"))
    await session.commit()

    # Second review (supersedes first)
    resp2 = await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved_with_caveats", "notes": "Second review"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp2.status_code == 201
    second_review_id = resp2.json()["id"]

    # Check first review is superseded
    result = await session.execute(
        text(f"SELECT superseded_by_id FROM pipeline_run_reviews WHERE id = {first_review_id}")
    )
    superseded_by = result.scalar()
    assert superseded_by == second_review_id


@pytest.mark.asyncio
async def test_get_active_review(client, comp_bio_token, experiment_and_run):
    run_id = experiment_and_run["run_id"]

    # Create a review
    await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved", "notes": "Active review"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    response = await client.get(
        f"/api/pipeline-runs/{run_id}/review",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "approved"
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_list_reviews(client, comp_bio_token, experiment_and_run):
    run_id = experiment_and_run["run_id"]

    # Create two reviews
    await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved", "notes": "First"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "rejected", "notes": "Second"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    response = await client.get(
        f"/api/pipeline-runs/{run_id}/reviews",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    reviews = response.json()["reviews"]
    assert len(reviews) == 2
    # Newest first
    assert reviews[0]["notes"] == "Second"


@pytest.mark.asyncio
async def test_bench_cannot_review(client, bench_token, experiment_and_run):
    run_id = experiment_and_run["run_id"]

    response = await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved", "notes": "Should fail"},
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_review(client, viewer_token, experiment_and_run):
    run_id = experiment_and_run["run_id"]

    response = await client.post(
        f"/api/pipeline-runs/{run_id}/reviews",
        json={"verdict": "approved", "notes": "Should fail"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_active_review_404_when_none(client, comp_bio_token, experiment_and_run):
    run_id = experiment_and_run["run_id"]

    response = await client.get(
        f"/api/pipeline-runs/{run_id}/review",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 404
