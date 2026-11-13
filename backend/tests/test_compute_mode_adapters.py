"""Every mode-aware adapter takes its mode from settings, or from a keyword-only typed override.

The mode is fixed when the adapter is built: changing the environment afterwards changes nothing, and
no constructor reads BIOAF_COMPUTE_MODE or falls back to a default of its own.
"""

from __future__ import annotations

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.adapters.work_nodes.ec2 import Ec2VmInstance, Ec2WorkNodeProvider
from app.adapters.work_nodes.gce import GCEWorkNodeProvider, GceVmInstance
from app.config import settings
from app.settings_contract import ComputeMode
from tests.support.mode_adapters import build_mode_adapter

MODE_AWARE = [
    KubernetesComputeProvider,
    KubernetesNotebookProvider,
    GcsStorageProvider,
    S3StorageProvider,
    GceVmInstance,
    GCEWorkNodeProvider,
    Ec2VmInstance,
    Ec2WorkNodeProvider,
]


@pytest.mark.parametrize("provider_cls", MODE_AWARE)
def test_without_an_override_the_adapter_follows_resolved_settings(provider_cls, monkeypatch):
    assert provider_cls().is_local is True  # conftest pins local
    monkeypatch.setattr(settings, "compute_mode", ComputeMode.KUBERNETES)
    assert provider_cls().is_local is False


@pytest.mark.parametrize("provider_cls", MODE_AWARE)
@pytest.mark.parametrize(
    ("mode", "local"), [(ComputeMode.LOCAL, True), (ComputeMode.KUBERNETES, False), (ComputeMode.SLURM, False)]
)
def test_the_keyword_override_selects_the_branch(provider_cls, mode, local):
    assert provider_cls(mode=mode).is_local is local


@pytest.mark.parametrize("provider_cls", MODE_AWARE)
def test_the_environment_is_not_reread_by_constructors_or_after_construction(provider_cls, monkeypatch):
    adapter = provider_cls()
    for raw in ("kubernetes", "k8s", "typo", ""):
        monkeypatch.setenv("BIOAF_COMPUTE_MODE", raw)
        assert provider_cls().is_local is True
        assert adapter.is_local is True
    real = provider_cls(mode=ComputeMode.KUBERNETES)
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    assert real.is_local is False


@pytest.mark.parametrize("provider_cls", MODE_AWARE)
def test_the_override_is_keyword_only_and_typed(provider_cls):
    with pytest.raises(ValueError):
        provider_cls(mode="production")
    if provider_cls in (GcsStorageProvider, S3StorageProvider):
        # The existing positional input (org slug) is still accepted.
        assert provider_cls("acme", mode=ComputeMode.LOCAL).is_local is True
    else:
        assert provider_cls(None, mode=ComputeMode.LOCAL).is_local is True


def test_local_simulation_runs_without_provider_clients():
    import asyncio

    compute = KubernetesComputeProvider(mode=ComputeMode.LOCAL)
    assert asyncio.run(compute.get_cluster_status()) is not None
    assert compute._api_client is None


@pytest.mark.parametrize("mode", [ComputeMode.LOCAL, ComputeMode.KUBERNETES])
def test_the_factory_builds_the_requested_branch(mode):
    adapter = build_mode_adapter(KubernetesComputeProvider, mode=mode)
    assert adapter.is_local is (mode is ComputeMode.LOCAL)


def test_the_factory_rejects_an_adapter_that_ignores_the_requested_real_mode():
    class StaysLocal:
        def __init__(self, *, mode=None):
            self.is_local = True

    with pytest.raises(AssertionError, match="real branch was not selected"):
        build_mode_adapter(StaysLocal, mode=ComputeMode.KUBERNETES)


def test_the_conftest_fixture_is_the_shared_factory(mode_adapter_factory):
    assert mode_adapter_factory is build_mode_adapter
