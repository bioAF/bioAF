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
# (reserved here, wired to their Phase 9 providers later).
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
