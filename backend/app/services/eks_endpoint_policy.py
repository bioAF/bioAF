"""EKS Kubernetes API endpoint and control-plane logging policy (security plan 04C).

Pure decisions over recorded platform_config: which endpoint and logging inputs a compute plan
passes, whether an existing cluster's identity checks out, and whether a reviewed plan changes the
cluster the way the recorded mode allows. No AWS SDK and no database: the AWS compute hooks in
``eks_endpoint`` gather state and live values and call these.

Endpoint modes:

- ``private_only``: fresh-install default. Public endpoint off, private on; needs the recorded
  application network so the backend reaches the API over the VPC peering.
- ``public_restricted``: explicit opt-in. Private stays on; public is limited to an approved,
  nonempty CIDR list with no unrestricted range.
- ``preserve_existing``: an already deployed cluster recorded on upgrade. Its observed flags and
  CIDRs are passed back exactly (including legacy unrestricted access) until a reviewed cutover.
  Never valid for a new or replacement cluster, and never a fallback from another mode.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime

from app.exceptions import ValidationError

MODE_KEY = "aws_eks_endpoint_mode"
CIDRS_KEY = "aws_eks_public_access_cidrs"
SNAPSHOT_KEY = "aws_eks_endpoint_snapshot"
CUTOVER_KEY = "aws_eks_endpoint_cutover"
RETENTION_KEY = "aws_eks_log_retention_days"
LOG_TYPES_KEY = "aws_eks_log_types"
CONFIG_KEYS = (MODE_KEY, CIDRS_KEY, SNAPSHOT_KEY, CUTOVER_KEY, RETENTION_KEY, LOG_TYPES_KEY)

PRIVATE_ONLY = "private_only"
PUBLIC_RESTRICTED = "public_restricted"
PRESERVE_EXISTING = "preserve_existing"
MODES = (PRIVATE_ONLY, PUBLIC_RESTRICTED, PRESERVE_EXISTING)
HARDENED_MODES = (PRIVATE_ONLY, PUBLIC_RESTRICTED)

LOG_TYPES = ("api", "audit", "authenticator", "controllerManager", "scheduler")
REQUIRED_LOG_TYPES = ("audit", "authenticator")
DEFAULT_RETENTION_DAYS = 30
# The values CloudWatch Logs accepts for a log group's retention.
RETENTION_DAYS = frozenset(
    {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653}
)

CLUSTER_ADDRESS = "aws_eks_cluster.this"

_ENDPOINT_FIELDS = ("endpoint_public_access", "endpoint_private_access", "public_access_cidrs")


def _json(config: dict, key: str):
    raw = config.get(key)
    if raw in (None, "", "null"):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"The recorded {key} is not valid JSON.") from exc


def _endpoint(public: bool, private: bool, cidrs: list[str]) -> dict:
    return {"endpoint_public_access": public, "endpoint_private_access": private, "public_access_cidrs": cidrs}


def validate_public_cidrs(cidrs) -> list[str]:
    """Approved public egress ranges: nonempty, valid and none unrestricted."""
    if not isinstance(cidrs, list) or not cidrs:
        raise ValidationError(
            "Restricted public Kubernetes API access needs at least one approved egress CIDR "
            "(the application's stable public egress if it uses the public endpoint, and any approved "
            "operator egress)."
        )
    normalized = []
    for value in cidrs:
        try:
            network = ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError as exc:
            raise ValidationError(f"{value!r} is not a valid CIDR for Kubernetes API access.") from exc
        if network.prefixlen == 0:
            raise ValidationError(
                f"{value} would allow the whole internet to reach the Kubernetes API; list approved egress ranges instead."
            )
        normalized.append(str(network))
    return normalized


def _snapshot(config: dict) -> dict:
    snapshot = _json(config, SNAPSHOT_KEY)
    if not isinstance(snapshot, dict) or not all(field in snapshot for field in _ENDPOINT_FIELDS):
        raise ValidationError(
            "The existing cluster's endpoint snapshot is missing. Run a compute plan so the backend "
            "can record the cluster's current endpoint settings before changing anything."
        )
    return snapshot


def _target(config: dict, cluster_in_state: bool) -> tuple[str, dict]:
    """The mode and endpoint settings a compute plan applies."""
    cutover = _json(config, CUTOVER_KEY)
    mode = (cutover or {}).get("mode") if cutover else config.get(MODE_KEY) or None
    if mode in (None, "", "null"):
        if cluster_in_state:
            raise ValidationError(
                "This cluster has no recorded Kubernetes API endpoint mode. The backend records the "
                "existing endpoint (preserve_existing) before an upgraded compute plan; it does not "
                "apply the fresh-install private-only default to an existing cluster."
            )
        mode = PRIVATE_ONLY
    if mode not in MODES:
        raise ValidationError(f"Unknown Kubernetes API endpoint mode {mode!r}; expected one of {', '.join(MODES)}.")

    if mode == PRESERVE_EXISTING:
        if not cluster_in_state:
            raise ValidationError(
                "preserve_existing only keeps an existing cluster's endpoint; it cannot create a new or "
                "replacement cluster. Choose private_only or public_restricted."
            )
        snap = _snapshot(config)
        return mode, _endpoint(
            bool(snap["endpoint_public_access"]),
            bool(snap["endpoint_private_access"]),
            list(snap.get("public_access_cidrs") or []),
        )

    if mode == PRIVATE_ONLY:
        if not (config.get("aws_app_vpc_id") and config.get("aws_app_security_group_id")):
            raise ValidationError(
                "A private-only Kubernetes API needs the recorded application network (VPC and "
                "security group) so the backend can reach it over VPC peering. Record the application "
                "network, or opt in to restricted public access with approved egress CIDRs."
            )
        return mode, _endpoint(False, True, [])

    source = cutover if cutover else {"public_access_cidrs": _json(config, CIDRS_KEY)}
    return mode, _endpoint(True, True, validate_public_cidrs(source.get("public_access_cidrs")))


def _logging(config: dict) -> dict:
    raw_days = config.get(RETENTION_KEY)
    if raw_days in (None, "", "null"):
        days = DEFAULT_RETENTION_DAYS
    else:
        try:
            days = int(str(raw_days).strip())
        except ValueError as exc:
            raise ValidationError(f"Control-plane log retention {raw_days!r} is not a number of days.") from exc
        if str(days) != str(raw_days).strip() or days not in RETENTION_DAYS:
            raise ValidationError(
                f"Control-plane log retention {raw_days} days is not a CloudWatch Logs retention value "
                f"({', '.join(str(d) for d in sorted(RETENTION_DAYS))})."
            )

    override = _json(config, LOG_TYPES_KEY)
    if override is None:
        types = list(LOG_TYPES)
    else:
        if not isinstance(override, list):
            raise ValidationError("The control-plane log type override must be a list.")
        unknown = sorted(set(override) - set(LOG_TYPES))
        if unknown:
            raise ValidationError(
                f"Unknown control-plane log type {', '.join(unknown)}; expected {', '.join(LOG_TYPES)}."
            )
        missing = [t for t in REQUIRED_LOG_TYPES if t not in override]
        if missing:
            raise ValidationError(
                f"The control-plane log override must keep the audit and authentication trail (missing {', '.join(missing)})."
            )
        types = [t for t in LOG_TYPES if t in override]
    return {"control_plane_log_types": types, "control_plane_log_retention_days": days}


def compute_logging(config: dict) -> dict:
    """The validated control-plane logging inputs alone. Raises to refuse."""
    return _logging(config)


def compute_tfvars(config: dict, cluster_in_state: bool) -> dict:
    """The explicit endpoint and logging inputs for a compute plan or apply. Raises to refuse."""
    _mode, endpoint = _target(config, cluster_in_state)
    return {**endpoint, **_logging(config)}


# --- Existing-cluster identity ---------------------------------------------------


def state_cluster(state: dict | None) -> dict | None:
    """The managed EKS cluster's attributes from a pulled (unrefreshed) state document."""
    for resource in (state or {}).get("resources") or []:
        if (
            resource.get("mode") == "managed"
            and resource.get("type") == "aws_eks_cluster"
            and resource.get("name") == "this"
        ):
            instances = resource.get("instances") or []
            if instances:
                return instances[0].get("attributes") or {}
    return None


