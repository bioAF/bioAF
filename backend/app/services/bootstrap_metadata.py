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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.bootstrap_metadata")

_METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/attributes/bioaf_bootstrap_sa_email"
_ATTACHED_SA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
_METADATA_TIMEOUT_SECONDS = 2.0


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
    existing = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='gcp_bootstrap_sa_email'"))
    ).scalar()
    if existing:
        return True

    email = await asyncio.to_thread(_read_metadata_attribute)
    if not email:
        return False

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('gcp_bootstrap_sa_email', :v) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(v=email)
    )
    await session.commit()
    logger.info("Persisted gcp_bootstrap_sa_email from VM metadata: %s", email)
    return True
