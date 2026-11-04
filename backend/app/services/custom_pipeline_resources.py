"""Whether a custom pipeline Job's resource request fits the pool it is scheduled on.

Every custom Job pod is pinned to the pipeline-head pool (``k8s_manifest.build_job_manifest``). A
pod that asks for more than one of that pool's nodes can give it is not slow to schedule: it is
Pending forever while its run reports ``running``. So a request is checked before a version is
created and before a run is launched or reproduced.

The limit was measured on blackbird's head node (GCP ``e2-standard-2``): 1930m CPU and 6170244Ki
allocatable, less 418m / 845Mi of DaemonSet requests and 100m for kube-proxy, leaves about 1412m and
5180Mi for one Job pod. AWS deploys ``t3.large``, the same 2 vCPU / 8 GiB with more allocatable
memory under EKS's reservations, so one rounded-down limit holds on both. Both machine types are
the Terraform defaults and cannot be changed from the app; a test ties this table to them, so
changing either one means measuring again.
"""

import re
from decimal import Decimal, InvalidOperation

from app.exceptions import ValidationError

HEAD_POOL_MACHINE_TYPES: dict[str, str] = {"gcp": "e2-standard-2", "aws": "t3.large"}

JOB_CPU_LIMIT_MILLICORES = 1400
JOB_MEMORY_LIMIT_MIB = 5120

_LIMIT_IN_WORDS = "at most 1.4 CPU and 5Gi of memory"

_MEMORY = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$")
_MIB_PER_UNIT: dict[str, Decimal] = {
    "": Decimal(1) / Decimal(1024 * 1024),
    "Ki": Decimal(1) / Decimal(1024),
    "Mi": Decimal(1),
    "Gi": Decimal(1024),
    "Ti": Decimal(1024 * 1024),
    "K": Decimal(1000) / Decimal(1024 * 1024),
    "M": Decimal(1000**2) / Decimal(1024 * 1024),
    "G": Decimal(1000**3) / Decimal(1024 * 1024),
    "T": Decimal(1000**4) / Decimal(1024 * 1024),
}


def cpu_millicores(value: str) -> Decimal:
    """A Kubernetes CPU quantity ("1", "1.5", "500m") in millicores; ValidationError if unreadable."""
    text = (value or "").strip()
    try:
        amount = Decimal(text[:-1]) if text.endswith("m") else Decimal(text) * 1000
    except InvalidOperation:
        amount = Decimal(-1)
    if not text or amount <= 0:
        raise ValidationError(
            f'CPU "{value}" is not a CPU amount bioAF can read. Use a number of CPUs, like 1 or 500m.'
        )
    return amount


def memory_mib(value: str) -> Decimal:
    """A Kubernetes memory quantity ("4Gi", "512Mi", "6G") in MiB; ValidationError if unreadable."""
    match = _MEMORY.match((value or "").strip())
    if not match or Decimal(match.group(1)) <= 0:
        raise ValidationError(
            f'Memory "{value}" is not a memory amount bioAF can read. Use a size with a unit, like 4Gi or 512Mi.'
        )
    return Decimal(match.group(1)) * _MIB_PER_UNIT[match.group(2) or ""]


def check_fits_head_pool(cpu_request: str, memory_request: str) -> None:
    """Refuse a request one pipeline-head node cannot schedule, in words a scientist can act on."""
    cpu = cpu_millicores(cpu_request)
    memory = memory_mib(memory_request)
    if cpu > JOB_CPU_LIMIT_MILLICORES or memory > JOB_MEMORY_LIMIT_MIB:
        raise ValidationError(
            f"This pipeline version asks for {cpu_request} CPU and {memory_request} of memory, but a custom "
            f"pipeline job can use {_LIMIT_IN_WORDS} here, so it would never start. Lower its CPU or memory."
        )