def _parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_instant(a, b) -> bool:
    left, right = _parse_time(a), _parse_time(b)
    return left is not None and right is not None and abs((left - right).total_seconds()) < 1


def _live_endpoint(live: dict) -> dict:
    vpc = live.get("resourcesVpcConfig") or {}
    return _endpoint(
        bool(vpc.get("endpointPublicAccess")),
        bool(vpc.get("endpointPrivateAccess")),
        list(vpc.get("publicAccessCidrs") or []),
    )


def snapshot_existing_cluster(state: dict | None, live: dict, config: dict) -> dict:
    """Verify the live cluster is the one in remote state and return its endpoint snapshot.

    Identity comes from the pre-refresh remote compute state (ARN, creation time, name), never from
    the live cluster alone, so a replaced or foreign cluster is refused instead of adopted.
    """
    attributes = state_cluster(state)
    if attributes is None:
        raise ValidationError(
            "The remote compute state could not be read or has no EKS cluster, so the existing cluster's "
            "identity cannot be verified. Reconcile the compute state before planning."
        )
    arn = attributes.get("arn")
    created = attributes.get("created_at")
    if not arn:
        raise ValidationError("The remote compute state has no cluster ARN; the existing cluster cannot be verified.")
    if not created:
        raise ValidationError(
            "The remote compute state has no cluster creation time; the existing cluster cannot be verified."
        )
    parts = arn.split(":")
    if len(parts) < 6 or parts[2] != "eks":
        raise ValidationError(f"The remote compute state's cluster ARN {arn!r} is not an EKS cluster ARN.")
    if config.get("aws_region") and parts[3] != config["aws_region"]:
        raise ValidationError(f"The cluster in remote state is in region {parts[3]}, not {config['aws_region']}.")
    if config.get("aws_account_id") and parts[4] != config["aws_account_id"]:
        raise ValidationError(
            f"The cluster in remote state belongs to account {parts[4]}, not {config['aws_account_id']}."
        )
    recorded_name = config.get("gke_cluster_name")
    if recorded_name and recorded_name not in ("null",) and attributes.get("name") != recorded_name:
        raise ValidationError(
            f"The cluster in remote state ({attributes.get('name')}) does not have the recorded name {recorded_name}."
        )
    if live.get("arn") != arn or not _same_instant(live.get("createdAt"), created):
        raise ValidationError(
            "The live EKS cluster does not match the cluster in remote compute state (ARN or creation time "
            "differ). It may have been replaced; nothing was recorded or changed."
        )
    created_at = _parse_time(live.get("createdAt"))
    return {
        "cluster_arn": arn,
        "cluster_name": attributes.get("name"),
        "created_at": created_at.isoformat() if created_at else str(created),
        **_live_endpoint(live),
    }


