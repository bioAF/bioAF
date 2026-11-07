"""A record whose released-schema timestamp is NULL is returned, with the time as null (plan 9, section 1).

Every application writer leaves these timestamps to their ``now()`` default, so a NULL can only come
from SQL run outside the application, and nothing records what the time was. Owner decision
(2026-11-07): the response field is optional and carries null; the record is not hidden and the
endpoint does not fail. Each test creates a complete record, then clears its timestamps the way such
SQL would, and reads it back through the endpoint people use.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.experiment_auto_run import ExperimentAutoRun
from app.models.manifest_entry import ManifestEntry
from app.models.pending_auto_run import PendingAutoRun
from app.models.role import Role
from app.models.sample import Sample
from app.models.sample_batch import SampleBatch
from app.models.sequencing_batch import SequencingBatch


async def _clear(session, table: str, row_id: int, *columns: str) -> None:
    assignments = ", ".join(f"{c} = NULL" for c in columns)
    await session.execute(text(f"UPDATE {table} SET {assignments} WHERE id = :id"), {"id": row_id})
    await session.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _experiment(session, admin_user) -> Experiment:
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Legacy timestamps",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.commit()
    return exp


@pytest.mark.asyncio
async def test_roles_list_returns_a_role_with_no_recorded_creation_time(client, session, admin_user, admin_token):
    role = Role(organization_id=admin_user.organization_id, name="Legacy role", is_system=False)
    session.add(role)
    await session.commit()
    await _clear(session, "roles", role.id, "created_at")

    resp = await client.get("/api/roles", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    listed = {r["name"]: r for r in resp.json()["roles"]}
    assert listed["Legacy role"]["created_at"] is None
    # Complete roles are unchanged.
    assert all(r["created_at"] for name, r in listed.items() if name != "Legacy role")

    one = await client.get(f"/api/roles/{role.id}", headers=_auth(admin_token))
    assert one.status_code == 200 and one.json()["created_at"] is None


@pytest.mark.asyncio
async def test_environments_return_an_environment_and_version_with_no_recorded_times(
    client, session, admin_user, admin_token
):
    env = Environment(organization_id=admin_user.organization_id, name="legacy-env", created_by_user_id=admin_user.id)
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        definition_format="dockerfile",
        definition_content="FROM scratch",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.commit()
    await _clear(session, "environments", env.id, "created_at", "updated_at")
    await _clear(session, "environment_versions", version.id, "created_at")

    listing = await client.get("/api/v1/environments", headers=_auth(admin_token))
    assert listing.status_code == 200, listing.text
    [row] = [e for e in listing.json()["environments"] if e["id"] == env.id]
    assert row["created_at"] is None and row["updated_at"] is None

    detail = await client.get(f"/api/v1/environments/{env.id}", headers=_auth(admin_token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["created_at"] is None and body["updated_at"] is None
    assert [v["created_at"] for v in body["versions"]] == [None]

    one = await client.get(f"/api/v1/environments/{env.id}/versions/{version.id}", headers=_auth(admin_token))
    assert one.status_code == 200 and one.json()["created_at"] is None


@pytest.mark.asyncio
async def test_custom_pipelines_return_a_pipeline_and_version_with_no_recorded_times(
    client, session, admin_user, admin_token
):
    env = Environment(organization_id=admin_user.organization_id, name="cp-env", created_by_user_id=admin_user.id)
    session.add(env)
    await session.flush()
    env_version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        definition_format="dockerfile",
        definition_content="FROM scratch",
        created_by_user_id=admin_user.id,
    )
    pipeline = CustomPipeline(
        organization_id=admin_user.organization_id,
        name="Legacy pipeline",
        pipeline_key="legacy-pipeline",
        created_by_user_id=admin_user.id,
    )
    session.add_all([env_version, pipeline])
    await session.flush()
    version = CustomPipelineVersion(
        custom_pipeline_id=pipeline.id,
        version_number=1,
        code_source_type="inline",
        entrypoint_command="run.sh",
        environment_version_id=env_version.id,
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.commit()
    await _clear(session, "custom_pipelines", pipeline.id, "created_at", "updated_at")
    await _clear(session, "custom_pipeline_versions", version.id, "created_at")

    listing = await client.get("/api/v1/custom-pipelines", headers=_auth(admin_token))
    assert listing.status_code == 200, listing.text
    [row] = [p for p in listing.json() if p["id"] == pipeline.id]
    assert row["created_at"] is None and row["updated_at"] is None

    detail = await client.get(f"/api/v1/custom-pipelines/{pipeline.id}", headers=_auth(admin_token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["created_at"] is None
    assert [v["created_at"] for v in detail.json()["versions"]] == [None]


@pytest.mark.asyncio
async def test_sequencing_batches_return_a_batch_and_manifest_entry_with_no_recorded_times(
    client, session, admin_user, admin_token
):
    batch = SequencingBatch(
        organization_id=admin_user.organization_id, code="LEGACY-SEQ", name="LEGACY-SEQ", status="pending"
    )
    session.add(batch)
    await session.flush()
    entry = ManifestEntry(sequencing_batch_id=batch.id, expected_filename="a.fastq.gz", expected_md5="0" * 32)
    session.add(entry)
    await session.commit()
    await _clear(session, "sequencing_batches", batch.id, "created_at", "updated_at")
    await _clear(session, "manifest_entries", entry.id, "created_at")

    listing = await client.get("/api/sequencing-batches", headers=_auth(admin_token))
    assert listing.status_code == 200, listing.text
    [row] = [b for b in listing.json() if b["id"] == batch.id]
    assert row["created_at"] is None and row["updated_at"] is None

    detail = await client.get(f"/api/sequencing-batches/{batch.id}", headers=_auth(admin_token))
    assert detail.status_code == 200, detail.text
    assert [e["created_at"] for e in detail.json()["manifest_entries"]] == [None]


@pytest.mark.asyncio
async def test_sample_batches_return_with_no_recorded_times_including_on_the_experiment(
    client, session, admin_user, admin_token
):
    exp = await _experiment(session, admin_user)
    batch = SampleBatch(experiment_id=exp.id, name="Legacy prep")
    session.add(batch)
    await session.commit()
    await _clear(session, "sample_batches", batch.id, "created_at", "updated_at")

    one = await client.get(f"/api/sample-batches/{batch.id}", headers=_auth(admin_token))
    assert one.status_code == 200, one.text
    assert one.json()["created_at"] is None and one.json()["updated_at"] is None

    experiment = await client.get(f"/api/experiments/{exp.id}", headers=_auth(admin_token))
    assert experiment.status_code == 200, experiment.text
    assert [b["created_at"] for b in experiment.json()["sample_batches"]] == [None]

    listed = await client.get(f"/api/experiments/{exp.id}/sample-batches", headers=_auth(admin_token))
    assert listed.status_code == 200, listed.text
    assert [b["created_at"] for b in listed.json()] == [None]


@pytest.mark.asyncio
async def test_auto_runs_return_a_config_and_pending_run_with_no_recorded_times(
    client, session, admin_user, admin_token
):
    exp = await _experiment(session, admin_user)
    sample = Sample(experiment_id=exp.id, external_id="S-1", status="registered")
    config = ExperimentAutoRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_key="nf-core/rnaseq",
        configured_by_user_id=admin_user.id,
    )
    session.add_all([sample, config])
    await session.flush()
    now = datetime.now(timezone.utc)
    pending = PendingAutoRun(
        organization_id=admin_user.organization_id,
        auto_run_config_id=config.id,
        experiment_id=exp.id,
        sample_id=sample.id,
        sample_completed_at=now,
        scheduled_at=now + timedelta(hours=1),
    )
    session.add(pending)
    await session.commit()
    await _clear(session, "experiment_auto_runs", config.id, "created_at", "updated_at")
    await _clear(session, "pending_auto_runs", pending.id, "created_at")

    configs = await client.get(f"/api/experiments/{exp.id}/auto-runs", headers=_auth(admin_token))
    assert configs.status_code == 200, configs.text
    assert [(c["created_at"], c["updated_at"]) for c in configs.json()] == [(None, None)]

    waiting = await client.get(f"/api/experiments/{exp.id}/auto-runs/{config.id}/pending", headers=_auth(admin_token))
    assert waiting.status_code == 200, waiting.text
    assert [p["created_at"] for p in waiting.json()] == [None]
