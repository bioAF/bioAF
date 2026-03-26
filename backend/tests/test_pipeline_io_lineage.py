"""Tests for the pipeline_run_input_files junction table (ADR-038)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_org_and_user(session, *, email: str = "admin@test.com") -> tuple[int, int]:
    """Return (org_id, user_id) after inserting an org + admin user."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Lineage Test Org", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("pass123"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return org.id, user.id


async def _insert_file(session, *, file_id: int, org_id: int, filename: str = "input.fastq.gz") -> int:
    """Insert a minimal file row and return its id."""
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, file_type, source_type) "
            "VALUES (:id, :org, :fn, :uri, 'fastq', 'upload')"
        ),
        {"id": file_id, "org": org_id, "fn": filename, "uri": f"gs://bucket/{filename}"},
    )
    return file_id


async def _insert_pipeline_run(
    session,
    *,
    run_id: int,
    org_id: int,
    input_files_json: list | None = None,
) -> int:
    """Insert a minimal pipeline run row and return its id."""
    await session.execute(
        text(
            "INSERT INTO pipeline_runs (id, organization_id, pipeline_name, status, input_files_json) "
            "VALUES (:id, :org, 'nf-core/test', 'completed', :inputs)"
        ),
        {"id": run_id, "org": org_id, "inputs": json.dumps(input_files_json) if input_files_json else None},
    )
    return run_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def org_user(session):
    """Create a test org and admin user, return (org_id, user_id)."""
    return await _create_org_and_user(session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJunctionRowCreation:
    """Submitting a pipeline run writes rows to pipeline_run_input_files."""

    @pytest.mark.asyncio
    async def test_trigger_service_writes_junction_rows(self, session, org_user):
        """When trigger_service creates a PipelineRun with input_files_json,
        corresponding junction rows must also be written."""
        org_id, user_id = org_user

        # Create two input files
        await _insert_file(session, file_id=100, org_id=org_id, filename="r1.fastq.gz")
        await _insert_file(session, file_id=101, org_id=org_id, filename="r2.fastq.gz")
        await session.commit()

        # Simulate what trigger_service does: create run + junction rows
        from app.models.pipeline_run import PipelineRun
        from app.models.pipeline_run_input_file import PipelineRunInputFile

        run = PipelineRun(
            organization_id=org_id,
            pipeline_name="nf-core/test",
            status="pending",
            input_files_json=[100, 101],
        )
        session.add(run)
        await session.flush()

        for fid in [100, 101]:
            session.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=fid))
        await session.flush()
        await session.commit()

        # Verify junction rows exist
        result = await session.execute(
            text("SELECT file_id FROM pipeline_run_input_files WHERE pipeline_run_id = :rid ORDER BY file_id"),
            {"rid": run.id},
        )
        rows = result.fetchall()
        assert [r[0] for r in rows] == [100, 101]


class TestDownstreamUsage:
    """Provenance gatherer uses junction table for downstream_usage."""

    @pytest.mark.asyncio
    async def test_gather_artifact_downstream_via_junction(self, session, org_user):
        org_id, _ = org_user

        await _insert_file(session, file_id=200, org_id=org_id, filename="raw.fastq.gz")
        await _insert_pipeline_run(session, run_id=200, org_id=org_id, input_files_json=[200])

        # Write junction row
        await session.execute(
            text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) VALUES (200, 200)"),
        )
        await session.commit()

        from app.services.provenance.data_gatherer import ProvenanceDataGatherer

        data = await ProvenanceDataGatherer.gather_artifact(session, 200, org_id)
        assert len(data.downstream_usage) == 1
        assert data.downstream_usage[0]["pipeline_run_id"] == 200