def require_live_matches(snapshot: dict, live: dict) -> None:
    """Refuse when the live cluster or its endpoint moved away from the recorded snapshot."""
    if live.get("arn") != snapshot.get("cluster_arn"):
        raise ValidationError(
            "The live EKS cluster changed since its endpoint was recorded (different ARN). Review it before applying."
        )
    endpoint = _live_endpoint(live)
    expected = _endpoint(
        bool(snapshot.get("endpoint_public_access")),
        bool(snapshot.get("endpoint_private_access")),
        list(snapshot.get("public_access_cidrs") or []),
    )
    if not _endpoint_equal(endpoint, expected):
        raise ValidationError(
            "The cluster's Kubernetes API endpoint changed since it was recorded. Review the live setting; "
            "the recorded snapshot was not overwritten and no mode was chosen automatically."
        )


def _endpoint_equal(a: dict, b: dict) -> bool:
    if a["endpoint_public_access"] != b["endpoint_public_access"]:
        return False
    if a["endpoint_private_access"] != b["endpoint_private_access"]:
        return False
    if not a["endpoint_public_access"]:
        return True  # CIDRs only apply to the public endpoint.
    return sorted(a["public_access_cidrs"] or []) == sorted(b["public_access_cidrs"] or [])


def live_endpoint(live: dict) -> dict:
    return _live_endpoint(live)


