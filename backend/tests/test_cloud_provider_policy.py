"""Unit tests for the cloud_provider backend-resolution policy (Stage 1a).

The policy module is the keystone of the multi-platform design: one authoritative
``cloud_provider`` identity drives which backend every BAL seam resolves to. These
are pure unit tests (no DB, no infra) for the resolution logic and the fail-closed
combo validation, plus thin async-wrapper tests that monkeypatch the config service.
"""

from __future__ import annotations

import pytest

from app.platform import cloud_provider as cp
from app.platform.platform_config_service import PlatformConfigService

# --- Structure / policy contents --------------------------------------------


def test_default_cloud_provider_is_gcp():
    """Existing installs (and local dev) have no cloud_provider key and must
    keep behaving as GCP, so the unset default is gcp."""
    assert cp.DEFAULT_CLOUD_PROVIDER == "gcp"


def test_both_clouds_define_the_same_seams():
    assert set(cp.POLICY["gcp"]) == set(cp.POLICY["aws"]) == set(cp.SEAMS)


def test_gcp_policy_matches_current_hard_defaults():
    """Behavior-preservation contract: the gcp row must equal each seam's
    current hard-default backend, so resolving from cloud_provider on a GCP
    install returns exactly what the factories pick today."""
    assert cp.POLICY["gcp"] == {
        "storage": "gcs",
        "work_node": "gce",
        "iam": "gcp",
        "billing": "gcp",
        "messaging": "gcp",
        "secrets": "gcp",
        "log_sink": "gcp",
        "credentials": "gcp",
    }


def test_aws_policy_values():
    assert cp.POLICY["aws"] == {
        "storage": "s3",
        "work_node": "ec2",
        "iam": "aws",
        "billing": "aws",
        "messaging": "aws",
        "secrets": "aws",
        "log_sink": "aws",
        "credentials": "aws",
    }


# --- resolve(): defaults --------------------------------------------------------


@pytest.mark.parametrize(
    "seam", sorted(["storage", "work_node", "iam", "billing", "messaging", "secrets", "log_sink", "credentials"])
)
def test_resolve_returns_policy_default_when_no_override(seam):
    assert cp.resolve("gcp", seam) == cp.POLICY["gcp"][seam]
    assert cp.resolve("aws", seam) == cp.POLICY["aws"][seam]


# --- resolve(): overrides + fail-closed validation ------------------------------


def test_resolve_valid_override_wins():
    # An explicit override equal to the cloud default is honored.
    assert cp.resolve("gcp", "secrets", "gcp") == "gcp"
    # A cloud-agnostic backend (NFS storage, used by SLURM on any cloud) is valid.
    assert cp.resolve("gcp", "storage", "nfs") == "nfs"
    assert cp.resolve("aws", "storage", "nfs") == "nfs"


def test_resolve_empty_override_is_treated_as_unset():
    assert cp.resolve("gcp", "secrets", "") == "gcp"
    assert cp.resolve("gcp", "secrets", None) == "gcp"


def test_resolve_invalid_override_fails_closed():
    # storage_backend=s3 on a gcp install: the canonical fail-closed example.
    with pytest.raises(cp.InvalidBackendError):
        cp.resolve("gcp", "storage", "s3")
    # A substrate seam pinned to the wrong cloud's backend.
    with pytest.raises(cp.InvalidBackendError):
        cp.resolve("gcp", "secrets", "aws")
    with pytest.raises(cp.InvalidBackendError):
        cp.resolve("aws", "storage", "gcs")


def test_resolve_unknown_cloud_raises():
    with pytest.raises(cp.InvalidBackendError):
        cp.resolve("azure", "secrets")


def test_resolve_unknown_seam_raises():
    with pytest.raises(cp.InvalidBackendError):
        cp.resolve("gcp", "not_a_seam")


# --- valid_backends / is_valid_combo --------------------------------------------


def test_valid_backends_includes_default_and_agnostic():
    assert cp.valid_backends("gcp", "storage") == frozenset({"gcs", "nfs"})
    assert cp.valid_backends("aws", "storage") == frozenset({"s3", "nfs"})
    # A substrate seam with no cloud-agnostic option is exactly the cloud default.
    assert cp.valid_backends("gcp", "secrets") == frozenset({"gcp"})


