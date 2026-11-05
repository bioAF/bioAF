"""A run's ``pipeline_key`` is the key of the pipeline it ran, and the run API says so.

Seen live on blackbird: the run API returned the display name as ``pipeline_key``, the
``?pipeline_key=`` filter matched display names, and custom runs stored no key at all, so the
custom pipelines list showed "Never run" for any pipeline whose name and key differ. The response
also left out ``project_id``. A run stored before keys were kept is still found by its name.
"""

# ruff: noqa: F811  (fixtures imported from test_custom_pipeline_launch are used as parameters)

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.pipeline_run import PipelineRun
from app.schemas.custom_pipeline import CustomPipelineLaunchRequest
from app.services.custom_pipeline_service import CustomPipelineService
from tests.test_custom_pipeline_launch import (  # noqa: F401  (fixtures)
    _create_pipeline_with_version,
    _mock_compute_adapter,
    _platform_config,
    experiment,
    input_files,
    project,
    ready_env_version,
)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _custom_run(session, admin_user, ready_env_version, project, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _captured = _mock_compute_adapter()
    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                project_id=project.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()
    return pipeline, run


@pytest.mark.asyncio
async def test_a_custom_launch_records_its_pipeline_key(
    session, admin_user, ready_env_version, project, experiment, input_files
):
    pipeline, run = await _custom_run(session, admin_user, ready_env_version, project, experiment, input_files)

    assert pipeline.name != pipeline.pipeline_key
    assert run.pipeline_key == pipeline.pipeline_key


@pytest.mark.asyncio
async def test_the_run_api_returns_the_key_and_project_and_filters_by_key(
    client, session, admin_user, admin_token, ready_env_version, project, experiment, input_files
):
    pipeline, run = await _custom_run(session, admin_user, ready_env_version, project, experiment, input_files)

    detail = await client.get(f"/api/pipeline-runs/{run.id}", headers=_bearer(admin_token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["pipeline_key"] == pipeline.pipeline_key
    assert detail.json()["pipeline_name"] == pipeline.name
    assert detail.json()["project_id"] == project.id

    listed = await client.get(f"/api/pipeline-runs?pipeline_key={pipeline.pipeline_key}", headers=_bearer(admin_token))
    assert listed.status_code == 200, listed.text
    assert [r["id"] for r in listed.json()["runs"]] == [run.id]

    by_name = await client.get(f"/api/pipeline-runs?pipeline_key={pipeline.name}", headers=_bearer(admin_token))
    assert by_name.json()["runs"] == []


@pytest.mark.asyncio
async def test_a_run_stored_without_a_key_is_answered_and_found_by_its_name(client, session, admin_user, admin_token):
    legacy = PipelineRun(
        organization_id=admin_user.organization_id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_key=None,
        status="completed",
    )
    keyed = PipelineRun(
        organization_id=admin_user.organization_id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_key="something-else",
        status="completed",
    )
    session.add_all([legacy, keyed])
    await session.commit()

    detail = await client.get(f"/api/pipeline-runs/{legacy.id}", headers=_bearer(admin_token))
    assert detail.json()["pipeline_key"] == "nf-core/rnaseq"

    listed = await client.get("/api/pipeline-runs?pipeline_key=nf-core/rnaseq", headers=_bearer(admin_token))
    assert [r["id"] for r in listed.json()["runs"]] == [legacy.id]


_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "131_custom_run_keys_and_resources.py"


def _upgrade(sync_conn):
    spec = importlib.util.spec_from_file_location("migration_131", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


@pytest.mark.asyncio
async def test_migration_131_gives_custom_runs_their_key_and_leaves_catalog_rows_alone(
    session, admin_user, ready_env_version, project, experiment, input_files
):
    pipeline, run = await _custom_run(session, admin_user, ready_env_version, project, experiment, input_files)
    run.pipeline_key = None
    catalog_legacy = PipelineRun(
        organization_id=admin_user.organization_id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_key=None,
        status="completed",
    )
    session.add(catalog_legacy)
    await session.commit()
    run_id, legacy_id = run.id, catalog_legacy.id

    conn = await session.connection()
    await conn.run_sync(_upgrade)
    await conn.run_sync(_upgrade)

    keys = dict(
        (
            await session.execute(
                text("SELECT id, pipeline_key FROM pipeline_runs WHERE id IN (:a, :b)").bindparams(
                    a=run_id, b=legacy_id
                )
            )
        ).all()
    )
    assert keys == {run_id: pipeline.pipeline_key, legacy_id: None}
