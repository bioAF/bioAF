"""Enforce public-access prevention on the GCP foundation buckets during the update (plan 04A).

Bootstrap creates the Terraform state and backup buckets with temporary local state, so no
Terraform plan can change them (and a foundation plan that would recreate them is refused). The
backend applies the one setting itself when it starts on a release that carries this guard:

- only the two names recorded in platform_config at bootstrap, and only ``bioaf-*`` names (the app
  service account's storage grant is conditioned on that prefix);
- only ``public_access_prevention``, patched with the metageneration it read, then read back;
- nothing on AWS, before initialization, or in simulated mode.

Each bucket's outcome is logged; a failure leaves the other bucket's result intact and is retried
on the next start. Enforcement is a pure tightening: bioAF never grants public access, and signed
URLs keep working.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import gcs_public_access
from app.logging_config import get_app_logger
from app.platform.platform_config_service import PlatformConfigService

logger = get_app_logger(__name__)

BUCKET_KEYS = ("terraform_state_bucket", "backups_bucket_name")


def _simulated() -> bool:
    from app.config import settings
    from app.settings_contract import is_simulated

    return is_simulated(settings.compute_mode)


def _storage_client(project_id: str):
    """The single seam tests replace. ADC is the VM's bioaf-app service account."""
    return gcs_public_access.storage_client(project_id)


def _enforce(client, name: str) -> str:
    return gcs_public_access.enforce_public_access_prevention(client, name)


async def reconcile(session: AsyncSession) -> dict[str, str]:
    """Enforce the guard on each recorded foundation bucket; returns each bucket's outcome."""
    from app.platform.cloud_provider import get_cloud_provider

    if _simulated() or await get_cloud_provider(session) != "gcp":
        return {}
    if await PlatformConfigService.get(session, "terraform_initialized") != "true":
        return {}
    project_id = await PlatformConfigService.get(session, "gcp_project_id")
    names = [n for n in [await PlatformConfigService.get(session, k) for k in BUCKET_KEYS] if n]
    if not project_id or not names:
        return {}

    client = None
    results: dict[str, str] = {}
    for name in names:
        if not name.startswith("bioaf-"):
            results[name] = "refused: not a bioaf- bucket"
            logger.warning("Foundation bucket guard refused recorded bucket %s: not a bioaf- bucket", name)
            continue
        try:
            if client is None:
                client = await asyncio.to_thread(_storage_client, project_id)
            results[name] = await asyncio.to_thread(_enforce, client, name)
        except Exception as exc:  # noqa: BLE001 - reported per bucket; the next start retries
            results[name] = f"failed: {exc}"
        if results[name].startswith("failed"):
            logger.warning("Public-access prevention not enforced on foundation bucket %s: %s", name, results[name])
        else:
            logger.info("Foundation bucket %s public-access prevention: %s", name, results[name])
    return results
