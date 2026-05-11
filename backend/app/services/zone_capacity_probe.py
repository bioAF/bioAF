"""Pre-flight GCE capacity probe.

GKE Standard clusters provision a throwaway default node pool at create
time with ``initial_node_count = 1`` per zone (for regional clusters).
That pool has no autoscaling and no ``location_policy``, so if any one
zone is out of capacity for the requested machine type, the per-zone IGM
hangs for ~70 minutes before GKE gives up. Terraform times out at 40 min
and the cluster is stuck in ``RUNNING_WITH_ERROR``.

Random suffixes on cluster names defend against name collisions; they do
nothing against per-zone capacity stockouts. This module checks capacity
before the cluster create so we can pin the default pool's
``node_locations`` to a single zone that we have just observed to have
capacity.

The probe attempts a real instance insert in each candidate zone in
order. The first zone whose insert succeeds wins. If the insert returns
``ZONE_RESOURCE_POOL_EXHAUSTED`` or ``GCE_STOCKOUT``, we move on. The
probe instance is deleted before we return.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("bioaf.zone_capacity_probe")

# Compute Engine error codes that indicate "no capacity in this zone for
# this machine type right now." Anything else (quota, permission, bad
# image, etc.) is not a capacity issue and should not silently roll over.
_STOCKOUT_CODES = frozenset(
    {
        "ZONE_RESOURCE_POOL_EXHAUSTED",
        "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
        "GCE_STOCKOUT",
    }
)

# Image used for the throwaway probe instance. Any small public image
# works; debian-12 is consistently available across all regions and the
# instance is deleted immediately, so disk content does not matter.
_PROBE_SOURCE_IMAGE = "projects/debian-cloud/global/images/family/debian-12"

# How long to wait for the insert operation to reach a terminal state.
# Stockout errors surface within a few seconds; a successful insert
# takes 10-30s. 60s is generous without being painful.
_OPERATION_TIMEOUT_SECONDS = 60


class AllZonesExhaustedError(RuntimeError):
    """Raised when every candidate zone returned a stockout error."""


def _build_instances_client(credentials: Any):
    """Construct a real InstancesClient. Patched in tests."""
    from google.cloud import compute_v1

    return compute_v1.InstancesClient(credentials=credentials)


def _build_instance_resource(name: str, machine_type: str, zone: str) -> Any:
    """Build the minimal Instance payload for a probe insert."""
    from google.cloud import compute_v1

    return compute_v1.Instance(
        name=name,
        machine_type=f"zones/{zone}/machineTypes/{machine_type}",
        disks=[
            compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image=_PROBE_SOURCE_IMAGE,
                    disk_size_gb=10,
                    disk_type=f"zones/{zone}/diskTypes/pd-standard",
                ),
            )
        ],
        network_interfaces=[
            compute_v1.NetworkInterface(
                network="global/networks/default",
            )
        ],
    )


def _operation_outcome(op: Any) -> tuple[str | None, str | None]:
    """Return (error_code, error_message) after waiting for the op.

    Wraps the polling so the caller does not have to deal with the two
    different ways the ExtendedOperation API surfaces errors (raising
    from ``result()`` and exposing ``error_code()`` after).
    """
    try:
        op.result(timeout=_OPERATION_TIMEOUT_SECONDS)
    except Exception:
        # The exception is informative but the structured error_code is
        # what we branch on, so swallow and read the fields below.
        pass
    code = op.error_code() if hasattr(op, "error_code") else None
    message = op.error_message() if hasattr(op, "error_message") else None
    return code, message


def probe_zones(
    *,
    zones: list[str],
    project_id: str,
    credentials: Any,
    machine_type: str = "e2-medium",
) -> str:
    """Return the first zone in ``zones`` that has capacity for ``machine_type``.

    Args:
        zones: Candidate zones in preference order.
        project_id: GCP project to probe in.
        credentials: google-auth Credentials with compute.instances.* perms.
        machine_type: Machine type to probe for. Defaults to e2-medium,
            which matches the GKE default node pool's implicit choice.

    Returns:
        The zone name that just accepted an instance insert.

    Raises:
        AllZonesExhaustedError: every zone returned a stockout code.
        RuntimeError: a zone returned a non-stockout error (quota,
            permission, etc.). The caller almost certainly wants to see
            this rather than treat it as "out of capacity."
        ValueError: ``zones`` was empty.
    """
    if not zones:
        raise ValueError("probe_zones requires at least one candidate zone")

    client = _build_instances_client(credentials)
    exhausted: list[str] = []

    for zone in zones:
        probe_name = f"bioaf-capacity-probe-{uuid.uuid4().hex[:8]}"
        logger.info(
            "Probing capacity in zone=%s machine_type=%s project=%s",
            zone,
            machine_type,
            project_id,
        )
        instance = _build_instance_resource(probe_name, machine_type, zone)
        insert_op = client.insert(
            project=project_id,
            zone=zone,
            instance_resource=instance,
        )
        code, message = _operation_outcome(insert_op)

        if code in _STOCKOUT_CODES:
            logger.info("Zone %s exhausted (code=%s); trying next.", zone, code)
            exhausted.append(zone)
            continue

        if code:
            # Unexpected error: quota, permission, bad image, etc. Do
            # not pretend this was a stockout.
            raise RuntimeError(
                f"Capacity probe in zone={zone} failed with non-stockout error "
                f"code={code} message={message!r}. Aborting probe so the real "
                f"problem is not masked."
            )

        # Insert succeeded. Tear down the probe instance, then return.
        logger.info("Zone %s has capacity; selected.", zone)
        try:
            delete_op = client.delete(
                project=project_id,
                zone=zone,
                instance=probe_name,
            )
            _operation_outcome(delete_op)
        except Exception:
            # Probe instance cleanup is best-effort: a leaked $0.01/hr
            # instance is far better than failing the deploy. The
            # orphaned-resource sweep will catch it eventually.
            logger.exception(
                "Failed to delete capacity probe instance %s in %s; leaking.",
                probe_name,
                zone,
            )
        return zone

    raise AllZonesExhaustedError(f"No capacity for {machine_type} in any of: {', '.join(exhausted)}")
