"""Orphaned clusters are recorded at the location the cluster is actually in.

Recovery and cleanup probe an orphan at its recorded location. gcp_region is the installation
default, which a compute deploy overrides and then restores, so neither a failed deploy nor a failed
teardown may record it as the cluster's location.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.adapters.kubernetes.cluster_location import ClusterLocationUnknown
from tests.test_deployment_characterization import _deploy, _event, _seed_gcp_ready, _seed_user
from tests.test_stack_deployment import _make_progress_event, _seed_org_and_user, _set_config


async def _orphan_location(session) -> str:
    row = (
        await session.execute(
            text("SELECT gcp_zone FROM orphaned_resources WHERE resource_type = 'gke_cluster' ORDER BY id LIMIT 1")
        )
    ).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_a_failed_deploy_records_the_attempted_location_not_the_restored_default(session, db_engine):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session, storage_deployed="true", deploy_suffix="abc123", org_slug="demo")

    await _deploy(
        session,
        user_id,
        {"compute": [_event("apply_error", message="quota exceeded")]},
        compute_region="europe-west1",
    )
    await session.commit()

    assert await _orphan_location(session) == "europe-west1"
    region = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'gcp_region'"))).scalar()
    assert region == "us-central1"


async def _failed_teardown(session, **config):
    from app.services.stack_deployment import teardown_stack

    _, user_id = await _seed_org_and_user(session)
    for key, value in {
        "compute_deployed": "true",
        "compute_stack": "kubernetes",
        "gke_cluster_name": "bioaf-demo-abc123",
        "org_slug": "demo",
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1",
        **config,
    }.items():
        await _set_config(session, key, value)
    await session.commit()

    async def mock_destroy(sess, uid, module_name):
        yield _make_progress_event("apply_error", "destroy failed")

    with patch("app.services.stack_deployment._run_destroy", side_effect=mock_destroy):
        [e async for e in teardown_stack(session, user_id=user_id)]


@pytest.mark.asyncio
async def test_a_failed_teardown_records_the_deployed_location(session):
    await _failed_teardown(session, gke_cluster_location="europe-west1")
    assert await _orphan_location(session) == "europe-west1"


def _adapter_locating(result):
    async def deployed_cluster_location():
        if isinstance(result, Exception):
            raise result
        return result

    return SimpleNamespace(deployed_cluster_location=deployed_cluster_location)


@pytest.mark.asyncio
async def test_a_failed_teardown_without_a_recorded_location_discovers_it(session, monkeypatch):
    from app.adapters import registry

    monkeypatch.setattr(registry, "get_compute_adapter", lambda: _adapter_locating("europe-west1-b"))
    await _failed_teardown(session)
    assert await _orphan_location(session) == "europe-west1-b"


@pytest.mark.parametrize("failure", [ClusterLocationUnknown("not found"), RuntimeError("403")])
@pytest.mark.asyncio
async def test_an_undiscoverable_location_still_records_the_orphan(session, monkeypatch, failure):
    """The orphan must still be recorded; the installation default is the only remaining hint."""
    from app.adapters import registry

    monkeypatch.setattr(registry, "get_compute_adapter", lambda: _adapter_locating(failure))
    await _failed_teardown(session)
    assert await _orphan_location(session) == "us-central1"
