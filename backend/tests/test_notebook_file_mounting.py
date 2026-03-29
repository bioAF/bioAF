"""Tests for input file mounting in notebook sessions."""

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy import text


@pytest_asyncio.fixture
async def experiment_with_files(session, admin_user):
    """Create an experiment with files for testing."""
    # Create experiment
    await session.execute(
        text("INSERT INTO experiments (id, organization_id, name, status) VALUES (:id, :org_id, :name, :status)"),
        {"id": 500, "org_id": admin_user.organization_id, "name": "Test Exp", "status": "registered"},
    )

    # Create files
    for i, (filename, size) in enumerate([("matrix.h5ad", 450_000_000), ("counts.csv", 12_000_000)], start=1):
        await session.execute(
            text(
                "INSERT INTO files (id, organization_id, gcs_uri, filename, size_bytes, file_type, experiment_id, source_type) "
                "VALUES (:id, :org_id, :gcs_uri, :filename, :size, :ftype, :exp_id, 'upload')"
            ),
            {
                "id": 600 + i,
                "org_id": admin_user.organization_id,
                "gcs_uri": f"gs://bucket/files/{filename}",
                "filename": filename,
                "size": size,
                "ftype": filename.split(".")[-1],
                "exp_id": 500,
            },
        )

    # Seed platform_config for compute
    for key, value in [
        ("compute_deployed", "true"),
        ("bioaf_scrna_image", "us-docker.pkg.dev/test/bioaf/scrna:latest"),
        ("working_bucket_name", "bioaf-working"),
    ]:
        await session.execute(
            text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
            {"k": key, "v": value},
        )

    await session.commit()
    return {"experiment_id": 500, "file_ids": [601, 602]}


class TestInputFileMounting:
    @pytest.mark.asyncio
    async def test_launch_with_input_file_ids_creates_session_file_rows(
        self, session, admin_user, experiment_with_files
    ):
        """Launching with input_file_ids should create NotebookSessionFile input rows."""
        from app.services.notebook_service import NotebookService

        ns = await NotebookService.launch_session(
            session,
            user_id=admin_user.id,
            org_id=admin_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
            experiment_id=500,
            input_file_ids=[601, 602],
        )
        await session.commit()

        result = await session.execute(
            text("SELECT file_id, access_type FROM notebook_session_files WHERE session_id = :sid ORDER BY file_id"),
            {"sid": ns.id},
        )
        rows = result.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 601
        assert rows[0][1] == "input"
        assert rows[1][0] == 602
        assert rows[1][1] == "input"

    @pytest.mark.asyncio
    async def test_launch_without_input_files_creates_no_session_file_rows(
        self, session, admin_user, experiment_with_files
    ):
        """Launching without input_file_ids should create no NotebookSessionFile rows."""
        from app.services.notebook_service import NotebookService

        ns = await NotebookService.launch_session(
            session,
            user_id=admin_user.id,
            org_id=admin_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
            experiment_id=500,
        )
        await session.commit()

        result = await session.execute(
            text("SELECT COUNT(*) FROM notebook_session_files WHERE session_id = :sid"),
            {"sid": ns.id},
        )
        assert result.scalar() == 0

    @pytest.mark.asyncio
    async def test_adapter_spec_includes_input_files(self, session, admin_user, experiment_with_files):
        """The adapter spec should include input_files with GCS URIs."""
        from app.services.notebook_service import NotebookService
        from app.adapters.registry import get_notebook_adapter

        adapter = get_notebook_adapter()
        original_launch = adapter.launch_session
        captured_spec = {}

        async def capture_spec(spec):
            captured_spec.update(spec)
            return await original_launch(spec)

        with patch.object(adapter, "launch_session", side_effect=capture_spec):
            await NotebookService.launch_session(
                session,
                user_id=admin_user.id,
                org_id=admin_user.organization_id,
                session_type="jupyter",
                resource_profile="small",
                experiment_id=500,
                input_file_ids=[601],
            )

        assert "input_files" in captured_spec
        assert len(captured_spec["input_files"]) == 1
        assert captured_spec["input_files"][0]["file_id"] == 601
        assert "gs://" in captured_spec["input_files"][0]["gcs_uri"]

    @pytest.mark.asyncio
    async def test_org_isolation_rejects_other_org_files(self, session, admin_user, experiment_with_files):
        """Cannot mount files from another organization."""
        from app.services.notebook_service import NotebookService

        # Create a file in a different org using ORM
        from app.models.organization import Organization
        from app.models.file import File

        other_org = Organization(name="Other Org", setup_complete=True)
        session.add(other_org)
        await session.flush()

        other_file = File(
            organization_id=other_org.id,
            gcs_uri="gs://other-bucket/file.csv",
            filename="other.csv",
            size_bytes=1000,
            file_type="csv",
            source_type="upload",
        )
        session.add(other_file)
        await session.flush()
        other_file_id = other_file.id
        await session.commit()

        with pytest.raises(ValueError, match="not found or not accessible"):
            await NotebookService.launch_session(
                session,
                user_id=admin_user.id,
                org_id=admin_user.organization_id,
                session_type="jupyter",
                resource_profile="small",
                experiment_id=500,
                input_file_ids=[other_file_id],
            )


class TestInputFileMountingAPI:
    @pytest.mark.asyncio
    async def test_launch_api_with_input_file_ids(
        self, client, admin_token, session, admin_user, experiment_with_files
    ):
        """The launch API should accept input_file_ids."""
        response = await client.post(
            "/api/v1/notebooks/sessions",
            json={
                "session_type": "jupyter",
                "resource_profile": "small",
                "experiment_id": 500,
                "input_file_ids": [601, 602],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

        # Verify NotebookSessionFile rows were created
        result = await session.execute(text("SELECT COUNT(*) FROM notebook_session_files WHERE access_type = 'input'"))
        assert result.scalar() >= 2