def endpoint_equal(a: dict, b: dict) -> bool:
    return _endpoint_equal(a, b)


# --- Plan review -----------------------------------------------------------------


def _vpc(values: dict | None) -> dict | None:
    vpc = (values or {}).get("vpc_config") or []
    if not vpc:
        return None
    first = vpc[0] or {}
    return _endpoint(
        bool(first.get("endpoint_public_access")),
        bool(first.get("endpoint_private_access")),
        list(first.get("public_access_cidrs") or []),
    )


def review_plan(raw_plan: dict, config: dict) -> None:
    """Refuse a compute plan that replaces the cluster or moves its endpoint outside the mode."""
    change = next((rc for rc in raw_plan.get("resource_changes") or [] if rc.get("address") == CLUSTER_ADDRESS), None)
    if change is None:
        return
    actions = (change.get("change") or {}).get("actions") or []
    if "delete" in actions:
        raise ValidationError(
            "This compute plan would replace or delete the EKS cluster. bioAF does not replace a cluster "
            "through an ordinary plan; review the change (and use teardown if removal is intended)."
        )
    cutover = _json(config, CUTOVER_KEY)
    mode = (cutover or {}).get("mode") or config.get(MODE_KEY) or PRIVATE_ONLY
    if "create" in actions:
        if mode == PRESERVE_EXISTING:
            raise ValidationError(
                "preserve_existing only keeps an existing cluster's endpoint; this plan would create a new cluster."
            )
        return
    if mode != PRESERVE_EXISTING:
        return
    before = _vpc((change.get("change") or {}).get("before"))
    after = _vpc((change.get("change") or {}).get("after"))
    snapshot = _snapshot(config)
    expected = _endpoint(
        bool(snapshot["endpoint_public_access"]),
        bool(snapshot["endpoint_private_access"]),
        list(snapshot.get("public_access_cidrs") or []),
    )
    for values in (before, after):
        if values is not None and not _endpoint_equal(values, expected):
            raise ValidationError(
                "This compute plan changes the cluster's Kubernetes API endpoint while it is preserved "
                "(preserve_existing). Endpoint changes happen only through a reviewed cutover."
            )


# --- Status ------------------------------------------------------------------------


def status(config: dict) -> dict:
    """What an administrator sees: the mode, the preserved exposure and any pending cutover."""
    mode = config.get(MODE_KEY) or None
    snapshot = _json(config, SNAPSHOT_KEY) or {}
    cutover = _json(config, CUTOVER_KEY)
    if mode == PRESERVE_EXISTING:
        public = bool(snapshot.get("endpoint_public_access"))
        cidrs = list(snapshot.get("public_access_cidrs") or [])
    elif mode == PUBLIC_RESTRICTED:
        public, cidrs = True, list(_json(config, CIDRS_KEY) or [])
    else:
        public, cidrs = False, []
    return {
        "mode": mode,
        "endpoint_public_access": public,
        "public_access_cidrs": cidrs,
        # Preserved settings are temporary; only a hardened mode closes the finding.
        "pending_hardening": mode not in HARDENED_MODES if mode else False,
        "snapshot": snapshot or None,
        "cutover": cutover,
    }
