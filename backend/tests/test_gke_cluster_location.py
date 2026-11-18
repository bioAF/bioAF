"""Where the deployed GKE cluster is: the recorded location, or discovered once and recorded.

gcp_region is the installation default, which a compute deploy can override and then restore, so it
never identifies the cluster. Installs deployed before gke_cluster_location existed discover it by
listing the project's clusters across all locations and matching the recorded cluster name. The
listing must be complete, bounded by the caller's time budget, and its result is saved only while
the recorded cluster is still the one that was looked up.
"""

from types import SimpleNamespace

import pytest
from google.api_core import exceptions as gexc
from sqlalchemy import text

from app.adapters.kubernetes.cluster_location import (
    ClusterLocationUnknown,
    classify_provider_failure,
    gke_cluster_path,
    resolve_gke_cluster_location,
)

CFG = {"gke_cluster_name": "bioaf-lab-abc123", "gcp_project_id": "lab-proj-1", "gcp_region": "us-central1"}


def _cluster(name, location):
    return SimpleNamespace(name=name, location=location)


class Lister:
    def __init__(self, clusters=(), error=None, missing_zones=()):
        self.clusters = list(clusters)
        self.error = error
        self.missing_zones = list(missing_zones)
        self.calls = []

    def __call__(self, parent, timeout):
        self.calls.append((parent, timeout))
        if self.error:
            raise self.error
        return SimpleNamespace(clusters=self.clusters, missing_zones=self.missing_zones)

    @property
    def parents(self):
        return [p for p, _ in self.calls]


@pytest.mark.asyncio
async def test_a_recorded_location_is_used_without_asking_gke():
    lister = Lister()
    cfg = {**CFG, "gke_cluster_location": "europe-west1"}
    assert await resolve_gke_cluster_location(cfg, list_clusters=lister) == "europe-west1"
    assert lister.calls == []


@pytest.mark.parametrize("recorded", [None, "", "null", "Europe West", "us-central1; rm"])
@pytest.mark.asyncio
async def test_a_missing_or_malformed_location_is_discovered_by_cluster_name(recorded):
    lister = Lister([_cluster("bioaf-other-000001", "us-central1"), _cluster("bioaf-lab-abc123", "europe-west1-b")])
    cfg = {**CFG, "gke_cluster_location": recorded}

    assert await resolve_gke_cluster_location(cfg, list_clusters=lister) == "europe-west1-b"
    assert lister.parents == ["projects/lab-proj-1/locations/-"]
    assert cfg["gke_cluster_location"] == "europe-west1-b"


@pytest.mark.asyncio
async def test_the_listing_gets_the_callers_time_budget():
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")])
    await resolve_gke_cluster_location({**CFG}, list_clusters=lister, timeout=3.5)
    assert lister.calls == [("projects/lab-proj-1/locations/-", 3.5)]


@pytest.mark.parametrize(
    "clusters",
    [
        [],
        [_cluster("bioaf-other-000001", "us-central1")],
        [_cluster("bioaf-lab-abc123", "us-central1"), _cluster("bioaf-lab-abc123", "europe-west1")],
        [_cluster("bioaf-lab-abc123", "")],
    ],
)
@pytest.mark.asyncio
async def test_no_single_valid_match_is_unknown_never_the_default_region(clusters):
    with pytest.raises(ClusterLocationUnknown):
        await resolve_gke_cluster_location({**CFG}, list_clusters=Lister(clusters))


@pytest.mark.asyncio
async def test_an_incomplete_listing_is_not_proof_of_a_unique_match():
    """GKE reports zones it could not list in missing_zones; one visible match is then not unique."""
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")], missing_zones=["asia-east1-a"])
    cfg = {**CFG}
    with pytest.raises(ClusterLocationUnknown):
        await resolve_gke_cluster_location(cfg, list_clusters=lister)
    assert "gke_cluster_location" not in cfg


@pytest.mark.parametrize("missing", ["gke_cluster_name", "gcp_project_id"])
@pytest.mark.asyncio
async def test_without_a_cluster_identity_nothing_is_asked(missing):
    lister = Lister()
    cfg = {k: v for k, v in CFG.items() if k != missing}
    with pytest.raises(ClusterLocationUnknown):
        await resolve_gke_cluster_location(cfg, list_clusters=lister)
    assert lister.calls == []


