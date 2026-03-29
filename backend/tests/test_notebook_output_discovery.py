"""Tests for notebook output file discovery and registration."""

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def session_with_experiment(session, admin_user):
    """Create a compute session linked to an experiment."""
    await session.execute(
        text("INSERT INTO experiments (id, organization_id, name, status) VALUES (:id, :org_id, :name, :status)"),
        {"id": 800, "org_id": admin_user.organization_id, "name": "Output Test Exp", "status": "registered"},
    )

    from app.models.notebook_session import ComputeSession
    from datetime import datetime, timezone

    cs = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        experiment_id=800,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.add(cs)
    await session.flush()
    await session.commit()
    return cs


class TestOutputRegistration:
    @pytest.mark.asyncio
    async def test_register_outputs_creates_file_records(
        self, client, admin_token, session, admin_user, session_with_experiment
    ):
        """POST register-outputs should create File records with source_type=notebook_output."""
        cs = session_with_experiment
        response = await client.post(
            f"/api/v1/notebooks/sessions/{cs.id}/register-outputs",
            json={
                "outputs": [
                    {
                        "filename": "analysis.h5ad",
                        "size_bytes": 500_000_000,
                        "gcs_uri": "gs://bucket/outputs/analysis.h5ad",
                    },
                    {"filename": "figure.png", "size_bytes": 2_000_000, "gcs_uri": "gs://bucket/outputs/figure.png"},
                ],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["registered_count"] == 2

        # Verify File records
        result = await session.execute(
            text(
                "SELECT filename, source_type, source_notebook_session_id "
                "FROM files WHERE source_notebook_session_id = :sid ORDER BY filename"
            ),
            {"sid": cs.id},
        )
        rows = result.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "analysis.h5ad"
        assert rows[0][1] == "notebook_output"
        assert rows[0][2] == cs.id

    @pytest.mark.asyncio
    async def test_register_outputs_creates_session_file_rows(
        self, client, admin_token, session, admin_user, session_with_experiment
    ):
        """POST register-outputs should create NotebookSessionFile output rows."""
        cs = session_with_experiment
        await client.post(
            f"/api/v1/notebooks/sessions/{cs.id}/register-outputs",
            json={
                "outputs": [
                    {"filename": "result.csv", "size_bytes": 1_000, "gcs_uri": "gs://bucket/result.csv"},
                ],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        result = await session.execute(
            text("SELECT access_type FROM notebook_session_files WHERE session_id = :sid"),
            {"sid": cs.id},
        )
        rows = result.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "output"

    @pytest.mark.asyncio
    async def test_register_outputs_excludes_system_files(
        self, client, admin_token, session, admin_user, session_with_experiment
    ):
        """System files like .bash_history should be excluded."""
        cs = session_with_experiment
        response = await client.post(
            f"/api/v1/notebooks/sessions/{cs.id}/register-outputs",
            json={
                "outputs": [
                    {"filename": ".bash_history", "size_bytes": 500, "gcs_uri": "gs://bucket/.bash_history"},
                    {"filename": ".Rhistory", "size_bytes": 300, "gcs_uri": "gs://bucket/.Rhistory"},
                    {"filename": "real_output.csv", "size_bytes": 1000, "gcs_uri": "gs://bucket/real_output.csv"},
                ],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["registered_count"] == 1

    @pytest.mark.asyncio
    async def test_register_outputs_excludes_data_dir_files(
        self, client, admin_token, session, admin_user, session_with_experiment
    ):
        """Files from /data/ (inputs) should be excluded."""
        cs = session_with_experiment
        response = await client.post(
            f"/api/v1/notebooks/sessions/{cs.id}/register-outputs",
            json={
                "outputs": [
                    {
                        "filename": "data/input_file.h5ad",
                        "size_bytes": 500_000,
                        "gcs_uri": "gs://bucket/data/input.h5ad",
                    },
                    {"filename": "notebook_output.ipynb", "size_bytes": 10_000, "gcs_uri": "gs://bucket/output.ipynb"},
                ],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["registered_count"] == 1

    @pytest.mark.asyncio
    async def test_register_outputs_session_not_found(self, client, admin_token):
        """Registering outputs for a nonexistent session returns 404."""
        response = await client.post(
            "/api/v1/notebooks/sessions/99999/register-outputs",
            json={"outputs": [{"filename": "a.csv", "size_bytes": 100, "gcs_uri": "gs://x/a.csv"}]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 404
