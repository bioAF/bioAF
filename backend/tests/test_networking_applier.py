"""Unit tests for the VmNginxApplier (the applier that backs Settings -> Networking on VM installs)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services.networking_applier import (
    CERT_STATUS_ACTIVE,
    CERT_STATUS_NOT_REQUESTED,
    ManualActionRequired,
    MockNetworkingApplier,
    VmNginxApplier,
    get_networking_applier,
)


def _write_cert(
    path: Path,
    common_name: str,
    sans: list[str],
    days_valid: int = 90,
    expired: bool = False,
) -> None:
    """Write an X.509 cert to *path* with the given CN and SubjectAltNames."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    if expired:
        not_before = now - timedelta(days=days_valid + 30)
        not_after = now - timedelta(days=1)
    else:
        not_before = now - timedelta(minutes=1)
        not_after = now + timedelta(days=days_valid)
    san = x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]) if sans else None
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )
    if san is not None:
        builder = builder.add_extension(san, critical=False)
    cert = builder.sign(private_key=key, algorithm=hashes.SHA256())
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


@pytest.mark.asyncio
async def test_vm_applier_get_status_active_when_cert_covers_fqdn(tmp_path):
    """get_certificate_status returns 'active' when the on-disk cert covers the FQDN."""
    cert = tmp_path / "tls.crt"
    _write_cert(cert, common_name="bioaf-demo.bioaf.co", sans=["bioaf-demo.bioaf.co"])

    applier = VmNginxApplier(cert_path=cert)
    assert await applier.get_certificate_status("bioaf-demo.bioaf.co") == CERT_STATUS_ACTIVE


@pytest.mark.asyncio
async def test_vm_applier_get_status_active_from_san_match(tmp_path):
    """SAN matching, not just CN, satisfies 'active'."""
    cert = tmp_path / "tls.crt"
    _write_cert(cert, common_name="bioaf-local", sans=["bioaf-demo.bioaf.co", "www.bioaf-demo.bioaf.co"])

    applier = VmNginxApplier(cert_path=cert)
    assert await applier.get_certificate_status("bioaf-demo.bioaf.co") == CERT_STATUS_ACTIVE


@pytest.mark.asyncio
async def test_vm_applier_get_status_not_requested_for_self_signed(tmp_path):
    """A self-signed install cert (CN=bioaf-local) does NOT count as active for a real FQDN."""
    cert = tmp_path / "tls.crt"
    _write_cert(cert, common_name="bioaf-local", sans=["localhost"])

    applier = VmNginxApplier(cert_path=cert)
    assert await applier.get_certificate_status("bioaf-demo.bioaf.co") == CERT_STATUS_NOT_REQUESTED


@pytest.mark.asyncio
async def test_vm_applier_get_status_not_requested_when_file_missing(tmp_path):
    cert = tmp_path / "does-not-exist.crt"
    applier = VmNginxApplier(cert_path=cert)
    assert await applier.get_certificate_status("bioaf-demo.bioaf.co") == CERT_STATUS_NOT_REQUESTED


@pytest.mark.asyncio
async def test_vm_applier_get_status_not_requested_when_file_unreadable(tmp_path):
    cert = tmp_path / "tls.crt"
    cert.write_bytes(b"not a real certificate")
    applier = VmNginxApplier(cert_path=cert)
    assert await applier.get_certificate_status("bioaf-demo.bioaf.co") == CERT_STATUS_NOT_REQUESTED


@pytest.mark.asyncio
async def test_vm_applier_get_status_expired_returns_not_requested(tmp_path):
    """An expired cert for the FQDN is treated as not active (operator must reissue)."""
    cert = tmp_path / "tls.crt"
    _write_cert(cert, common_name="bioaf-demo.bioaf.co", sans=["bioaf-demo.bioaf.co"], expired=True)

    applier = VmNginxApplier(cert_path=cert)
    assert await applier.get_certificate_status("bioaf-demo.bioaf.co") == CERT_STATUS_NOT_REQUESTED


@pytest.mark.asyncio
async def test_vm_applier_request_certificate_raises_manual_action():
    """On VM installs the backend cannot issue a cert itself; operator runs certbot."""
    applier = VmNginxApplier()
    with pytest.raises(ManualActionRequired) as exc_info:
        await applier.request_certificate("bioaf-demo.bioaf.co")
    # The error must carry an operator-actionable instruction string.
    detail = str(exc_info.value)
    assert "certbot" in detail.lower() or "let's encrypt" in detail.lower()
    assert "bioaf-demo.bioaf.co" in detail


@pytest.mark.asyncio
async def test_vm_applier_enforce_https_is_a_documented_noop():
    """HTTPS is already enforced by the install nginx config; the applier records intent but does nothing."""
    applier = VmNginxApplier()
    # Must not raise; must not require any external resources.
    await applier.enforce_https("bioaf-demo.bioaf.co", enabled=True)
    await applier.enforce_https("bioaf-demo.bioaf.co", enabled=False)


@pytest.mark.asyncio
async def test_vm_applier_restart_services_is_a_documented_noop():
    """No automated restart from the backend container on VM installs."""
    applier = VmNginxApplier()
    await applier.restart_services()


@pytest.mark.asyncio
async def test_mock_applier_get_https_enforced_defaults_false_overridable():
    """MockNetworkingApplier reports https_enforced as configurable; tests can drive it."""
    applier = MockNetworkingApplier()
    assert await applier.get_https_enforced() is False
    applier.https_enforced_value = True
    assert await applier.get_https_enforced() is True


@pytest.mark.asyncio
async def test_vm_applier_get_https_enforced_returns_true():
    """On VM installs, nginx.conf unconditionally redirects HTTP to HTTPS,
    so the applier always reports HTTPS as enforced. Operators do not toggle
    this; it is a property of the install topology."""
    applier = VmNginxApplier()
    assert await applier.get_https_enforced() is True


def test_default_factory_returns_vm_applier():
    """get_networking_applier() must return a VmNginxApplier in any non-test mode."""
    import os

    # Default (unset BIOAF_COMPUTE_MODE) and 'kubernetes' both used to pick a K8s
    # applier; both must now resolve to the VM applier.
    old = os.environ.get("BIOAF_COMPUTE_MODE")
    try:
        os.environ.pop("BIOAF_COMPUTE_MODE", None)
        assert isinstance(get_networking_applier(), VmNginxApplier)
        os.environ["BIOAF_COMPUTE_MODE"] = "kubernetes"
        assert isinstance(get_networking_applier(), VmNginxApplier)
    finally:
        if old is None:
            os.environ.pop("BIOAF_COMPUTE_MODE", None)
        else:
            os.environ["BIOAF_COMPUTE_MODE"] = old
