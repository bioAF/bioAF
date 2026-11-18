"""Where the deployed GKE cluster is.

``gcp_region`` is the installation default. A compute deploy can override it and then restore it,
so it never identifies the cluster. The cluster's own region or zone is ``gke_cluster_location``,
written from the compute Terraform output ``cluster_location``. Installs deployed before that output
existed discover it once: list the project's clusters across all locations, match the recorded
cluster name, and record the location.

Discovery only answers when it is sure: the listing must be complete (GKE's ``missing_zones`` is
empty), exactly one cluster must match, and the result is saved only while the recorded cluster is
still the one that was looked up. Anything else is unknown, never a guessed default.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Union

logger = logging.getLogger("bioaf.adapters.kubernetes.cluster_location")

LOCATION_KEY = "gke_cluster_location"
# The GKE client's own default is 20 s with retries; discovery passes an explicit budget instead.
DISCOVERY_TIMEOUT_SECONDS = 15.0

_GCP_REGION = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
_GCP_ZONE = re.compile(r"^[a-z]+-[a-z]+[0-9]+-[a-z]$")

# ``list_clusters(parent, timeout)``: a blocking GKE list returning an object with ``clusters``
# (each with ``name`` and ``location``) and ``missing_zones``.
ClusterLister = Callable[[str, float], object]
# Seconds for the listing call: a fixed number, or a callable read just before the call starts so
# time spent earlier (database reads) is not handed to the SDK twice. The callable may raise when
# nothing is left.
Timeout = Union[float, Callable[[], float]]


class ClusterLocationUnknown(RuntimeError):
    """bioAF cannot tell where the deployed GKE cluster is."""


class ClusterNotListed(ClusterLocationUnknown):
    """A complete listing of the project's clusters does not include it: verified absent."""


def is_gke_location(value: object) -> bool:
    return isinstance(value, str) and bool(_GCP_REGION.match(value) or _GCP_ZONE.match(value))


def is_gke_zone(value: str) -> bool:
    return bool(_GCP_ZONE.match(value))


def gke_cluster_path(project_id: str, location: str, cluster_name: str) -> str:
    return f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"


def classify_provider_failure(exc: BaseException) -> str:
    """``denied`` (bioAF's cloud access), ``timeout``, or ``unavailable`` (try again)."""
    from google.api_core import exceptions as gexc

    if isinstance(exc, (gexc.PermissionDenied, gexc.Forbidden, gexc.Unauthenticated, gexc.Unauthorized)):
        return "denied"
    if isinstance(exc, (gexc.DeadlineExceeded, TimeoutError)):
        return "timeout"
    return "unavailable"


def _clean(value: object) -> str:
    text_value = (value or "").strip() if isinstance(value, str) else ""
    return "" if text_value == "null" else text_value


async def find_gke_cluster_location(
    project_id: str, cluster_name: str, *, list_clusters: ClusterLister, timeout: Timeout = DISCOVERY_TIMEOUT_SECONDS
) -> str:
    """The one location holding ``cluster_name`` in ``project_id``, from a complete listing.

    Provider failures propagate for the caller to map (see ``classify_provider_failure``).
    """
    parent = f"projects/{project_id}/locations/-"

    def call():
        return list_clusters(parent, timeout() if callable(timeout) else timeout)

    response = await asyncio.to_thread(call)
    if list(getattr(response, "missing_zones", None) or []):
        logger.warning("GKE cluster location not discovered: the cluster listing was incomplete")
        raise ClusterLocationUnknown("GKE could not list every location, so the cluster's location is not certain")
    locations = {
        getattr(c, "location", "") for c in getattr(response, "clusters", None) or [] if c.name == cluster_name
    }
    if not locations:
        logger.warning("GKE cluster location not discovered: no cluster of that name in a complete listing")
        raise ClusterNotListed("the GKE cluster is not in the project's complete cluster listing")
    if len(locations) != 1:
        logger.warning("GKE cluster location not discovered: %d location(s) match the cluster", len(locations))
        raise ClusterLocationUnknown("the GKE cluster was not found in exactly one location")
    location = locations.pop()
    if not is_gke_location(location):
        raise ClusterLocationUnknown("GKE reported an unrecognized cluster location")
    return location


async def _record(session_factory, name: str, project: str, discovered: str) -> str:
    """Save ``discovered`` only while ``name``/``project`` are still the recorded cluster.

    Both identity rows (project and cluster name) are locked, in key order, before they are
    compared, so a change to either one either commits before this reads it (and discovery is
    refused) or waits until this result is saved. Returns the location now recorded for that cluster.
    """
    from app.platform.platform_config_service import PlatformConfigService

    async with session_factory() as session:
        locked_project = await PlatformConfigService.get_for_update(session, "gcp_project_id")
        locked_name = await PlatformConfigService.get_for_update(session, "gke_cluster_name")
        rows = await PlatformConfigService.get_many(session, [LOCATION_KEY])
        if _clean(locked_name) != name or _clean(locked_project) != project:
            await session.rollback()
            logger.warning("Discovered GKE location discarded: the recorded cluster changed during discovery")
            raise ClusterLocationUnknown("the recorded cluster changed while its location was being discovered")
        current = _clean(rows.get(LOCATION_KEY))
        if is_gke_location(current):
            await session.rollback()
            return current
        await PlatformConfigService.set(session, LOCATION_KEY, discovered)
        await session.commit()
    logger.info("Recorded the deployed GKE cluster location %s (discovered by cluster name)", discovered)
    return discovered


async def resolve_gke_cluster_location(
    cfg: dict,
    *,
    list_clusters: ClusterLister,
    session_factory=None,
    timeout: Timeout = DISCOVERY_TIMEOUT_SECONDS,
) -> str:
    """The deployed cluster's location: recorded, or discovered by name and then recorded.

    ``cfg`` is the caller's cached config snapshot; it is updated only with a location that
    belongs to the cluster it names.
    """
    recorded = _clean(cfg.get(LOCATION_KEY))
    if is_gke_location(recorded):
        return recorded

    name = _clean(cfg.get("gke_cluster_name"))
    project = _clean(cfg.get("gcp_project_id"))
    if not name or not project:
        raise ClusterLocationUnknown("GKE cluster name or project is not recorded")

    location = await find_gke_cluster_location(project, name, list_clusters=list_clusters, timeout=timeout)
    if session_factory is not None:
        location = await _record(session_factory, name, project, location)
    cfg[LOCATION_KEY] = location
    return location
