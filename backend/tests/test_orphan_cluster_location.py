"""Orphaned clusters are recorded at the location the cluster is actually in.

Recovery and cleanup probe an orphan at its recorded location. gcp_region is the installation
default, which a compute deploy overrides and then restores, so neither a failed deploy nor a failed
teardown may record it as the cluster's location.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from google.api_core import exceptions as gexc

from app.adapters.kubernetes.cluster_location import ClusterLocationUnknown, ClusterNotListed
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
async def test_an_undiscoverable_location_is_recorded_as_unknown_not_guessed(session, monkeypatch, failure):
    """The orphan is still recorded, with no location: a guessed region would read as 'already gone'."""
    from app.adapters import registry

    monkeypatch.setattr(registry, "get_compute_adapter", lambda: _adapter_locating(failure))
    await _failed_teardown(session)
    assert await _orphan_location(session) is None


class FakeGke:
    """Compute adapter double: where clusters really are, and what was asked of it."""

    def __init__(
        self, clusters: dict[str, str], *, locate_error: Exception | None = None, probe_state: str | None = None
    ):
        self.clusters = clusters  # name -> location
        self.locate_error = locate_error
        self.probe_state = probe_state  # e.g. "UNVERIFIED": the provider refused or failed the probe
        self.probed: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    async def locate_cluster(self, project_id, cluster_name):
        if self.locate_error:
            raise self.locate_error
        if cluster_name not in self.clusters:
            raise ClusterNotListed("not in a complete listing")
        return self.clusters[cluster_name]

    async def probe_cluster(self, project_id, location, cluster_name):
        from app.adapters.models import ClusterProbe

        self.probed.append((location, cluster_name))
        if self.probe_state:
            return ClusterProbe(state=self.probe_state)
        if self.clusters.get(cluster_name) != location:
            return ClusterProbe(state="NOT_FOUND")
        return ClusterProbe(state="RUNNING", endpoint="10.0.0.9", ca_cert="ca", location=location)

    async def delete_cluster(self, project_id, location, cluster_name):
        self.deleted.append((location, cluster_name))


async def _unknown_location_orphan(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-eu0001",
        gcp_project_id="test-project",
        stack_uid="eu0001",
        gcp_zone=None,
    )
    await session.flush()
    return resource


@pytest.mark.asyncio
async def test_an_orphan_without_a_location_is_located_before_it_is_probed(session, monkeypatch):
    from app.adapters import registry
    from app.services.orphaned_resource_service import OrphanedResourceService

    gke = FakeGke({"bioaf-demo-eu0001": "europe-west1"})
    monkeypatch.setattr(registry, "get_compute_adapter", lambda: gke)
    resource = await _unknown_location_orphan(session)

    report = await OrphanedResourceService.recovery_check(session)

    assert [e["id"] for e in report["recoverable"]] == [resource.id]
    assert gke.probed == [("europe-west1", "bioaf-demo-eu0001")]
    assert resource.gcp_zone == "europe-west1"


@pytest.mark.parametrize("locate_error", [ClusterLocationUnknown("listing incomplete"), RuntimeError("403 denied")])
@pytest.mark.asyncio
async def test_an_orphan_that_cannot_be_located_is_never_treated_as_gone(session, monkeypatch, locate_error):
    """Codex repro: cleanup probed a guessed region, got NOT_FOUND, and marked a live cluster cleaned."""
    from app.adapters import registry
    from app.services.orphaned_resource_service import OrphanedResourceService

    gke = FakeGke({}, locate_error=locate_error)
    monkeypatch.setattr(registry, "get_compute_adapter", lambda: gke)
    resource = await _unknown_location_orphan(session)

    report = await OrphanedResourceService.recovery_check(session)
    assert report["dead"] == []
    assert [e["id"] for e in report["unverified"]] == [resource.id]

    counts = await OrphanedResourceService.cleanup_dead_orphans(session, user_id=1)
    assert counts["cleaned"] == 0 and counts["skipped"] == 1
    assert resource.status == "detected"
    assert gke.probed == [] and gke.deleted == []

    result = await OrphanedResourceService.cleanup_resource(session, resource.id, user_id=1)
    assert result.status == "failed"
    assert "location" in (result.error_message or "")
    assert gke.deleted == []


async def _orphan_at(session, zone):
    from app.services.orphaned_resource_service import OrphanedResourceService

    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-eu0001",
        gcp_project_id="test-project",
        stack_uid="eu0001",
        gcp_zone=zone,
    )
    await session.flush()
    return resource


async def _check_then_cleanup(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    report = await OrphanedResourceService.recovery_check(session)
    user_id = await _seed_user(session)
    counts = await OrphanedResourceService.cleanup_dead_orphans(session, user_id=user_id)
    return report, counts


@pytest.mark.asyncio
async def test_a_refused_probe_is_unverified_never_gone(session, monkeypatch):
    """Codex repro: a permission denial probed as NOT_FOUND, so cleanup marked a live cluster cleaned."""
    from app.adapters import registry

    gke = FakeGke({"bioaf-demo-eu0001": "europe-west1"}, probe_state="UNVERIFIED")
    monkeypatch.setattr(registry, "get_compute_adapter", lambda: gke)
    resource = await _orphan_at(session, "europe-west1")

    report, counts = await _check_then_cleanup(session)

    assert report["dead"] == [] and [e["id"] for e in report["unverified"]] == [resource.id]
    assert counts == {"cleaned": 0, "skipped": 1, "failed": 0}
    assert resource.status == "detected" and gke.deleted == []


@pytest.mark.asyncio
async def test_a_stale_recorded_region_is_corrected_not_read_as_gone(session, monkeypatch):
    """An older orphan recorded at a guessed region: NOT_FOUND there is not proof the cluster is gone."""
    from app.adapters import registry

    gke = FakeGke({"bioaf-demo-eu0001": "europe-west1"})
    monkeypatch.setattr(registry, "get_compute_adapter", lambda: gke)
    resource = await _orphan_at(session, "us-central1")

    report, counts = await _check_then_cleanup(session)

    assert [e["id"] for e in report["recoverable"]] == [resource.id]
    assert resource.gcp_zone == "europe-west1"
    assert counts["cleaned"] == 0 and resource.status == "detected"


@pytest.mark.parametrize("zone", ["us-central1", None])
@pytest.mark.asyncio
async def test_a_cluster_absent_from_a_complete_listing_is_gone(session, monkeypatch, zone):
    from app.adapters import registry

    gke = FakeGke({})
    monkeypatch.setattr(registry, "get_compute_adapter", lambda: gke)
    resource = await _orphan_at(session, zone)

    report, counts = await _check_then_cleanup(session)

    assert [e["id"] for e in report["dead"]] == [resource.id]
    assert counts["cleaned"] == 1 and resource.status == "cleaned"
    assert gke.deleted == []


@pytest.mark.parametrize("locate_error", [ClusterLocationUnknown("listing incomplete"), RuntimeError("503")])
@pytest.mark.asyncio
async def test_not_found_at_the_recorded_region_without_a_confirming_listing_is_unverified(
    session, monkeypatch, locate_error
):
    from app.adapters import registry

    gke = FakeGke({}, locate_error=locate_error)
    monkeypatch.setattr(registry, "get_compute_adapter", lambda: gke)
    resource = await _orphan_at(session, "us-central1")

    report, counts = await _check_then_cleanup(session)

    assert report["dead"] == [] and [e["id"] for e in report["unverified"]] == [resource.id]
    assert counts["cleaned"] == 0 and resource.status == "detected"


def _probe_adapter(monkeypatch, error):
    from unittest.mock import MagicMock

    from app.adapters.compute.kubernetes import KubernetesComputeProvider
    from app.settings_contract import ComputeMode

    adapter = KubernetesComputeProvider(mode=ComputeMode.KUBERNETES)
    client = MagicMock()
    client.get_cluster.side_effect = error
    monkeypatch.setattr(adapter, "_get_gke_client", lambda: client)
    return adapter


@pytest.mark.parametrize(
    "error,state",
    [
        (gexc.NotFound("gone"), "NOT_FOUND"),
        (gexc.PermissionDenied("denied"), "UNVERIFIED"),
        (gexc.ServiceUnavailable("down"), "UNVERIFIED"),
        (RuntimeError("boom"), "UNVERIFIED"),
    ],
)
@pytest.mark.asyncio
async def test_only_a_provider_not_found_probes_as_not_found(monkeypatch, error, state):
    probe = await _probe_adapter(monkeypatch, error).probe_cluster("p", "europe-west1", "c")
    assert probe.state == state
    assert probe.endpoint is None
