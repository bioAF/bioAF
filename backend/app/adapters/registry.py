"""Adapter registry - resolves active adapters from platform_config.

Singleton initialized on application startup. Reads compute_stack from
the database and instantiates the correct adapter implementations.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CellxgeneProvider, ComputeProvider, NotebookProvider, StorageProvider, WorkNodeProvider
from app.adapters.capabilities import CapabilityNotSupported, ProviderCapabilities

logger = logging.getLogger("bioaf.adapters.registry")

VALID_COMPUTE_STACKS = ("kubernetes", "slurm")
# Work-node and cellxgene backends are independent of compute_stack (a SLURM
# compute stack can still run GCE work nodes / K8s cellxgene). Only one
# implementation of each exists today; the config seam lets EC2 / others slot in
# later. Defaults preserve today's choice when the config key is absent.
VALID_WORK_NODE_BACKENDS = ("gce",)
VALID_CELLXGENE_BACKENDS = ("kubernetes",)
DEFAULT_WORK_NODE_BACKEND = "gce"
DEFAULT_CELLXGENE_BACKEND = "kubernetes"

# Serializes initialize_adapters so a concurrent or repeated startup call cannot
# interleave the async read-then-assign of the singleton state.
_init_lock = asyncio.Lock()

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
) -> tuple[ComputeProvider, StorageProvider, NotebookProvider]:
    """Instantiate the compute/storage/notebook adapters for ``compute_stack``.

    Work-node and cellxgene backends are resolved separately (they are not tied
    to compute_stack); see ``_create_work_node_adapter`` / ``_create_cellxgene_adapter``.
    """
    if compute_stack not in VALID_COMPUTE_STACKS:
        raise ValueError(f"Unknown compute_stack '{compute_stack}'. Valid options: {VALID_COMPUTE_STACKS}")

    if compute_stack == "kubernetes":
        from app.adapters.compute.kubernetes import KubernetesComputeProvider
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
        from app.adapters.storage.gcs import GcsStorageProvider

        return (
            KubernetesComputeProvider(session_factory=session_factory),
            GcsStorageProvider(),
            KubernetesNotebookProvider(session_factory=session_factory),
        )
    else:
        from app.adapters.compute.slurm import SlurmComputeProvider
        from app.adapters.notebooks.slurm import SlurmNotebookProvider
        from app.adapters.storage.nfs import NfsStorageProvider

        return (
            SlurmComputeProvider(),
            NfsStorageProvider(),
            SlurmNotebookProvider(),
        )


def _create_work_node_adapter(backend: str, session_factory=None) -> WorkNodeProvider:
    """Instantiate the work-node adapter for ``backend`` (default GCE)."""
    if backend not in VALID_WORK_NODE_BACKENDS:
        raise ValueError(f"Unknown work_node_backend '{backend}'. Valid options: {VALID_WORK_NODE_BACKENDS}")
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    return GCEWorkNodeProvider(session_factory=session_factory)


def _create_cellxgene_adapter(backend: str, session_factory=None) -> CellxgeneProvider:
    """Instantiate the cellxgene adapter for ``backend`` (default Kubernetes)."""
    if backend not in VALID_CELLXGENE_BACKENDS:
        raise ValueError(f"Unknown cellxgene_backend '{backend}'. Valid options: {VALID_CELLXGENE_BACKENDS}")
    from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider

    return KubernetesCellxgeneProvider(session_factory=session_factory)


async def initialize_adapters(session: AsyncSession, session_factory=None) -> None:
    """Read the backend selections from platform_config and initialize adapters.

    Guarded by ``_init_lock`` so a concurrent or repeated call cannot interleave
    the read-then-assign of the singleton state.
    """
    global _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter, _work_node_adapter, _initialized

    async with _init_lock:
        result = await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('compute_stack', 'work_node_backend', 'cellxgene_backend')"
            )
        )
        cfg = {row[0]: row[1] for row in result.fetchall()}
        compute_stack = cfg.get("compute_stack") or "kubernetes"
        work_node_backend = cfg.get("work_node_backend") or DEFAULT_WORK_NODE_BACKEND
        cellxgene_backend = cfg.get("cellxgene_backend") or DEFAULT_CELLXGENE_BACKEND

        logger.info(
            "Initializing BAL adapters (compute_stack=%s work_node_backend=%s cellxgene_backend=%s)",
            compute_stack,
            work_node_backend,
            cellxgene_backend,
        )
        _compute_adapter, _storage_adapter, _notebook_adapter = _create_adapters(
            compute_stack, session_factory=session_factory
        )
        _cellxgene_adapter = _create_cellxgene_adapter(cellxgene_backend, session_factory=session_factory)
        _work_node_adapter = _create_work_node_adapter(work_node_backend, session_factory=session_factory)

        # Eagerly load cluster config so adapters never need to run async DB
        # queries from a sync context (which breaks asyncpg). load_cluster_config
        # is a base no-op, so this calls it unconditionally rather than sniffing
        # for the method with hasattr.
        await _compute_adapter.load_cluster_config()
        await _notebook_adapter.load_cluster_config()
        await _cellxgene_adapter.load_cluster_config()
        await _work_node_adapter.load_gcp_config()

        _initialized = True


def initialize_adapters_sync(
    compute_stack: str,
    work_node_backend: str = DEFAULT_WORK_NODE_BACKEND,
    cellxgene_backend: str = DEFAULT_CELLXGENE_BACKEND,
) -> None:
    """Initialize adapters synchronously from known values (for testing)."""
    global _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter, _work_node_adapter, _initialized

    _compute_adapter, _storage_adapter, _notebook_adapter = _create_adapters(compute_stack)
    _cellxgene_adapter = _create_cellxgene_adapter(cellxgene_backend)
    _work_node_adapter = _create_work_node_adapter(work_node_backend)
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


def get_active_capabilities() -> ProviderCapabilities:
    """Aggregate the active stack's capabilities across all five adapters.

    Each adapter declares the flags its backend truly supports; the registry
    merges them (logical OR) into one capability surface that services and the
    frontend (Phase 4b) read to decide what actions to expose.
    """
    if not _initialized:
        raise RuntimeError("Adapter registry not initialized. Call initialize_adapters() first.")
    caps = ProviderCapabilities()
    for adapter in (
        _compute_adapter,
        _storage_adapter,
        _notebook_adapter,
        _cellxgene_adapter,
        _work_node_adapter,
    ):
        if adapter is not None:
            caps = caps.merge(adapter.capabilities())
    return caps


def require_capability(flag: str) -> None:
    """Raise CapabilityNotSupported if the active backend lacks ``flag``.

    Server-side enforcement counterpart to the frontend useCapabilities() gating
    (Phase 4b): a direct API caller that requests a capability-dependent action
    the active backend cannot perform gets a clean 4xx (via the registered
    exception handler) instead of a 500 from deeper in the stack. Looks the flag
    up on the merged active capability surface.
    """
    caps = get_active_capabilities()
    if not getattr(caps, flag, False):
        raise CapabilityNotSupported(flag)


def reset_registry() -> None:
    """Reset the registry (for testing)."""
    global _compute_adapter, _storage_adapter, _notebook_adapter, _cellxgene_adapter, _work_node_adapter, _initialized
    _compute_adapter = None
    _storage_adapter = None
    _notebook_adapter = None
    _cellxgene_adapter = None
    _work_node_adapter = None
    _initialized = False
