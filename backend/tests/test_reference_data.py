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


REF_DATA = {
    "name": "GRCh38 GENCODE v43",
    "category": "genome",
    "scope": "public",
    "version": "v43",
    "source_url": "https://www.gencodegenes.org/human/release_43.html",
    "gcs_prefix": "genomes/GRCh38/v43/",
    "total_size_bytes": 3000000000,
    "files": [
        {
            "filename": "genome.fa",
            "gcs_uri": "gs://bioaf-references-test/genomes/GRCh38/v43/genome.fa",
            "size_bytes": 2500000000,
            "md5_checksum": "abc123def456abc123def456abc12345",
            "file_type": "fasta",
        },
        {
            "filename": "genes.gtf",
            "gcs_uri": "gs://bioaf-references-test/genomes/GRCh38/v43/genes.gtf",
            "size_bytes": 500000000,
            "md5_checksum": "def456abc123def456abc123def45678",
            "file_type": "gtf",
        },
    ],
}


@pytest.mark.asyncio
async def test_create_reference_as_comp_bio(client, comp_bio_token, session):
    response = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "GRCh38 GENCODE v43"
    assert data["category"] == "genome"
    assert data["scope"] == "public"
    assert data["status"] == "active"
    assert len(data["files"]) == 2
    assert data["file_count"] == 2

    # Verify audit log
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'reference_dataset' AND action = 'created'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_create_reference_duplicate_rejected(client, comp_bio_token):
    # First creation
    await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    # Duplicate
    response = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_bench_cannot_create_reference(client, bench_token):
    response = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_references(client, comp_bio_token):
    # Create two references
    await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    internal_ref = {**REF_DATA, "name": "Custom markers", "scope": "internal", "version": "v1", "category": "markers"}
    await client.post(
        "/api/references",
        json=internal_ref,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    # List all
    response = await client.get(
        "/api/references",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    # Filter by category
    response = await client.get(
        "/api/references?category=markers",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.json()["total"] == 1

    # Filter by scope
    response = await client.get(
        "/api/references?scope=internal",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.json()["total"] == 1

    # Name search
    response = await client.get(
        "/api/references?name_search=GRCh38",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_bench_can_read_references(client, comp_bio_token, bench_token):
    await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    response = await client.get(
        "/api/references",
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_reference_detail(client, comp_bio_token):
    resp = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    ref_id = resp.json()["id"]

    response = await client.get(
        f"/api/references/{ref_id}",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "GRCh38 GENCODE v43"
    assert len(data["files"]) == 2
    assert data["uploaded_by"] is not None


@pytest.mark.asyncio
async def test_get_reference_404(client, comp_bio_token):
    response = await client.get(
        "/api/references/99999",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_internal_deprecation_immediate(client, comp_bio_token, session):
    """Internal scope: deprecation is immediate (no approval needed)."""
    internal_ref = {**REF_DATA, "name": "Custom markers", "scope": "internal", "version": "v1", "category": "markers"}
    resp = await client.post(
        "/api/references",
        json=internal_ref,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    ref_id = resp.json()["id"]

    response = await client.post(
        f"/api/references/{ref_id}/deprecate",
        json={"deprecation_note": "Replaced by v2"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deprecated"

    # Verify audit log
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'reference_dataset' AND action = 'deprecated'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_public_deprecation_pending_approval(client, comp_bio_token, admin_token, session):
    """Public scope: deprecation requires admin approval."""
    resp = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    ref_id = resp.json()["id"]

    # Request deprecation
    response = await client.post(
        f"/api/references/{ref_id}/deprecate",
        json={"deprecation_note": "Replaced by v44"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending_approval"

    # Admin approves
    response = await client.post(
        f"/api/references/{ref_id}/approve-deprecation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deprecated"

    # Verify audit log
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'reference_dataset' AND action = 'deprecation_approved'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_comp_bio_cannot_approve_deprecation(client, comp_bio_token):
    """Only admin can approve public deprecation."""
    resp = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    ref_id = resp.json()["id"]

    await client.post(
        f"/api/references/{ref_id}/deprecate",
        json={"deprecation_note": "Replaced by v44"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    response = await client.post(
        f"/api/references/{ref_id}/approve-deprecation",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_impact_assessment(client, comp_bio_token, admin_token, session):
    """Impact assessment returns pipeline runs that used a reference."""
    # Create reference
    resp = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    ref_id = resp.json()["id"]

    # Create experiment + pipeline run + linkage
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Impact Test Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]
    result = await session.execute(text(f"SELECT organization_id FROM experiments WHERE id = {exp_id}"))
    org_id = result.scalar()

    from app.models.pipeline_run import PipelineRun

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp_id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
    )
    session.add(run)
    await session.flush()

    # Link
    await session.execute(
        text(f"INSERT INTO pipeline_run_references (pipeline_run_id, reference_dataset_id) VALUES ({run.id}, {ref_id})")
    )
    await session.commit()

    # Get impact
    response = await client.get(
        f"/api/references/{ref_id}/impact",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_pipeline_runs"] == 1
    assert data["total_experiments"] == 1
    assert data["pipeline_runs"][0]["pipeline_name"] == "nf-core/scrnaseq"


@pytest.mark.asyncio
async def test_impact_404_nonexistent(client, comp_bio_token):
    response = await client.get(
        "/api/references/99999/impact",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pipeline_run_references_endpoint(client, comp_bio_token, admin_token, session):
    """GET /api/pipeline-runs/{id}/references returns linked references."""
    # Create reference
    resp = await client.post(
        "/api/references",
        json=REF_DATA,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    ref_id = resp.json()["id"]

    # Create pipeline run
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Run Ref Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]
    result = await session.execute(text(f"SELECT organization_id FROM experiments WHERE id = {exp_id}"))
    org_id = result.scalar()

    from app.models.pipeline_run import PipelineRun

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp_id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
    )
    session.add(run)
    await session.flush()
    await session.execute(
        text(f"INSERT INTO pipeline_run_references (pipeline_run_id, reference_dataset_id) VALUES ({run.id}, {ref_id})")
    )
    await session.commit()

    response = await client.get(
        f"/api/pipeline-runs/{run.id}/references",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    refs = response.json()["references"]
    assert len(refs) == 1
    assert refs[0]["name"] == "GRCh38 GENCODE v43"