def test_is_valid_combo():
    assert cp.is_valid_combo("gcp", "storage", "gcs") is True
    assert cp.is_valid_combo("gcp", "storage", "nfs") is True
    assert cp.is_valid_combo("gcp", "storage", "s3") is False
    assert cp.is_valid_combo("aws", "secrets", "aws") is True
    assert cp.is_valid_combo("aws", "secrets", "gcp") is False


# --- async wrappers (monkeypatched config service, no DB) -----------------------


@pytest.mark.asyncio
async def test_get_cloud_provider_defaults_to_gcp_when_unset(monkeypatch):
    async def fake_get(session, key):
        return None

    monkeypatch.setattr(PlatformConfigService, "get", fake_get)
    assert await cp.get_cloud_provider(object()) == "gcp"


@pytest.mark.asyncio
async def test_get_cloud_provider_reads_explicit_value(monkeypatch):
    async def fake_get(session, key):
        return "aws"

    monkeypatch.setattr(PlatformConfigService, "get", fake_get)
    assert await cp.get_cloud_provider(object()) == "aws"


@pytest.mark.asyncio
async def test_resolve_backend_unset_cloud_uses_gcp_policy(monkeypatch):
    async def fake_get_many(session, keys):
        return {}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    assert await cp.resolve_backend(object(), "secrets") == "gcp"
    assert await cp.resolve_backend(object(), "storage") == "gcs"


@pytest.mark.asyncio
async def test_resolve_backend_threads_per_seam_override(monkeypatch):
    async def fake_get_many(session, keys):
        # gcp install, storage overridden to the cloud-agnostic NFS backend.
        return {"storage_backend": "nfs"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    assert await cp.resolve_backend(object(), "storage") == "nfs"


@pytest.mark.asyncio
async def test_resolve_backend_resolves_aws_install(monkeypatch):
    async def fake_get_many(session, keys):
        return {"cloud_provider": "aws"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    assert await cp.resolve_backend(object(), "secrets") == "aws"
    assert await cp.resolve_backend(object(), "storage") == "s3"


@pytest.mark.asyncio
async def test_resolve_backend_invalid_override_raises(monkeypatch):
    async def fake_get_many(session, keys):
        return {"cloud_provider": "gcp", "storage_backend": "s3"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    with pytest.raises(cp.InvalidBackendError):
        await cp.resolve_backend(object(), "storage")


# --- resolved-backend cache (Stage 1c): backend_for / load_resolved_backends ----
#
# The registry loads this cache once at startup so the sessionless factory call
# sites (secrets fetched pre-DB; billing/iam/messaging created on-demand) read
# their backend synchronously. Unloaded, backend_for falls back to the gcp policy
# default, preserving current behavior.


def test_backend_for_defaults_to_gcp_policy_when_cache_empty():
    cp.reset_resolved_backends()
    for seam in cp.SEAMS:
        assert cp.backend_for(seam) == cp.POLICY["gcp"][seam]


def test_backend_for_unknown_seam_raises():
    with pytest.raises(cp.InvalidBackendError):
        cp.backend_for("not_a_seam")


@pytest.mark.asyncio
async def test_load_resolved_backends_caches_gcp(monkeypatch):
    async def fake_get_many(session, keys):
        return {}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    await cp.load_resolved_backends(object())
    assert cp.backend_for("secrets") == "gcp"
    assert cp.backend_for("storage") == "gcs"
    cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_load_resolved_backends_caches_aws(monkeypatch):
    async def fake_get_many(session, keys):
        return {"cloud_provider": "aws"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    await cp.load_resolved_backends(object())
    assert cp.backend_for("secrets") == "aws"
    assert cp.backend_for("storage") == "s3"
    cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_load_resolved_backends_honors_valid_override(monkeypatch):
    async def fake_get_many(session, keys):
        return {"storage_backend": "nfs"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    await cp.load_resolved_backends(object())
    assert cp.backend_for("storage") == "nfs"
    cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_load_resolved_backends_fails_closed_on_invalid_override(monkeypatch):
    async def fake_get_many(session, keys):
        return {"cloud_provider": "gcp", "storage_backend": "s3"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    with pytest.raises(cp.InvalidBackendError):
        await cp.load_resolved_backends(object())
    cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_reset_resolved_backends_restores_defaults(monkeypatch):
    async def fake_get_many(session, keys):
        return {"cloud_provider": "aws"}

    monkeypatch.setattr(PlatformConfigService, "get_many", fake_get_many)
    await cp.load_resolved_backends(object())
    assert cp.backend_for("secrets") == "aws"
    cp.reset_resolved_backends()
    assert cp.backend_for("secrets") == "gcp"
