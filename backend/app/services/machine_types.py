"""Curated machine type catalog for work nodes (ADR-034, ADR-043).

Machine type names map directly to GCE machine types for non-GPU entries.
GPU entries include accelerator metadata for the GCE API.
"""

MACHINE_TYPES: list[dict] = [
    {
        "name": "e2-standard-4",
        "category": "standard",
        "cpu": 4,
        "memory_gb": 16,
        "gpu": None,
        "description": "Light analysis, data wrangling (high availability)",
    },
    {
        "name": "e2-standard-8",
        "category": "standard",
        "cpu": 8,
        "memory_gb": 32,
        "gpu": None,
        "description": "General-purpose analysis (high availability)",
    },
    {
        "name": "n2-standard-4",
        "category": "standard",
        "cpu": 4,
        "memory_gb": 16,
        "gpu": None,
        "description": "Light analysis, data wrangling",
    },
    {
        "name": "n2-standard-8",
        "category": "standard",
        "cpu": 8,
        "memory_gb": 32,
        "gpu": None,
        "description": "General-purpose analysis",
    },
    {
        "name": "e2-highmem-8",
        "category": "high-memory",
        "cpu": 8,
        "memory_gb": 64,
        "gpu": None,
        "description": "Large datasets, Seurat integration (high availability)",
    },
    {
        "name": "n2-highmem-8",
        "category": "high-memory",
        "cpu": 8,
        "memory_gb": 64,
        "gpu": None,
        "description": "Large datasets, Seurat integration",
    },
    {
        "name": "n2-highmem-16",
        "category": "high-memory",
        "cpu": 16,
        "memory_gb": 128,
        "gpu": None,
        "description": "Very large datasets, multi-sample integration",
    },
    {
        "name": "n2-highmem-32",
        "category": "high-memory",
        "cpu": 32,
        "memory_gb": 256,
        "gpu": None,
        "description": "Extreme memory workloads",
    },
    {
        "name": "n1-standard-8-nvidia-tesla-t4",
        "category": "gpu",
        "cpu": 8,
        "memory_gb": 30,
        "gpu": "NVIDIA Tesla T4",
        "gce_machine_type": "n1-standard-8",
        "accelerator_type": "nvidia-tesla-t4",
        "accelerator_count": 1,
        "description": "scVI, rapids-singlecell, light deep learning",
    },
    {
        "name": "n1-standard-16-nvidia-tesla-v100",
        "category": "gpu",
        "cpu": 16,
        "memory_gb": 60,
        "gpu": "NVIDIA Tesla V100",
        "gce_machine_type": "n1-standard-16",
        "accelerator_type": "nvidia-tesla-v100",
        "accelerator_count": 1,
        "description": "Heavy deep learning, large-scale model training",
    },
]

MACHINE_TYPE_NAMES: set[str] = {mt["name"] for mt in MACHINE_TYPES}


def get_machine_type(name: str) -> dict | None:
    for mt in MACHINE_TYPES:
        if mt["name"] == name:
            return mt
    return None


# Memory-per-vCPU ratio (GB) for the standard GCE machine families, used to size
# machine types that are not in the curated catalog above.
_FAMILY_MEM_PER_VCPU = {"standard": 4, "highmem": 8, "highcpu": 1}


def machine_type_capacity(name: str) -> tuple[int, int] | None:
    """Return (vCPU, memory_gb) for a GCE machine type.

    Prefers the curated catalog, then falls back to parsing the standard
    `{family}-{class}-{vcpu}` naming (e.g. n2-highmem-16). Returns None when the
    name cannot be interpreted.
    """
    mt = get_machine_type(name)
    if mt:
        return mt["cpu"], mt["memory_gb"]
    parts = (name or "").split("-")
    if len(parts) >= 3 and parts[-1].isdigit():
        vcpu = int(parts[-1])
        per_vcpu = _FAMILY_MEM_PER_VCPU.get(parts[-2])
        if per_vcpu and vcpu > 0:
            return vcpu, vcpu * per_vcpu
    return None
