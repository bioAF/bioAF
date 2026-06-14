"""Bootstrap-time GCE metadata reads.

The installer attaches the bioaf-bootstrap SA email to the VM's instance
metadata so the backend can persist it to platform_config on first startup
without a separate config-write step. This avoids putting deployment-time
state in code paths the user must run later.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import is_running_on_gce
from app.platform.cloud_provider import CLOUD_PROVIDER_KEY, DEFAULT_CLOUD_PROVIDER, SUPPORTED_CLOUD_PROVIDERS
from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.bootstrap_metadata")

_METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/attributes/bioaf_bootstrap_sa_email"
_ATTACHED_SA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
_METADATA_TIMEOUT_SECONDS = 2.0

# cloud_provider identity discovery (Stage 1b). The installer stamps an explicit
# value into VM metadata; if absent, the host is auto-detected. Both clouds expose
# a metadata service at 169.254.169.254, told apart by protocol: GCE answers the
# Metadata-Flavor: Google header, EC2 answers the IMDSv2 token handshake.
_CLOUD_PROVIDER_ATTR_URL = "http://metadata.google.internal/computeMetadata/v1/instance/attributes/bioaf_cloud_provider"
_IMDS_TOKEN_URL = "http://169.254.169.254/latest/api/token"  # link-local IMDS endpoint
_IMDS_TOKEN_TTL_HEADER = {"X-aws-ec2-metadata-token-ttl-seconds": "21600"}


def _read_metadata_attribute(url: str = _METADATA_URL) -> str | None:
    """Synchronously read a VM-metadata attribute. Returns None when absent."""
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=_METADATA_TIMEOUT_SECONDS) as resp:
            return resp.read().decode().strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


async def get_attached_sa_email() -> str | None:
    """Return the email of the SA attached to this VM, or None when off-GCE."""
    return await asyncio.to_thread(_read_metadata_attribute, _ATTACHED_SA_URL)


async def persist_bootstrap_sa_from_metadata(session: AsyncSession) -> bool:
    """Read bioaf_bootstrap_sa_email from VM metadata and upsert to platform_config.

    Returns True if a value was persisted (or already present), False if the
    metadata server is unreachable or the attribute is unset (e.g. running
    outside of GCE).

    Idempotent: leaves an existing row untouched.
    """
    existing = await PlatformConfigService.get(session, "gcp_bootstrap_sa_email")
    if existing:
        return True

    email = await asyncio.to_thread(_read_metadata_attribute)
    if not email:
        return False

    await PlatformConfigService.set(session, "gcp_bootstrap_sa_email", email)
    await session.commit()
    logger.info("Persisted gcp_bootstrap_sa_email from VM metadata: %s", email)
    return True


async def persist_app_sa_from_metadata(session: AsyncSession) -> bool:
    """Read the VM's attached SA (bioaf-app) and upsert it to platform_config.

    The runtime data-plane SA email is needed by Terraform so it can grant that
    identity read access to project resources the backend queries (e.g. the
    BigQuery billing export dataset; see ADR-028). The installer does not write
    it to platform_config, so the backend resolves it from the metadata server
    on startup.

    Returns True if a value was persisted (or already present), False if the
    metadata server is unreachable or the attribute is unset (e.g. running
    outside of GCE).

    Idempotent: leaves an existing row untouched.
    """
    existing = await PlatformConfigService.get(session, "bioaf_app_sa_email")
    if existing:
        return True

    email = await get_attached_sa_email()
    if not email:
        return False

    await PlatformConfigService.set(session, "bioaf_app_sa_email", email)
    await session.commit()
    logger.info("Persisted bioaf_app_sa_email from VM metadata: %s", email)
    return True


def _read_explicit_cloud_provider() -> str | None:
    """Read the installer-stamped ``bioaf_cloud_provider`` value.

    Today this reads the GCE metadata attribute. The EC2 explicit read (user-data
    / instance tag via IMDSv2) is stage 7c; until then EC2 installs are identified
    by auto-detection instead, so this returning None on EC2 is expected.
    """
    return _read_metadata_attribute(_CLOUD_PROVIDER_ATTR_URL)


def _is_gce() -> bool:
    """True if running on a GCE instance (Metadata-Flavor: Google probe)."""
    return is_running_on_gce()


def _is_ec2_imdsv2() -> bool:
    """True if the host answers the EC2 IMDSv2 token handshake.

    The PUT-token handshake is unique to EC2 (GCE does not implement it), so a
    successful token response reliably identifies an EC2 instance with no risk of
    a GCE false positive.
    """
    req = urllib.request.Request(_IMDS_TOKEN_URL, method="PUT", headers=_IMDS_TOKEN_TTL_HEADER)
    try:
        with urllib.request.urlopen(req, timeout=_METADATA_TIMEOUT_SECONDS) as resp:
            return resp.status == 200 and bool(resp.read().strip())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def _detect_cloud_provider() -> str | None:
    """Auto-detect the host cloud by metadata protocol: ``gcp`` | ``aws`` | None.

    The two probes are mutually exclusive; GCE is checked first. Returns None when
    neither answers (on-prem / local dev / IMDS disabled), in which case the caller
    leaves cloud_provider unset and the policy defaults to gcp.
    """
    if _is_gce():
        return "gcp"
    if _is_ec2_imdsv2():
        return "aws"
    return None


def _reconcile(explicit: str | None, detected: str | None) -> str | None:
    """Combine the explicit (installer) and detected (probe) signals.

    Explicit wins: it states the cloud the install is INTENDED for, while
    detection only reports the metal the process happens to run on. An
    unrecognized explicit value (installer typo) is ignored in favor of detection;
    a disagreement between a valid explicit value and detection is logged but the
    explicit value is trusted.
    """
    if explicit and explicit not in SUPPORTED_CLOUD_PROVIDERS:
        logger.warning(
            "Ignoring unrecognized explicit cloud_provider=%r (supported: %s)",
            explicit,
            SUPPORTED_CLOUD_PROVIDERS,
        )
        explicit = None
    if explicit:
        if detected and detected != explicit:
            logger.warning(
                "cloud_provider mismatch: installer stamped %s but host detected as %s; trusting the explicit value.",
                explicit,
                detected,
            )
        return explicit
    return detected


async def persist_cloud_provider(session: AsyncSession) -> bool:
    """Resolve and persist the install's cloud_provider on first boot.

    Hybrid discovery: an explicit installer-stamped value is authoritative;
    auto-detection (GCE metadata vs EC2 IMDSv2) is the fallback and a consistency
    check. The value is IMMUTABLE: an existing row is never overwritten (moving
    clouds is a new deployment, not a config flip). Returns True if a value is set
    (or already present), False if neither explicit nor detected, in which case it
    is left unset and the policy defaults to gcp downstream.
    """
    existing = await PlatformConfigService.get(session, CLOUD_PROVIDER_KEY)
    if existing:
        return True

    explicit = await asyncio.to_thread(_read_explicit_cloud_provider)
    detected = await asyncio.to_thread(_detect_cloud_provider)
    resolved = _reconcile(explicit, detected)
    if resolved is None:
        logger.info(
            "cloud_provider unset and undetected; leaving unset (defaults to %s downstream).",
            DEFAULT_CLOUD_PROVIDER,
        )
        return False

    await PlatformConfigService.set(session, CLOUD_PROVIDER_KEY, resolved)
    await session.commit()
    logger.info("Persisted cloud_provider=%s (explicit=%s detected=%s).", resolved, explicit, detected)
    return True