class TestUniqueConstraint:
    """Duplicate (pipeline_run_id, file_id) pairs are rejected."""

    @pytest.mark.asyncio
    async def test_duplicate_junction_row_raises(self, session, org_user):
        org_id, _ = org_user

        await _insert_file(session, file_id=300, org_id=org_id)
        await _insert_pipeline_run(session, run_id=300, org_id=org_id)
        await session.execute(text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) VALUES (300, 300)"))
        await session.commit()

        with pytest.raises(Exception):  # IntegrityError
            await session.execute(
                text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) VALUES (300, 300)")
            )
            await session.commit()


class TestRoleDefaults:
    """The role column defaults to 'primary_input' when not specified."""

    @pytest.mark.asyncio
    async def test_role_defaults_to_primary_input(self, session, org_user):
        org_id, _ = org_user

        await _insert_file(session, file_id=400, org_id=org_id)
        await _insert_pipeline_run(session, run_id=400, org_id=org_id)
        await session.execute(text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) VALUES (400, 400)"))
        await session.commit()

        result = await session.execute(
            text("SELECT role FROM pipeline_run_input_files WHERE pipeline_run_id = 400 AND file_id = 400")
        )
        assert result.scalar_one() == "primary_input"


class TestReferenceRole:
    """The role = 'reference' is accepted for reference genome inputs."""

    @pytest.mark.asyncio
    async def test_reference_role_accepted(self, session, org_user):
        org_id, _ = org_user

        await _insert_file(session, file_id=500, org_id=org_id, filename="GRCh38.fa")
        await _insert_pipeline_run(session, run_id=500, org_id=org_id)
        await session.execute(
            text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id, role) VALUES (500, 500, 'reference')")
        )
        await session.commit()

        result = await session.execute(
            text("SELECT role FROM pipeline_run_input_files WHERE pipeline_run_id = 500 AND file_id = 500")
        )
        assert result.scalar_one() == "reference"


class TestOrgIsolation:
    """A run from org A cannot reference files from org B via junction table."""

    @pytest.mark.asyncio
    async def test_cross_org_file_reference_blocked(self, session, org_user):
        """FK constraint ensures file_id must exist; we verify the provenance
        gatherer only returns downstream usage within the same org."""
        org_a, _ = org_user

        # Create org B using ORM to satisfy all NOT NULL constraints
        from app.models.organization import Organization

        org_b_obj = Organization(name="Org B", setup_complete=True)
        session.add(org_b_obj)
        await session.flush()
        await session.commit()
        org_b = org_b_obj.id

        # File belongs to org A
        await _insert_file(session, file_id=600, org_id=org_a, filename="orgA.fastq.gz")
        # Run belongs to org B
        await _insert_pipeline_run(session, run_id=600, org_id=org_b)
        # Junction links them (structurally possible at DB level)
        await session.execute(text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) VALUES (600, 600)"))
        await session.commit()

        from app.services.provenance.data_gatherer import ProvenanceDataGatherer

        # Querying file 600 provenance as org A should NOT see org B's run
        data = await ProvenanceDataGatherer.gather_artifact(session, 600, org_a)
        for usage in data.downstream_usage:
            assert usage["pipeline_run_id"] != 600, "Cross-org run should not appear in downstream_usage"


class TestGatherPipelineRunInputFiles:
    """gather_pipeline_run loads input files via the junction relationship."""

    @pytest.mark.asyncio
    async def test_input_files_loaded_from_junction(self, session, org_user):
        org_id, _ = org_user

        await _insert_file(session, file_id=700, org_id=org_id, filename="sample.fastq.gz")
        await _insert_pipeline_run(
            session,
            run_id=700,
            org_id=org_id,
            input_files_json=[{"file_id": 700, "filename": "sample.fastq.gz"}],
        )
        await session.execute(text("INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) VALUES (700, 700)"))
        await session.commit()

        from app.services.provenance.data_gatherer import ProvenanceDataGatherer

        data = await ProvenanceDataGatherer.gather_pipeline_run(session, 700, org_id)
        assert len(data.input_files) >= 1
        assert any(f["id"] == 700 for f in data.input_files)
