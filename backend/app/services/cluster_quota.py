"""Decide whether a proposed node-pool config can actually be built.

A pipeline pool moved onto pd-balanced at 500 GB produced ZERO schedulable nodes:
pd-balanced bills to `SSD_TOTAL_GB`, whose regional limit was 500 GB, so one node
exhausted it. Every signal said success. Terraform reported `completed 2/2`, the
node pool reported `RUNNING`, and the only place the truth appeared was the GCE
audit log's `QUOTA_EXCEEDED` on `instances.insert`. A study sat in `running` for 35
minutes behind 11 pods that could never be placed.

The arithmetic here is what the Components page needed and did not have. It is
deliberately pure: no cloud calls, no session, no I/O. The reader that fetches live
quota is a separate seam, so this decision stays testable against the exact numbers
the incident produced.

Two severities, because two different things were wrong and they are not the same
kind of wrong:

- **block**: not even one node fits. The pool can never schedule anything, so every
  run against it hangs forever. This is the pd-balanced case.
- **warn**: at least one node fits but fewer than `max_nodes`. The pool works at
  reduced concurrency. Not broken, just not what was typed, and the operator is told
  rather than left to infer it from throughput.

Failing open is deliberate. A quota that cannot be read must never block an
operator: a missing IAM role, a cloud API blip, or a provider with no reader would
otherwise make the page unusable. Unschedulable-run detection is the backstop that
makes an unverified config surface loudly instead of silently.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# QuotaMetric is the adapter layer's return model: this module decides against it,
# it does not own it. Services may depend on adapters; the reverse is a layering
# inversion that test_bal_layering pins.
from app.adapters.models import QuotaMetric

# Which regional quota a disk type bills to. This mapping is the whole incident:
# the two buckets have wildly different limits (500 vs 4096 GB in bioaf-495400)
# and nothing on the Components page said so.
_DISK_QUOTA_METRIC = {
    "pd-balanced": "SSD_TOTAL_GB",
    "pd-ssd": "SSD_TOTAL_GB",
    "pd-extreme": "SSD_TOTAL_GB",
    "pd-standard": "DISKS_TOTAL_GB",
}

# GCE machine types encode their vCPU count as a trailing integer
# (`n2-highmem-16` -> 16). Quota charges TOTAL vCPU, which is NOT the
# `_MACHINE_ALLOCATABLE` figure the Nextflow config uses: that one is what
# Kubernetes can hand to a pod after system reservations (14 for n2-highmem-16).
# Using allocatable here would under-count every pool by ~12%.
_VCPU_SUFFIX = re.compile(r"-(\d+)$")

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_BLOCK = "block"
STATUS_UNVERIFIED = "unverified"


@dataclass(frozen=True)
class PoolPlan:
    """A proposed (or current) node-pool shape."""

    machine_type: str
    max_nodes: int
    disk_size_gb: int
    disk_type: str
    use_spot: bool = True


@dataclass(frozen=True)
class QuotaVerdict:
    """What the preflight concluded, in terms an operator can act on."""

    status: str
    achievable_nodes: int | None
    binding_metric: str | None
    message: str
    unverified_metrics: tuple[str, ...] = field(default=())


def machine_vcpus(machine_type: str) -> int:
    """Total vCPU for a GCE machine type, or 0 when it cannot be derived.

    0 means "do not evaluate CPU", not "free"; shared-core types (`e2-micro`)
    carry no trailing count and must not be read as zero-cost.
    """
    match = _VCPU_SUFFIX.search(machine_type or "")
    return int(match.group(1)) if match else 0


def disk_quota_metric(disk_type: str) -> str | None:
    """The quota bucket a disk type bills to, or None when unrecognised."""
    return _DISK_QUOTA_METRIC.get(disk_type)


def _cpu_quota_metrics(plan: PoolPlan, quotas: dict[str, QuotaMetric]) -> list[str]:
    """The CPU quotas this pool charges.

    A machine bills to its family quota (`N2_CPUS`) AND to an aggregate. Which
    aggregate depends on preemptibility: GCE charges Spot instances to
    `PREEMPTIBLE_CPUS` when that quota is non-zero, and falls back to the regular
    `CPUS` quota when it is zero, which is bioaf-495400's case. Reading it the
    other way would make every Spot pool look unbounded.
    """
    metrics: list[str] = []

    family = (plan.machine_type or "").split("-", 1)[0].upper()
    if family:
        metrics.append(f"{family}_CPUS")

    preemptible = quotas.get("PREEMPTIBLE_CPUS")
    if plan.use_spot and preemptible is not None and preemptible.limit > 0:
        metrics.append("PREEMPTIBLE_CPUS")
    else:
        metrics.append("CPUS")

    return metrics


def _headroom(
    metric_name: str,
    per_node: float,
    quotas: dict[str, QuotaMetric],
    pool_current_usage: dict[str, float],
) -> tuple[int, float] | None:
    """Nodes this quota allows and its free capacity, or None if unknown.

    Headroom is net of what the pool already holds, so re-applying an unchanged
    config on a running pool is not blocked by its own disks. The subtraction is
    clamped at zero: a caller passing usage figures that disagree with the live
    quota must not manufacture headroom that does not exist.
    """
    quota = quotas.get(metric_name)
    if quota is None or per_node <= 0:
        return None

    already_ours = pool_current_usage.get(metric_name, 0.0)
    usage_excluding_pool = max(0.0, quota.usage - already_ours)
    free = max(0.0, quota.limit - usage_excluding_pool)
    return int(math.floor(free / per_node)), free


def per_node_costs(plan: PoolPlan, quotas: dict[str, QuotaMetric]) -> dict[str, float]:
    """What one node of `plan` consumes, by quota metric.

    Public because the same arithmetic prices two different things: what a
    PROPOSED pool would need, and what the CURRENT pool already holds (which is
    netted out of the headroom the proposal is measured against).
    """
    costs: dict[str, float] = {}

    disk_metric = disk_quota_metric(plan.disk_type)
    if disk_metric:
        costs[disk_metric] = float(plan.disk_size_gb)

    vcpus = machine_vcpus(plan.machine_type)
    if vcpus:
        for cpu_metric in _cpu_quota_metrics(plan, quotas):
            costs[cpu_metric] = float(vcpus)

    return costs


def evaluate_pool_quota(
    plan: PoolPlan,
    quotas: dict[str, QuotaMetric] | None,
    pool_current_usage: dict[str, float] | None = None,
) -> QuotaVerdict:
    """Decide whether `plan` can be built against `quotas`.

    `quotas` of None means the quota could not be read at all; the verdict is
    `unverified` and never blocks.
    """
    if quotas is None:
        return QuotaVerdict(
            status=STATUS_UNVERIFIED,
            achievable_nodes=None,
            binding_metric=None,
            message="Could not read this region's quota, so the pool size was not verified.",
        )

    pool_current_usage = pool_current_usage or {}

    per_node = per_node_costs(plan, quotas)

    supported: dict[str, int] = {}
    free_by_metric: dict[str, float] = {}
    unverified: list[str] = []
    for metric_name, cost in per_node.items():
        result = _headroom(metric_name, cost, quotas, pool_current_usage)
        if result is None:
            unverified.append(metric_name)
        else:
            supported[metric_name], free_by_metric[metric_name] = result

    if not supported:
        return QuotaVerdict(
            status=STATUS_UNVERIFIED,
            achievable_nodes=None,
            binding_metric=None,
            message="Could not read this region's quota, so the pool size was not verified.",
            unverified_metrics=tuple(unverified),
        )

    # Ties are broken by least absolute headroom. Two quotas can allow the same node
    # count (CPUS with 193 free and N2_CPUS with 200 both allow 12 x 16 vCPU), and the
    # useful one to name is the one closest to its ceiling, since that is what binds
    # first as anything else in the region grows.
    binding_metric = min(supported, key=lambda name: (supported[name], free_by_metric[name]))
    achievable = supported[binding_metric]

    if achievable <= 0:
        free = free_by_metric[binding_metric]
        message = (
            f"{binding_metric} has {free:g} free in this region, but one node needs "
            f"{per_node[binding_metric]:g}. This pool could not create a single node. "
            f"The largest that fits is {free:g}."
        )
        return QuotaVerdict(
            status=STATUS_BLOCK,
            achievable_nodes=0,
            binding_metric=binding_metric,
            message=message,
            unverified_metrics=tuple(unverified),
        )

    if achievable < plan.max_nodes:
        message = (
            f"{binding_metric} supports {achievable} of {plan.max_nodes} nodes at these "
            f"settings. The pool will run at reduced concurrency rather than fail."
        )
        return QuotaVerdict(
            status=STATUS_WARN,
            achievable_nodes=achievable,
            binding_metric=binding_metric,
            message=message,
            unverified_metrics=tuple(unverified),
        )

    if unverified:
        return QuotaVerdict(
            status=STATUS_UNVERIFIED,
            achievable_nodes=achievable,
            binding_metric=binding_metric,
            message=(
                "Quota for "
                + ", ".join(sorted(unverified))
                + " could not be read, so the pool size was only partly verified."
            ),
            unverified_metrics=tuple(unverified),
        )

    return QuotaVerdict(
        status=STATUS_OK,
        achievable_nodes=achievable,
        binding_metric=binding_metric,
        message="",
    )
