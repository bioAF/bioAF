"""Tests that pipeline_run_service.launch_run passes the correct job_spec
to the compute adapter, including pipeline_name for K8s Job labels.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_jobspec@test.com",
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
async def experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="JobSpec Test Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def samples(session, experiment):
    from app.models.sample import Sample

    sample_list = []
    for i in range(2):
        s = Sample(
            experiment_id=experiment.id,
            sample_id_unique=f"JS_{i + 1}",
            organism="Homo sapiens",
            tissue_type="PBMC",
        )
        session.add(s)
        sample_list.append(s)
    await session.flush()
    await session.commit()
    return sample_list


@pytest_asyncio.fixture
async def initialized_catalog(client, admin_token):
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})


class TestLaunchRunJobSpec:
    @pytest.mark.asyncio
    async def test_job_spec_includes_pipeline_name(self, client, admin_token, experiment, samples, initialized_catalog):
        """launch_run should pass pipeline_name in the job_spec for K8s labels."""
        captured_spec = {}

        async def capture_submit(job_spec):
            captured_spec.update(job_spec)
            return {
                "job_id": "bioaf-pipeline-test",
                "namespace": "bioaf-pipelines",
                "status": "queued",
                "estimated_cost": {"estimated_cost_usd": 0.50},
            }

        mock_adapter = MagicMock()
        mock_adapter.submit_job = AsyncMock(side_effect=capture_submit)

        with (
            patch("app.services.pipeline_run_service.get_compute_adapter", return_value=mock_adapter),
            patch(
                "app.services.experiment_service.ExperimentService.update_status",
                new_callable=AsyncMock,
            ),
        ):
            response = await client.post(
                "/api/pipeline-runs",
                json={
                    "pipeline_key": "nf-core/scrnaseq",
                    "experiment_id": experiment.id,
                    "sample_ids": [s.id for s in samples],
                    "parameters": {"aligner": "cellranger"},
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        assert "pipeline_name" in captured_spec, f"job_spec missing pipeline_name. Keys: {list(captured_spec.keys())}"
        assert captured_spec["pipeline_name"] == "nf-core/scrnaseq"

    @pytest.mark.asyncio
    async def test_job_spec_includes_pipeline_source_and_version(
        self, client, admin_token, experiment, samples, initialized_catalog
    ):
        """launch_run should pass pipeline_source and pipeline_version."""
        captured_spec = {}

        async def capture_submit(job_spec):
            captured_spec.update(job_spec)
            return {
                "job_id": "bioaf-pipeline-test",
                "namespace": "bioaf-pipelines",
                "status": "queued",
                "estimated_cost": {"estimated_cost_usd": 0.50},
            }

        mock_adapter = MagicMock()
        mock_adapter.submit_job = AsyncMock(side_effect=capture_submit)

        with (
            patch("app.services.pipeline_run_service.get_compute_adapter", return_value=mock_adapter),
            patch(
                "app.services.experiment_service.ExperimentService.update_status",
                new_callable=AsyncMock,
            ),
        ):
            response = await client.post(
                "/api/pipeline-runs",
                json={
                    "pipeline_key": "nf-core/scrnaseq",
                    "experiment_id": experiment.id,
                    "parameters": {},
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        assert captured_spec.get("pipeline_source") == "https://github.com/nf-core/scrnaseq"
        assert captured_spec.get("pipeline_version") == "2.7.1"

    @pytest.mark.asyncio
    async def test_job_spec_includes_sample_sheet(self, client, admin_token, experiment, samples, initialized_catalog):
        """launch_run should pass generated sample_sheet CSV."""
        captured_spec = {}

        async def capture_submit(job_spec):
            captured_spec.update(job_spec)
            return {
                "job_id": "bioaf-pipeline-test",
                "namespace": "bioaf-pipelines",
                "status": "queued",
                "estimated_cost": {"estimated_cost_usd": 0.50},
            }

        mock_adapter = MagicMock()
        mock_adapter.submit_job = AsyncMock(side_effect=capture_submit)

        with (
            patch("app.services.pipeline_run_service.get_compute_adapter", return_value=mock_adapter),
            patch(
                "app.services.experiment_service.ExperimentService.update_status",
                new_callable=AsyncMock,
            ),
        ):
            response = await client.post(
                "/api/pipeline-runs",
                json={
                    "pipeline_key": "nf-core/scrnaseq",
                    "experiment_id": experiment.id,
                    "sample_ids": [s.id for s in samples],
                    "parameters": {},
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        assert "sample_sheet" in captured_spec
        assert captured_spec["sample_sheet"]  # non-empty
        assert "JS_1" in captured_spec["sample_sheet"]  # sample ID present
