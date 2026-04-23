"""Adapter registry - resolves active adapters from platform_config.

Singleton initialized on application startup. Reads compute_stack from
the database and instantiates the correct adapter implementations.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CellxgeneProvider, ComputeProvider, NotebookProvider, StorageProvider, WorkNodeProvider

logger = logging.getLogger("bioaf.adapters.registry")

VALID_COMPUTE_STACKS = ("kubernetes", "slurm")

# Singleton state
_compute_adapter: ComputeProvider | None = None
_storage_adapter: StorageProvider | None = None
_notebook_adapter: NotebookProvider | None = None
_cellxgene_adapter: CellxgeneProvider | None = None
_work_node_adapter: WorkNodeProvider | None = None
_initialized: bool = False


def _create_adapters(
    compute_stack: str,
    session_factory=None,
) -> tuple[ComputeProvider, StorageProvider, NotebookProvider, CellxgeneProvider]:
    """Instantiate adapters based on the compute_stack value."""
    if compute_stack not in VALID_COMPUTE_STACKS:
        raise ValueError(f"Unknown compute_stack '{compute_stack}'. Valid options: {VALID_COMPUTE_STACKS}")

    if compute_stack == "kubernetes":
        from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider
        from app.adapters.compute.kubernetes import KubernetesComputeProvider
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
        from app.adapters.storage.gcs import GcsStorageProvider

        return (
            KubernetesComputeProvider(session_factory=session_factory),
            GcsStorageProvider(),
            KubernetesNotebookProvider(session_factory=session_factory),
            KubernetesCellxgeneProvider(session_factory=session_factory),
        )
    else:
        from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider
        from app.adapters.compute.slurm import SlurmComputeProvider
        from app.adapters.notebooks.slurm import SlurmNotebookProvider
        from app.adapters.storage.nfs import NfsStorageProvider

        return (
            SlurmComputeProvider(),
            NfsStorageProvider(),
            SlurmNotebookProvider(),
            KubernetesCellxgeneProvider(session_factory=session_factory),
        )


async def initialize_adapters(session: AsyncSession, session_factory=None) -> None:
    """Read compute_stack from platform_config and initialize adapters."""
    global _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter, _work_node_adapter, _initialized

    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'compute_stack'"))
    row = result.first()
    compute_stack = row[0] if row else "kubernetes"

    logger.info("Initializing BAL adapters for compute_stack=%s", compute_stack)
    _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter = _create_adapters(
        compute_stack, session_factory=session_factory
    )

    # Work nodes always use GCE regardless of compute_stack (ADR-043)
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    _work_node_adapter = GCEWorkNodeProvider(session_factory=session_factory)

    # Eagerly load cluster config so adapters never need to run async DB
    # queries from a sync context (which breaks asyncpg).
    if hasattr(_compute_adapter, "load_cluster_config"):
        await _compute_adapter.load_cluster_config()
    if hasattr(_notebook_adapter, "load_cluster_config"):
        await _notebook_adapter.load_cluster_config()
    if hasattr(_cellxgene_adapter, "load_cluster_config"):
        await _cellxgene_adapter.load_cluster_config()
    await _work_node_adapter.load_gcp_config()

    _initialized = True


def initialize_adapters_sync(compute_stack: str) -> None:
    """Initialize adapters synchronously from a known value (for testing)."""
    global _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter, _work_node_adapter, _initialized

    _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter = _create_adapters(compute_stack)

    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    _work_node_adapter = GCEWorkNodeProvider()
    _initialized = True


def get_compute_adapter() -> ComputeProvider:
    """Get the active compute adapter."""
    if not _initialized or _compute_adapter is None:
        raise RuntimeError("Adapter registry not initialized. Call initialize_adapters() first.")
    return _compute_adapter


def get_storage_adapter() -> StorageProvider:
    """Get the active storage adapter."""
    if not _initialized or _storage_adapter is None:
        raise RuntimeError("Adapter registry not initialized. Call initialize_adapters() first.")
    return _storage_adapter


def get_notebook_adapter() -> NotebookProvider:
    """Get the active notebook adapter."""
    if not _initialized or _notebook_adapter is None:
        raise RuntimeError("Adapter registry not initialized. Call initialize_adapters() first.")
    return _notebook_adapter


def get_cellxgene_adapter() -> CellxgeneProvider:
    """Get the active cellxgene adapter."""
    if not _initialized or _cellxgene_adapter is None:
        raise RuntimeError("Adapter registry not initialized. Call initialize_adapters() first.")
    return _cellxgene_adapter


def get_work_node_adapter() -> WorkNodeProvider:
    """Get the active work node adapter (GCE VMs)."""
    if not _initialized or _work_node_adapter is None:
        raise RuntimeError("Adapter registry not initialized. Call initialize_adapters() first.")
    return _work_node_adapter


def reset_registry() -> None:
    """Reset the registry (for testing)."""
    global _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter, _work_node_adapter, _initialized
    _compute_adapter = None
    _storage_adapter = None
    _notebook_adapter = None
    _cellxgene_adapter = None
    _work_node_adapter = None
    _initialized = False
