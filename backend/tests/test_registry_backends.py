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

from app.exceptions import ValidationError

from app.adapters import registry
from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider
from app.adapters.registry import (
    _create_cellxgene_adapter,
    _create_storage_adapter,
    _create_work_node_adapter,
    _resolve_storage_backend,
    get_cellxgene_adapter,
    get_work_node_adapter,
    initialize_adapters,
    reset_registry,
)
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.adapters.work_nodes.gce import GCEWorkNodeProvider
from app.platform import cloud_provider as cp
from app.platform.platform_config_service import PlatformConfigService


def test_work_node_backend_factory_resolves_gce():
    assert isinstance(_create_work_node_adapter("gce"), GCEWorkNodeProvider)


def test_cellxgene_backend_factory_resolves_kubernetes():
    assert isinstance(_create_cellxgene_adapter("kubernetes"), KubernetesCellxgeneProvider)


def test_unknown_work_node_backend_raises():
    with pytest.raises(ValidationError):
        _create_work_node_adapter("ec2")


def test_unknown_cellxgene_backend_raises():
    with pytest.raises(ValidationError):
        _create_cellxgene_adapter("posit")


# --- storage decoupled from compute_stack (Stage 2a) -------------------------


def test_storage_backend_factory_resolves_gcs():
    assert isinstance(_create_storage_adapter("gcs"), GcsStorageProvider)


def test_storage_backend_factory_resolves_nfs():
    assert isinstance(_create_storage_adapter("nfs"), NfsStorageProvider)


def test_storage_backend_factory_resolves_s3():
    # s3 (POLICY aws -> s3) is the S3StorageProvider, implemented in Stage 6a.
    assert isinstance(_create_storage_adapter("s3"), S3StorageProvider)


def test_unknown_storage_backend_raises():
    with pytest.raises(ValidationError):
        _create_storage_adapter("azure")


def test_resolve_storage_backend_slurm_is_nfs():
    # SLURM stages on NFS regardless of cloud; cloud_provider does not change it.
    assert _resolve_storage_backend("slurm") == "nfs"


def test_resolve_storage_backend_kubernetes_follows_cloud_provider_gcp():
    cp.reset_resolved_backends()  # unloaded cache -> gcp policy default (gcs)
    assert _resolve_storage_backend("kubernetes") == "gcs"


@pytest.mark.asyncio
async def test_resolve_storage_backend_kubernetes_aws_is_s3(monkeypatch):
    async def fake_get_many(session, keys):
        return {"cloud_provider": "aws"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    await cp.load_resolved_backends(object())
    assert _resolve_storage_backend("kubernetes") == "s3"
    cp.reset_resolved_backends()


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
