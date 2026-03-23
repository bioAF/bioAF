import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

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
async def sample_job(session, admin_user):
    from app.models.slurm_job import SlurmJob

    job = SlurmJob(
        organization_id=admin_user.organization_id,
        user_id=admin_user.id,
        slurm_job_id="12345",
        job_name="test-job",
        partition="standard",
        status="running",
        cpu_requested=4,
        memory_gb_requested=8,
    )
    session.add(job)
    await session.flush()
    await session.commit()
    return job


@pytest.mark.asyncio
async def test_cluster_status_endpoint(client, admin_token):
    """Cluster status endpoint returns expected structure via BAL adapter."""
    response = await client.get(
        "/api/compute/cluster",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["controller_status"] == "running"
    # Local-mode adapter returns 3 node pools
    assert len(data["partitions"]) == 3
    pool_names = {p["name"] for p in data["partitions"]}
    assert "bioaf-platform" in pool_names
    assert "bioaf-pipelines" in pool_names
    assert "bioaf-interactive" in pool_names
    assert data["total_nodes"] == 1
    assert data["active_nodes"] == 1
    assert data["queue_depth"] == 0


@pytest.mark.asyncio
async def test_job_list(client, admin_token, sample_job):
    """Job list returns jobs."""
    response = await client.get(
        "/api/compute/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["jobs"]) >= 1
    assert data["jobs"][0]["slurm_job_id"] == "12345"


@pytest.mark.asyncio
async def test_job_list_with_status_filter(client, admin_token, sample_job):
    """Job list filters by status."""
    response = await client.get(
        "/api/compute/jobs?status=running",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(j["status"] == "running" for j in data["jobs"])


@pytest.mark.asyncio
async def test_job_cancel(client, admin_token, sample_job):
    """Admin can cancel any job."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="",
    ):
        response = await client.post(
            f"/api/compute/jobs/{sample_job.id}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_job_cancel_own_only_for_comp_bio(client, comp_bio_token, sample_job):
    """Comp Bio can only cancel own jobs."""
    response = await client.post(
        f"/api/compute/jobs/{sample_job.id}/cancel",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_job_resubmit(client, admin_token, sample_job):
    """Resubmit creates a new job record."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="99999",
    ):
        response = await client.post(
            f"/api/compute/jobs/{sample_job.id}/resubmit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["slurm_job_id"] == "99999"
        assert data["id"] != sample_job.id


@pytest.mark.asyncio
async def test_job_cancel_creates_audit_entry(client, admin_token, sample_job, session):
    """Job cancel writes an audit log entry."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="",
    ):
        response = await client.post(
            f"/api/compute/jobs/{sample_job.id}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "slurm_job",
            AuditLog.action == "cancel",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_bench_user_cannot_access_compute(client, bench_token):
    """Bench users cannot access compute endpoints."""
    response = await client.get(
        "/api/compute/jobs",
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403
