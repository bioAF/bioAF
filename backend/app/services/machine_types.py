"""Curated machine type catalog for work nodes (ADR-034)."""

MACHINE_TYPES: list[dict] = [
    {
        "name": "n2-standard-4",
        "category": "standard",
        "cpu": 4,
        "memory_gb": 16,
        "gpu": None,
        "description": "Light analysis, data wrangling",
        "node_pool": "bioaf-interactive",
    },
    {
        "name": "n2-standard-8",
        "category": "standard",
        "cpu": 8,
        "memory_gb": 32,
        "gpu": None,
        "description": "General-purpose analysis",
        "node_pool": "bioaf-interactive",
    },
    {
        "name": "n2-highmem-8",
        "category": "high-memory",
        "cpu": 8,
        "memory_gb": 64,
        "gpu": None,
        "description": "Large datasets, Seurat integration",
        "node_pool": "bioaf-interactive",
    },
    {
        "name": "n2-highmem-16",
        "category": "high-memory",
        "cpu": 16,
        "memory_gb": 128,
        "gpu": None,
        "description": "Very large datasets, multi-sample integration",
        "node_pool": "bioaf-interactive",
    },
    {
        "name": "n2-highmem-32",
        "category": "high-memory",
        "cpu": 32,
        "memory_gb": 256,
        "gpu": None,
        "description": "Extreme memory workloads",
        "node_pool": "bioaf-interactive",
    },
    {
        "name": "n1-standard-8-nvidia-tesla-t4",
        "category": "gpu",
        "cpu": 8,
        "memory_gb": 30,
        "gpu": "NVIDIA Tesla T4",
        "description": "scVI, rapids-singlecell, light deep learning",
        "node_pool": "bioaf-gpu",
    },
    {
        "name": "n1-standard-16-nvidia-tesla-v100",
        "category": "gpu",
        "cpu": 16,
        "memory_gb": 60,
        "gpu": "NVIDIA Tesla V100",
        "description": "Heavy deep learning, large-scale model training",
        "node_pool": "bioaf-gpu",
    },
]

MACHINE_TYPE_NAMES: set[str] = {mt["name"] for mt in MACHINE_TYPES}


def get_machine_type(name: str) -> dict | None:
    for mt in MACHINE_TYPES:
        if mt["name"] == name:
            return mt
    return None
