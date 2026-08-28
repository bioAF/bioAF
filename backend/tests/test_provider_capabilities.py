"""ProviderCapabilities contract and the CapabilityNotSupported exception.

BAL rework, Phase 2 (keystone). Each adapter declares what its backend can
actually do; the registry aggregates the active adapters into one capability
surface the API/UI can read (Phase 4b). Logic and UI key off these flags, never
off a backend name or a null guess.
"""

from __future__ import annotations

import pytest

from app.adapters.capabilities import CapabilityNotSupported, ProviderCapabilities

# The agreed starting flag set: the specced 10 plus messaging and billing
# (reserved here, wired to their Phase 9 providers later), plus quota_introspection.
#
# quota_introspection was added 2026-08-26 and reviewed, which is what this pin
# exists to force. It says whether a backend can report the cloud quota its node
# pools draw on. It is a capability rather than an inference because "this backend
# cannot answer" and "this backend is misconfigured" must stay distinguishable: the
# Components page preflight fails OPEN, so a GCP install missing compute.regions.get
# degrades to "unverified" exactly like SLURM does, and only the flag says which of
# them could ever have answered.
ALL_FLAGS = (
    "cost_estimation",
    "autoscaling",
    "ssh_exec",
    "signed_url_upload",
    "notebooks",
    "cellxgene",
    "work_nodes",
    "spot_retry",
    "job_report",
    "storage_tier_metrics",
    "messaging",
    "billing",
    "quota_introspection",
)


def test_all_flags_default_false():
    caps = ProviderCapabilities()
    for flag in ALL_FLAGS:
        assert getattr(caps, flag) is False, flag


def test_model_has_exactly_the_agreed_flags():
    """Pin the flag set so adding/removing one is deliberate and reviewed."""
    assert set(ProviderCapabilities.model_fields) == set(ALL_FLAGS)


def test_flags_can_be_set():
    caps = ProviderCapabilities(cost_estimation=True, notebooks=True)
    assert caps.cost_estimation is True
    assert caps.notebooks is True
    assert caps.autoscaling is False


def test_merge_is_logical_or_across_flags():
    a = ProviderCapabilities(cost_estimation=True, job_report=True)
    b = ProviderCapabilities(notebooks=True, job_report=True)
    merged = a.merge(b)
    assert merged.cost_estimation is True
    assert merged.notebooks is True
    assert merged.job_report is True
    assert merged.billing is False


def test_merge_does_not_mutate_operands():
    a = ProviderCapabilities(cost_estimation=True)
    b = ProviderCapabilities(notebooks=True)
    a.merge(b)
    assert a.notebooks is False
    assert b.cost_estimation is False


def test_capability_not_supported_carries_capability_name():
    exc = CapabilityNotSupported("signed_url_upload")
    assert exc.capability == "signed_url_upload"
    assert "signed_url_upload" in str(exc)


def test_capability_not_supported_is_raisable():
    with pytest.raises(CapabilityNotSupported) as info:
        raise CapabilityNotSupported("autoscaling")
    assert info.value.capability == "autoscaling"


# --- Per-adapter declarations -----------------------------------------------


def _flags_true(caps: ProviderCapabilities) -> set[str]:
    return {f for f in ALL_FLAGS if getattr(caps, f)}


def test_base_provider_capabilities_default_to_nothing():
    """A provider that does not override capabilities() declares nothing.

    This is what the SLURM/NFS stubs rely on: until they implement a feature,
    they honestly claim it is unsupported.
    """
    from app.adapters.compute.slurm import SlurmComputeProvider
    from app.adapters.notebooks.slurm import SlurmNotebookProvider
    from app.adapters.storage.nfs import NfsStorageProvider

    for stub in (SlurmComputeProvider(), SlurmNotebookProvider(), NfsStorageProvider()):
        assert _flags_true(stub.capabilities()) == set()


def test_kubernetes_compute_declares_its_capabilities():
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    caps = KubernetesComputeProvider().capabilities()
    assert _flags_true(caps) == {
        "cost_estimation",
        "autoscaling",
        "ssh_exec",
        "spot_retry",
        "job_report",
        # GKE can be asked what its region's quotas are, which is what lets the
        # Components page refuse a node pool that could not build a single node.
        "quota_introspection",
    }


def test_gcs_storage_declares_its_capabilities():
    from app.adapters.storage.gcs import GcsStorageProvider

    caps = GcsStorageProvider().capabilities()
    assert _flags_true(caps) == {"signed_url_upload", "storage_tier_metrics"}


def test_kubernetes_notebook_declares_notebooks():
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    assert _flags_true(KubernetesNotebookProvider().capabilities()) == {"notebooks"}


def test_kubernetes_cellxgene_declares_cellxgene():
    from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider

    assert _flags_true(KubernetesCellxgeneProvider().capabilities()) == {"cellxgene"}


def test_gce_work_node_declares_work_nodes():
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    assert _flags_true(GCEWorkNodeProvider().capabilities()) == {"work_nodes"}


# --- Registry aggregation ----------------------------------------------------


@pytest.fixture
def _reset_registry():
    from app.adapters import registry

    registry.reset_registry()
    yield
    registry.reset_registry()


def test_registry_aggregates_active_capabilities(_reset_registry):
    from app.adapters import registry

    registry.initialize_adapters_sync("kubernetes")
    caps = registry.get_active_capabilities()
    assert _flags_true(caps) == {
        "cost_estimation",
        "autoscaling",
        "ssh_exec",
        "spot_retry",
        "job_report",
        "signed_url_upload",
        "storage_tier_metrics",
        "notebooks",
        "cellxgene",
        "work_nodes",
        "quota_introspection",
    }
    assert caps.messaging is False
    assert caps.billing is False


def test_registry_has_no_hasattr_probe():
    """The registry must resolve optional adapter methods via the typed contract
    (a base no-op load_cluster_config), not by sniffing with hasattr."""
    from pathlib import Path

    registry_src = (Path(__file__).resolve().parent.parent / "app" / "adapters" / "registry.py").read_text()
    assert "hasattr(" not in registry_src


def test_load_cluster_config_is_a_noop_on_stubs(_reset_registry):
    """Stub adapters inherit the base no-op so the registry can call it
    unconditionally. Running full initialization must not raise."""
    from app.adapters import registry

    registry.initialize_adapters_sync("slurm")
    # SLURM/NFS stubs have no load_cluster_config override; the base no-op
    # means get_active_capabilities and method dispatch still work.
    assert registry.get_compute_adapter() is not None
