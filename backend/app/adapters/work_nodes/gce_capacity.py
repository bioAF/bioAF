"""Pre-flight GCE zone capacity probe (BAL work-node backend internal).

Relocated from app/services/zone_capacity_probe.py into the adapter layer in
Phase 6 so that the only `compute_v1` usage lives under `adapters/`. The probe
logic is unchanged: GKE Standard clusters provision a throwaway default node
pool at create time with ``initial_node_count = 1`` per zone (for regional
clusters). That pool has no autoscaling and no ``location_policy``, so if any
one zone is out of capacity for the requested machine type, the per-zone IGM
hangs for ~70 minutes before GKE gives up. Terraform times out at 40 min and the
cluster is stuck in ``RUNNING_WITH_ERROR``.

Random suffixes on cluster names defend against name collisions; they do nothing
against per-zone capacity stockouts. This module checks capacity before the
cluster create so we can pin the default pool's ``node_locations`` to a single
zone that we have just observed to have capacity.

The probe attempts a real instance insert in each candidate zone in order. The
first zone whose insert succeeds wins. If the insert returns
``ZONE_RESOURCE_POOL_EXHAUSTED`` or ``GCE_STOCKOUT``, we move on. The probe
instance is deleted before we return.

It is exposed to application code through ``WorkNodeProvider.probe_zone_capacity``
(the GCE work-node adapter); callers never import this module or the SDK.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.exceptions import ValidationError

logger = logging.getLogger("bioaf.adapters.work_nodes.gce_capacity")

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


def _classify_operation(op: Any) -> tuple[str, str | None, str | None]:
    """Categorise an insert/delete operation outcome.

    Returns one of:
        ("ok", None, None)                  -- operation completed cleanly
        ("stockout", gce_code, message)     -- zone has no capacity
        ("error", code_str, message)        -- non-stockout failure

    The compute_v1 SDK exposes errors in two places:

    * ``op.error_code`` / ``op.error_message`` are PROPERTIES (not
      methods) that surface the HTTP-level status of the long-running
      operation. For a stockout these typically come back as 503 with a
      generic message: not specific enough to act on.
    * ``op._extended_operation.error.errors[]`` is a list of GCE-specific
      error structs with ``.code`` strings like
      ``ZONE_RESOURCE_POOL_EXHAUSTED`` or ``GCE_STOCKOUT``. This is what
      we branch on.

    An earlier version of this function called ``op.error_code()`` (with
    parens) which raises ``TypeError: 'int' object is not callable`` on
    the real SDK -- caught in production after the first deploy. Tests
    now use a fake that subclasses the real SDK's surface so this
    failure mode is caught at unit-test time.
    """
    try:
        op.result(timeout=_OPERATION_TIMEOUT_SECONDS)
    except Exception:
        # ExtendedOperation.result() raises GoogleAPICallError on
        # failure. The structured fields below tell us specifically why,
        # so swallow and inspect.
        pass

    # Walk the GCE-specific error codes first.
    extended = getattr(op, "_extended_operation", None)
    if extended is not None:
        error_struct = getattr(extended, "error", None)
        if error_struct is not None:
            for err in getattr(error_struct, "errors", None) or []:
                gce_code = getattr(err, "code", None)
                if gce_code in _STOCKOUT_CODES:
                    return ("stockout", gce_code, getattr(err, "message", None))
            # There were error entries but none were stockouts: surface
            # the first one as a hard error so the caller does not retry
            # blindly through other zones.
            first = next(iter(error_struct.errors or []), None)
            if first is not None:
                return (
                    "error",
                    getattr(first, "code", None),
                    getattr(first, "message", None),
                )

    # No GCE-specific entries, but maybe an HTTP-level failure (proxy
    # error, IAM hiccup before GCE even saw the request, etc.). These
    # are reported on the operation's top-level properties.
    http_code = getattr(op, "error_code", 0) or 0
    http_message = getattr(op, "error_message", "") or ""
    if http_code or http_message:
        return ("error", str(http_code) if http_code else None, http_message or None)

    return ("ok", None, None)


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
        ValidationError: ``zones`` was empty.
    """
    if not zones:
        raise ValidationError("probe_zones requires at least one candidate zone")

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
        outcome, code, message = _classify_operation(insert_op)

        if outcome == "stockout":
            logger.info("Zone %s exhausted (code=%s); trying next.", zone, code)
            exhausted.append(zone)
            continue

        if outcome == "error":
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
            _classify_operation(delete_op)
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
