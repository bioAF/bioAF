"""Where the deployed GKE cluster is: the recorded location, or discovered once and recorded.

gcp_region is the installation default, which a compute deploy can override and then restore, so it
never identifies the cluster. Installs deployed before gke_cluster_location existed discover it by
listing the project's clusters across all locations and matching the recorded cluster name.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.adapters.kubernetes.cluster_location import (
    ClusterLocationUnknown,
    gke_cluster_path,
    resolve_gke_cluster_location,
)

CFG = {"gke_cluster_name": "bioaf-lab-abc123", "gcp_project_id": "lab-proj-1", "gcp_region": "us-central1"}


def _cluster(name, location):
    return SimpleNamespace(name=name, location=location)


class Lister:
    def __init__(self, clusters=(), error=None):
        self.clusters = list(clusters)
        self.error = error
        self.parents = []

    def __call__(self, parent):
        self.parents.append(parent)
        if self.error:
            raise self.error
        return self.clusters


@pytest.mark.asyncio
async def test_a_recorded_location_is_used_without_asking_gke():
    lister = Lister()
    cfg = {**CFG, "gke_cluster_location": "europe-west1"}
    assert await resolve_gke_cluster_location(cfg, list_clusters=lister) == "europe-west1"
    assert lister.parents == []


@pytest.mark.parametrize("recorded", [None, "", "null", "Europe West", "us-central1; rm"])
@pytest.mark.asyncio
async def test_a_missing_or_malformed_location_is_discovered_by_cluster_name(recorded):
    lister = Lister([_cluster("bioaf-other-000001", "us-central1"), _cluster("bioaf-lab-abc123", "europe-west1-b")])
    cfg = {**CFG, "gke_cluster_location": recorded}

    assert await resolve_gke_cluster_location(cfg, list_clusters=lister) == "europe-west1-b"
    assert lister.parents == ["projects/lab-proj-1/locations/-"]
    # The cached config now carries it, so the caller's later reads agree.
    assert cfg["gke_cluster_location"] == "europe-west1-b"


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


@pytest.mark.parametrize("missing", ["gke_cluster_name", "gcp_project_id"])
@pytest.mark.asyncio
async def test_without_a_cluster_identity_nothing_is_asked(missing):
    lister = Lister()
    cfg = {k: v for k, v in CFG.items() if k != missing}
    with pytest.raises(ClusterLocationUnknown):
        await resolve_gke_cluster_location(cfg, list_clusters=lister)
    assert lister.parents == []


@pytest.mark.asyncio
async def test_a_provider_failure_is_raised_as_is_for_the_caller_to_map():
    boom = RuntimeError("permission denied")
    with pytest.raises(RuntimeError) as exc:
        await resolve_gke_cluster_location({**CFG}, list_clusters=Lister(error=boom))
    assert exc.value is boom


@pytest.mark.asyncio
async def test_a_discovered_location_is_recorded(db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    lister = Lister([_cluster("bioaf-lab-abc123", "europe-west1")])

    await resolve_gke_cluster_location({**CFG}, list_clusters=lister, session_factory=factory)

    async with factory() as s:
        row = (await s.execute(text("SELECT value FROM platform_config WHERE key = 'gke_cluster_location'"))).first()
    assert row is not None and row[0] == "europe-west1"


def test_cluster_path():
    assert gke_cluster_path("p", "europe-west1", "c") == "projects/p/locations/europe-west1/clusters/c"
