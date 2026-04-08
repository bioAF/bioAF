"""Tests for reference genome auto-population on pipeline run launch."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Auto-populate Genome Test",
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
            sample_id_unique=f"AUTOP_{i + 1}",
            organism="Homo sapiens",
        )
        session.add(s)
        sample_list.append(s)
    await session.flush()
    await session.commit()
    return sample_list


@pytest_asyncio.fixture
async def reference_dataset(session, admin_user):
    from app.models.reference_dataset import ReferenceDataset

    ref = ReferenceDataset(
        organization_id=admin_user.organization_id,
        name="GRCh38",
        category="genome",
        scope="public",
        version="release-104",
        gcs_prefix="genomes/GRCh38/release-104",
        status="active",
    )
    session.add(ref)
    await session.flush()
    await session.commit()
    return ref


@pytest_asyncio.fixture
async def initialized_catalog(client, admin_token):
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})


@pytest.mark.asyncio
async def test_auto_populate_reference_genome_from_linked_dataset(
    client, admin_token, experiment, samples, reference_dataset, initialized_catalog
):
    """When parameters reference a path matching a reference dataset, reference_genome is auto-populated."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
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
                "parameters": {
                    "genome_fasta": "/data/references/genomes/GRCh38/release-104/genome.fa",
                },
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reference_genome"] == "GRCh38 release-104"


@pytest.mark.asyncio
async def test_auto_populate_reference_genome_fallback_to_params_genome_key(
    client, admin_token, experiment, samples, initialized_catalog
):
    """When no reference dataset is linked but parameters contain a genome key, use that value."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
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
                "parameters": {
                    "genome": "GRCh38",
                },
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reference_genome"] == "GRCh38"


@pytest.mark.asyncio
async def test_auto_populate_reference_genome_fallback_to_reference_genome_key(
    client, admin_token, experiment, samples, initialized_catalog
):
    """Fallback to parameters_json reference_genome key."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
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
                "parameters": {
                    "reference_genome": "mm10",
                },
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reference_genome"] == "mm10"


@pytest.mark.asyncio
async def test_explicit_reference_genome_not_overwritten(
    client, admin_token, experiment, samples, reference_dataset, initialized_catalog
):
    """When reference_genome is explicitly provided, it is not overwritten by auto-population."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
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
                "parameters": {
                    "genome_fasta": "/data/references/genomes/GRCh38/release-104/genome.fa",
                },
                "reference_genome": "custom-genome-v1",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reference_genome"] == "custom-genome-v1"
