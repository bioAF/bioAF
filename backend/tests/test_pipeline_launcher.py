"""Tests for pipeline launcher refactor (spec tests 16-19).

Tests that the pipeline launcher creates a run record, calls the compute adapter,
updates experiment status, and writes audit log.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample
from app.models.user import User
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services.auth_service import AuthService
from app.services.pipeline_run_service import PipelineRunService
from app.services.bootstrap_roles import seed_builtin_roles


@pytest_asyncio.fixture
async def setup_data(session):
    """Create org, user, experiment, samples, and pipeline catalog entry."""
    org = Organization(name="LauncherTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="launcher@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp = Experiment(
        name="Launcher Test Experiment",
        organization_id=org.id,
        status="fastq_uploaded",
        owner_user_id=user.id,
    )
    session.add(exp)
    await session.flush()

    sample = Sample(
        experiment_id=exp.id,
        sample_id_external="S01-001",
        organism="Homo sapiens",
    )
    session.add(sample)
    await session.flush()

    pipeline = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="bioaf-system-test",
        name="bioAF System Test",
        description="Test pipeline",
        source_type="builtin",
        version="1.0.0",
        default_params_json={"message": "Hello from bioAF", "sleep_seconds": 10},
        is_builtin=True,
        enabled=True,
    )
    session.add(pipeline)
    await session.flush()
    await session.commit()

    return {"org": org, "user": user, "experiment": exp, "sample": sample, "pipeline": pipeline}


class TestSubmitCreatesRunRecord:
    @pytest.mark.asyncio
    async def test_creates_pipeline_run_record(self, session, setup_data):
        """Test 16: submit_pipeline_run creates a pipeline_runs record."""
        data = setup_data
        request = PipelineRunLaunchRequest(
            pipeline_key="bioaf-system-test",
            experiment_id=data["experiment"].id,
        )

        run = await PipelineRunService.launch_run(session, data["org"].id, data["user"].id, request)
        await session.commit()

        assert run.id is not None
        assert run.pipeline_name == "bioAF System Test"
        assert run.organization_id == data["org"].id
        assert run.experiment_id == data["experiment"].id
        assert run.submitted_by_user_id == data["user"].id


class TestSubmitCallsComputeAdapter:
    @pytest.mark.asyncio
    async def test_calls_compute_adapter_submit(self, session, setup_data):
        """Test 17: submit calls compute_adapter.submit_job()."""
        data = setup_data
        request = PipelineRunLaunchRequest(
            pipeline_key="bioaf-system-test",
            experiment_id=data["experiment"].id,
        )

        # The adapter is called in local mode by default, which succeeds
        run = await PipelineRunService.launch_run(session, data["org"].id, data["user"].id, request)
        await session.commit()

        # In local mode, slurm_job_id gets set to the mock job_id
        assert run.slurm_job_id is not None
        assert run.slurm_job_id.startswith("local-")
        assert run.status == "running"
        assert run.started_at is not None


class TestSubmitUpdatesExperimentStatus:
    @pytest.mark.asyncio
    async def test_updates_experiment_to_processing(self, session, setup_data):
        """Test 18: submit updates experiment status to processing."""
        data = setup_data
        request = PipelineRunLaunchRequest(
            pipeline_key="bioaf-system-test",
            experiment_id=data["experiment"].id,
        )

        await PipelineRunService.launch_run(session, data["org"].id, data["user"].id, request)
        await session.commit()

        # Reload experiment
        row = (
            await session.execute(
                text("SELECT status FROM experiments WHERE id = :id").bindparams(id=data["experiment"].id)
            )
        ).fetchone()
        assert row[0] == "processing"


class TestSubmitWritesAuditLog:
    @pytest.mark.asyncio
    async def test_writes_audit_log_entry(self, session, setup_data):
        """Test 19: submit writes an audit log entry."""
        data = setup_data
        request = PipelineRunLaunchRequest(
            pipeline_key="bioaf-system-test",
            experiment_id=data["experiment"].id,
        )

        run = await PipelineRunService.launch_run(session, data["org"].id, data["user"].id, request)
        await session.commit()

        # Check audit log
        row = (
            await session.execute(
                text(
                    "SELECT action, entity_type, entity_id FROM audit_log "
                    "WHERE entity_type = 'pipeline_run' AND entity_id = :id AND action = 'launch'"
                ).bindparams(id=run.id)
            )
        ).fetchone()

        assert row is not None
        assert row[0] == "launch"
        assert row[1] == "pipeline_run"
        assert row[2] == run.id
