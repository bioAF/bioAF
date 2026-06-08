"""Phase 6: the registry selects work-node and cellxgene backends from config.

Previously the GCE work-node and Kubernetes cellxgene backends were hardcoded in
initialize_adapters regardless of the selected stack. They are now resolved from
platform_config (work_node_backend / cellxgene_backend) and default to today's
GCE / Kubernetes choice when the keys are absent, so existing installs behave
identically. initialize_adapters is also guarded by an asyncio.Lock so concurrent
or repeated startup calls cannot interleave the read-then-assign.
"""

import asyncio

import pytest

from app.adapters import registry
from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider
from app.adapters.registry import (
    _create_cellxgene_adapter,
    _create_work_node_adapter,
    get_cellxgene_adapter,
    get_work_node_adapter,
    initialize_adapters,
    reset_registry,
)
from app.adapters.work_nodes.gce import GCEWorkNodeProvider


def test_work_node_backend_factory_resolves_gce():
    assert isinstance(_create_work_node_adapter("gce"), GCEWorkNodeProvider)


def test_cellxgene_backend_factory_resolves_kubernetes():
    assert isinstance(_create_cellxgene_adapter("kubernetes"), KubernetesCellxgeneProvider)


def test_unknown_work_node_backend_raises():
    with pytest.raises(ValueError):
        _create_work_node_adapter("ec2")


def test_unknown_cellxgene_backend_raises():
    with pytest.raises(ValueError):
        _create_cellxgene_adapter("posit")


@pytest.mark.asyncio
async def test_registry_worknode_backend_defaults_to_gce(session):
    """Absent work_node_backend config -> today's GCE choice."""
    reset_registry()
    await initialize_adapters(session)
    assert isinstance(get_work_node_adapter(), GCEWorkNodeProvider)


@pytest.mark.asyncio
async def test_registry_cellxgene_backend_defaults_to_kubernetes(session):
    """Absent cellxgene_backend config -> today's Kubernetes choice."""
    reset_registry()
    await initialize_adapters(session)
    assert isinstance(get_cellxgene_adapter(), KubernetesCellxgeneProvider)


@pytest.mark.asyncio
async def test_registry_initialize_is_concurrency_safe(session):
    """Concurrent initialize_adapters calls serialize via the lock.

    The two calls share one AsyncSession; asyncpg forbids concurrent operations
    on it, so without the lock the gather would raise. The lock serializes the
    read-then-assign so both complete and the registry ends consistent.
    """
    reset_registry()
    assert isinstance(registry._init_lock, asyncio.Lock)
    await asyncio.gather(initialize_adapters(session), initialize_adapters(session))
    assert isinstance(get_work_node_adapter(), GCEWorkNodeProvider)
    assert isinstance(get_cellxgene_adapter(), KubernetesCellxgeneProvider)
