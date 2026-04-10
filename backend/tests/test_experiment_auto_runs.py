"""Tests for experiment auto-run configuration and pending run management."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample
from app.models.sequencing_batch import SequencingBatch
from app.models.manifest_entry import ManifestEntry

pytestmark = pytest.mark.asyncio


# ---- Fixtures ----


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Auto-Run Test Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def pipeline(session, admin_user):
    entry = PipelineCatalogEntry(
        organization_id=admin_user.organization_id,
        pipeline_key="nf-core/scrnaseq",
        name="nf-core/scrnaseq",
        source_type="github",
        source_url="https://github.com/nf-core/scrnaseq",
        version="2.7.1",
        default_params_json={"aligner": "cellranger"},
        enabled=True,
    )
    session.add(entry)
    await session.flush()
    await session.commit()
    return entry


@pytest_asyncio.fixture
async def sample_with_manifest(session, admin_user, experiment):
    """Create a sample with a sequencing batch and manifest entries."""
    batch = SequencingBatch(
        organization_id=admin_user.organization_id,
        code="SEQ-AUTO-001",
        status="ingesting",
        expected_file_count=2,
    )
    session.add(batch)
    await session.flush()

    sample = Sample(
        experiment_id=experiment.id,
        sample_id_unique="AUTO_S001",
        sequencing_batch_id=batch.id,
    )
    session.add(sample)
    await session.flush()

    entry1 = ManifestEntry(
        sequencing_batch_id=batch.id,
        expected_filename="AUTO_S001_S1_L001_R1_001.fastq.gz",
        expected_md5="aaa111",
        resolved_sample_id=sample.id,
        resolved_experiment_id=experiment.id,
        status="pending",
    )
    entry2 = ManifestEntry(
        sequencing_batch_id=batch.id,
        expected_filename="AUTO_S001_S1_L001_R2_001.fastq.gz",
        expected_md5="bbb222",
        resolved_sample_id=sample.id,
        resolved_experiment_id=experiment.id,
        status="pending",
    )
    session.add_all([entry1, entry2])
    await session.flush()
    await session.commit()

    return {"sample": sample, "batch": batch, "entries": [entry1, entry2]}


# ---- Auto-Run Config CRUD Tests ----


async def test_create_auto_run_config(client, admin_token, experiment, pipeline):
    resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={
            "pipeline_key": "nf-core/scrnaseq",
            "parameters": {"aligner": "star"},
            "delay_minutes": 10,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pipeline_key"] == "nf-core/scrnaseq"
    assert data["parameters"]["aligner"] == "star"
    assert data["delay_minutes"] == 10
    assert data["enabled"] is True
    assert data["experiment_id"] == experiment.id


async def test_create_auto_run_config_invalid_pipeline(client, admin_token, experiment):
    resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nonexistent/pipeline"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


async def test_list_auto_run_configs(client, admin_token, experiment, pipeline):
    await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq", "delay_minutes": 5},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = await client.get(
        f"/api/experiments/{experiment.id}/auto-runs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    configs = resp.json()
    assert isinstance(configs, list)
    assert len(configs) >= 1
    assert any(c["pipeline_key"] == "nf-core/scrnaseq" for c in configs)


async def test_update_auto_run_config(client, admin_token, experiment, pipeline):
    create_resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq", "delay_minutes": 5},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    config_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/experiments/{experiment.id}/auto-runs/{config_id}",
        json={"delay_minutes": 15, "parameters": {"aligner": "kallisto"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["delay_minutes"] == 15
    assert resp.json()["parameters"]["aligner"] == "kallisto"


async def test_toggle_auto_run_config(client, admin_token, experiment, pipeline):
    create_resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    config_id = create_resp.json()["id"]
    assert create_resp.json()["enabled"] is True

    resp = await client.patch(
        f"/api/experiments/{experiment.id}/auto-runs/{config_id}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_delete_auto_run_config(client, admin_token, experiment, pipeline, session):
    create_resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    config_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/api/experiments/{experiment.id}/auto-runs/{config_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Verify config is gone
    get_resp = await client.get(
        f"/api/experiments/{experiment.id}/auto-runs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert all(c["id"] != config_id for c in get_resp.json())


async def test_delete_config_cancels_waiting_runs(
    client, admin_token, experiment, pipeline, sample_with_manifest, session
):
    """Deleting a config should cascade-delete all pending runs."""
    sample = sample_with_manifest["sample"]

    create_resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq", "delay_minutes": 0},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    config_id = create_resp.json()["id"]

    # Insert a waiting pending run using the real sample
    from datetime import datetime, timezone

    await session.execute(
        text(
            "INSERT INTO pending_auto_runs "
            "(organization_id, auto_run_config_id, experiment_id, sample_id, "
            "sample_completed_at, scheduled_at, status) "
            "VALUES (:org, :config, :exp, :sid, :now, :now, 'waiting')"
        ),
        {
            "org": experiment.organization_id,
            "config": config_id,
            "exp": experiment.id,
            "sid": sample.id,
            "now": datetime.now(timezone.utc),
        },
    )
    await session.commit()

    await client.delete(
        f"/api/experiments/{experiment.id}/auto-runs/{config_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Pending run should be cascade-deleted with the config
    result = await session.execute(
        text("SELECT count(*) FROM pending_auto_runs WHERE auto_run_config_id = :cid"),
        {"cid": config_id},
    )
    assert result.scalar() == 0


async def test_viewer_cannot_create_auto_run(client, viewer_token, experiment, pipeline):
    resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


# ---- Pending Run List Test ----


async def test_list_pending_runs(client, admin_token, experiment, pipeline, session):
    create_resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    config_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/experiments/{experiment.id}/auto-runs/{config_id}/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---- Table Existence Tests ----


async def test_experiment_auto_runs_table_exists(session):
    result = await session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'experiment_auto_runs'")
    )
    assert result.scalar() == 1


async def test_pending_auto_runs_table_exists(session):
    result = await session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'pending_auto_runs'")
    )
    assert result.scalar() == 1


# ---- Sample Completeness -> Pending Run Creation Tests ----


async def test_sample_completeness_creates_pending_run(
    client, admin_token, experiment, pipeline, sample_with_manifest, session
):
    """When all manifest entries for a sample are verified and an auto-run config
    exists, a pending_auto_run should be created."""
    sample = sample_with_manifest["sample"]

    # Create auto-run config
    await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq", "delay_minutes": 10},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Simulate both manifest entries becoming verified
    await session.execute(
        text("UPDATE manifest_entries SET status = 'verified' WHERE resolved_sample_id = :sid"),
        {"sid": sample.id},
    )
    await session.commit()

    # Call the service function that would be called from ingest
    from app.services.auto_run_service import AutoRunService

    await AutoRunService.check_and_queue_auto_runs(
        session,
        sample_id=sample.id,
        sequencing_batch_id=sample_with_manifest["batch"].id,
    )
    await session.commit()

    # Verify pending run was created
    result = await session.execute(
        text("SELECT status, scheduled_at FROM pending_auto_runs WHERE sample_id = :sid"),
        {"sid": sample.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "waiting"


async def test_incomplete_sample_does_not_queue(
    client, admin_token, experiment, pipeline, sample_with_manifest, session
):
    """If not all manifest entries are verified, no pending run should be created."""
    sample = sample_with_manifest["sample"]

    await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Only verify one of two entries
    await session.execute(
        text(
            "UPDATE manifest_entries SET status = 'verified' "
            "WHERE resolved_sample_id = :sid AND expected_filename LIKE '%R1%'"
        ),
        {"sid": sample.id},
    )
    await session.commit()

    from app.services.auto_run_service import AutoRunService

    await AutoRunService.check_and_queue_auto_runs(
        session,
        sample_id=sample.id,
        sequencing_batch_id=sample_with_manifest["batch"].id,
    )
    await session.commit()

    result = await session.execute(
        text("SELECT count(*) FROM pending_auto_runs WHERE sample_id = :sid"),
        {"sid": sample.id},
    )
    assert result.scalar() == 0


async def test_idempotent_pending_run_creation(
    client, admin_token, experiment, pipeline, sample_with_manifest, session
):
    """Calling check_and_queue twice should not create duplicate pending runs."""
    sample = sample_with_manifest["sample"]

    await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq", "delay_minutes": 0},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await session.execute(
        text("UPDATE manifest_entries SET status = 'verified' WHERE resolved_sample_id = :sid"),
        {"sid": sample.id},
    )
    await session.commit()

    from app.services.auto_run_service import AutoRunService

    await AutoRunService.check_and_queue_auto_runs(
        session, sample_id=sample.id, sequencing_batch_id=sample_with_manifest["batch"].id
    )
    await session.commit()
    await AutoRunService.check_and_queue_auto_runs(
        session, sample_id=sample.id, sequencing_batch_id=sample_with_manifest["batch"].id
    )
    await session.commit()

    result = await session.execute(
        text("SELECT count(*) FROM pending_auto_runs WHERE sample_id = :sid"),
        {"sid": sample.id},
    )
    assert result.scalar() == 1


async def test_checksum_mismatch_cancels_pending_run(
    client, admin_token, experiment, pipeline, sample_with_manifest, session
):
    """A checksum mismatch should cancel any waiting pending runs for that sample."""
    sample = sample_with_manifest["sample"]

    await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq", "delay_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # First, make the sample complete so a pending run is created
    await session.execute(
        text("UPDATE manifest_entries SET status = 'verified' WHERE resolved_sample_id = :sid"),
        {"sid": sample.id},
    )
    await session.commit()

    from app.services.auto_run_service import AutoRunService

    await AutoRunService.check_and_queue_auto_runs(
        session, sample_id=sample.id, sequencing_batch_id=sample_with_manifest["batch"].id
    )
    await session.commit()

    # Now simulate a checksum mismatch on one entry
    await AutoRunService.cancel_pending_runs_for_sample(session, sample_id=sample.id, reason="checksum_mismatch")
    await session.commit()

    result = await session.execute(
        text("SELECT status, cancelled_reason FROM pending_auto_runs WHERE sample_id = :sid"),
        {"sid": sample.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "cancelled"
    assert row[1] == "checksum_mismatch"


async def test_disabled_config_does_not_queue(client, admin_token, experiment, pipeline, sample_with_manifest, session):
    """A disabled auto-run config should not create pending runs."""
    sample = sample_with_manifest["sample"]

    create_resp = await client.post(
        f"/api/experiments/{experiment.id}/auto-runs",
        json={"pipeline_key": "nf-core/scrnaseq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    config_id = create_resp.json()["id"]

    # Disable the config
    await client.patch(
        f"/api/experiments/{experiment.id}/auto-runs/{config_id}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await session.execute(
        text("UPDATE manifest_entries SET status = 'verified' WHERE resolved_sample_id = :sid"),
        {"sid": sample.id},
    )
    await session.commit()

    from app.services.auto_run_service import AutoRunService

    await AutoRunService.check_and_queue_auto_runs(
        session, sample_id=sample.id, sequencing_batch_id=sample_with_manifest["batch"].id
    )
    await session.commit()

    result = await session.execute(
        text("SELECT count(*) FROM pending_auto_runs WHERE sample_id = :sid"),
        {"sid": sample.id},
    )
    assert result.scalar() == 0