@pytest.mark.asyncio
async def test_a_provider_failure_is_raised_as_is_for_the_caller_to_map():
    boom = gexc.ServiceUnavailable("down")
    with pytest.raises(gexc.ServiceUnavailable) as exc:
        await resolve_gke_cluster_location({**CFG}, list_clusters=Lister(error=boom))
    assert exc.value is boom


@pytest.mark.parametrize(
    "exc,kind",
    [
        (gexc.PermissionDenied("no"), "denied"),
        (gexc.Forbidden("no"), "denied"),
        (gexc.Unauthenticated("no"), "denied"),
        (gexc.DeadlineExceeded("slow"), "timeout"),
        (TimeoutError(), "timeout"),
        (gexc.ServiceUnavailable("down"), "unavailable"),
        (RuntimeError("other"), "unavailable"),
    ],
)
def test_provider_failures_are_classified(exc, kind):
    assert classify_provider_failure(exc) == kind


async def _seed(factory, **values):
    async with factory() as s:
        for key, value in values.items():
            await s.execute(
                text(
                    "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"k": key, "v": value},
            )
        await s.commit()


async def _stored(factory, key):
    async with factory() as s:
        return (await s.execute(text("SELECT value FROM platform_config WHERE key = :k"), {"k": key})).scalar()


@pytest.fixture
def factory(db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_a_discovered_location_is_recorded(factory):
    await _seed(factory, gke_cluster_name="bioaf-lab-abc123", gcp_project_id="lab-proj-1")
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")])

    await resolve_gke_cluster_location({**CFG}, list_clusters=lister, session_factory=factory)

    assert await _stored(factory, "gke_cluster_location") == "europe-west1"


@pytest.mark.asyncio
async def test_discovery_never_overwrites_a_cluster_that_replaced_it_meanwhile(factory):
    """Discovery for cluster A finishes after the configuration switched to cluster B."""
    await _seed(
        factory, gke_cluster_name="bioaf-lab-new999", gcp_project_id="lab-proj-1", gke_cluster_location="asia-east1"
    )
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")])
    cfg = {**CFG}  # the caller's snapshot still names cluster A

    with pytest.raises(ClusterLocationUnknown):
        await resolve_gke_cluster_location(cfg, list_clusters=lister, session_factory=factory)

    assert await _stored(factory, "gke_cluster_location") == "asia-east1"
    assert "gke_cluster_location" not in cfg


@pytest.mark.asyncio
async def test_a_location_recorded_meanwhile_for_the_same_cluster_wins(factory):
    await _seed(
        factory, gke_cluster_name="bioaf-lab-abc123", gcp_project_id="lab-proj-1", gke_cluster_location="us-west1"
    )
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")])
    cfg = {**CFG}

    assert await resolve_gke_cluster_location(cfg, list_clusters=lister, session_factory=factory) == "us-west1"
    assert await _stored(factory, "gke_cluster_location") == "us-west1"
    assert cfg["gke_cluster_location"] == "us-west1"


def test_cluster_path():
    assert gke_cluster_path("p", "europe-west1", "c") == "projects/p/locations/europe-west1/clusters/c"


@pytest.mark.asyncio
async def test_the_project_cannot_change_between_the_identity_check_and_the_save(factory, monkeypatch):
    """Codex repro: the project row was read unlocked, changed by another transaction, then saved over."""
    import asyncio

    from sqlalchemy.exc import DBAPIError

    from app.platform.platform_config_service import PlatformConfigService

    await _seed(factory, gke_cluster_name="bioaf-lab-abc123", gcp_project_id="lab-proj-1")
    checked, resume = asyncio.Event(), asyncio.Event()
    original = PlatformConfigService.get_many

    async def get_many_then_pause(session, keys):
        rows = await original(session, keys)
        checked.set()
        await resume.wait()
        return rows

    monkeypatch.setattr(PlatformConfigService, "get_many", staticmethod(get_many_then_pause))
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")])
    discovery = asyncio.create_task(
        resolve_gke_cluster_location({**CFG}, list_clusters=lister, session_factory=factory)
    )
    await asyncio.wait_for(checked.wait(), timeout=5)

    async with factory() as other:
        await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with pytest.raises(DBAPIError):
            await other.execute(text("UPDATE platform_config SET value = 'lab-proj-2' WHERE key = 'gcp_project_id'"))
        await other.rollback()

    resume.set()
    assert await discovery == "europe-west1"
    assert await _stored(factory, "gcp_project_id") == "lab-proj-1"
