"""Where the deployed GKE cluster is.

``gcp_region`` is the installation default. A compute deploy can override it and then restore it,
so it never identifies the cluster. The cluster's own region or zone is ``gke_cluster_location``,
written from the compute Terraform output ``cluster_location``. Installs deployed before that output
existed discover it once: list the project's clusters across all locations, match the recorded
cluster name, and record the location. No match, or several, is unknown, never a guessed default.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Iterable

logger = logging.getLogger("bioaf.adapters.kubernetes.cluster_location")

LOCATION_KEY = "gke_cluster_location"

_GCP_REGION = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
_GCP_ZONE = re.compile(r"^[a-z]+-[a-z]+[0-9]+-[a-z]$")


class ClusterLocationUnknown(RuntimeError):
    """bioAF cannot tell where the deployed GKE cluster is."""


def is_gke_location(value: object) -> bool:
    return isinstance(value, str) and bool(_GCP_REGION.match(value) or _GCP_ZONE.match(value))


def is_gke_zone(value: str) -> bool:
    return bool(_GCP_ZONE.match(value))


def gke_cluster_path(project_id: str, location: str, cluster_name: str) -> str:
    return f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"


def _clean(value: object) -> str:
    text = (value or "").strip() if isinstance(value, str) else ""
    return "" if text == "null" else text


async def resolve_gke_cluster_location(
    cfg: dict,
    *,
    list_clusters: Callable[[str], Iterable],
    session_factory=None,
) -> str:
    """The deployed cluster's location: recorded, or discovered by name and then recorded.

    ``list_clusters(parent)`` is a blocking GKE ``list_clusters`` read returning objects with
    ``name`` and ``location``; it runs off the event loop. Provider failures propagate for the
    caller to map. ``cfg`` (the caller's cached config) is updated with a discovered location.
    """
    recorded = _clean(cfg.get(LOCATION_KEY))
    if is_gke_location(recorded):
        return recorded

    name = _clean(cfg.get("gke_cluster_name"))
    project = _clean(cfg.get("gcp_project_id"))
    if not name or not project:
        raise ClusterLocationUnknown("GKE cluster name or project is not recorded")

    clusters = await asyncio.to_thread(list_clusters, f"projects/{project}/locations/-")
    locations = {getattr(c, "location", "") for c in clusters or [] if getattr(c, "name", None) == name}
    if len(locations) != 1:
        logger.warning("GKE cluster location not discovered: %d location(s) match the recorded cluster", len(locations))
        raise ClusterLocationUnknown("the recorded GKE cluster was not found in exactly one location")
    location = locations.pop()
    if not is_gke_location(location):
        raise ClusterLocationUnknown("GKE reported an unrecognized cluster location")

    cfg[LOCATION_KEY] = location
    if session_factory is not None:
        from app.platform.platform_config_service import PlatformConfigService

        async with session_factory() as session:
            await PlatformConfigService.set(session, LOCATION_KEY, location)
            await session.commit()
    logger.info("Recorded the deployed GKE cluster location %s (discovered by cluster name)", location)
    return location
